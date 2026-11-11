#!/usr/bin/env python
"""Step 9: publish the model, data and demo Space to Hugging Face, with cards and collections.

    python forge/09_publish.py --user <hf-user>             # private first: review everything
    python forge/09_publish.py --user <hf-user> --public    # then flip to public (un-gated)

Creates
  <user>/lorde-tiny-aya   merged weights, model card, template fingerprints, tag v1.0.0
  <user>/lorde-data       pool, teacher labels, splits, human labels, CuratorKIT manifests and
                          lexsi_provenance.json for every gated set, golden sets, all results
  <user>/lorde-demo       the Gradio Space in space/ (with --space)
  collections "Lorde models" and "Lorde datasets" (the submission form asks for both)

Needs HF_TOKEN with write scope (use it only here, never in serving).

The release tag is immutable: if it already exists and points at another commit than the one just
published, the step fails; publish a new version with ``--tag v1.0.1``. Human labels are published without
labeller names or notes. The repos stay un-gated (the hackathon requires it); the licence (CC-BY-NC-4.0) and
the Cohere Labs Acceptable Use Policy are stated in the card metadata and at the top of every card.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lorde_forge import config, contract, human, jsonl  # noqa: E402

LANG_NAMES = ", ".join(v["name"] for v in config.LANGS.values())
LICENCE_URL = "https://cohere.com/c4ai-cc-by-nc-license"
AUP_URL = "https://docs.cohere.com/docs/c4ai-acceptable-use-policy"
LICENCE_BOX = f"""> **Licence: [CC-BY-NC-4.0]({LICENCE_URL}) and the [Cohere Labs Acceptable Use Policy]({AUP_URL}).**
> Lorde is derived from Tiny Aya Global and trained on Aya Expanse outputs, so both terms apply to it:
> non-commercial use only, and every use must follow the Acceptable Use Policy. The repository is
> un-gated for the Lexsi hackathon; downloading it means accepting these terms."""


def load(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def results_section() -> str:
    parts = []
    for name in ("judge_eval", "plugin_eval", "downstream"):
        p = config.RESULTS / f"{name}.md"
        if p.exists():
            body = p.read_text(encoding="utf-8")
            parts.append("\n".join("#" + line if line.startswith("#") else line for line in body.splitlines()))
    return "\n\n".join(parts) or "_Results are added by forge/06-08._"


def model_card(user: str, tag: str) -> str:
    fp = contract.fingerprints()
    ev = load(config.RESULTS / "judge_eval.json")
    lorde = ev.get("judges", {}).get("lorde", {}).get("vs_teacher", {})
    headline = ""
    if lorde:
        headline = (f"On the held-out test set Lorde agrees with the teacher on **{lorde['decision_agreement'] * 100:.1f}%** "
                    f"of pass/reject decisions (κ = {lorde['kappa']:.2f}) and **{lorde['gate_usable_rate'] * 100:.1f}%** "
                    "of its replies are gate-usable without constrained decoding.\n")
    templates = "\n".join(f"- `{t['id']}` sha256 `{t['sha256']}`" for t in fp["templates"].values())
    return f"""---
license: cc-by-nc-4.0
license_link: {LICENCE_URL}
base_model: {config.STUDENT_MODEL}
library_name: transformers
pipeline_tag: text-generation
language: [{", ".join(config.LANG_CODES)}]
tags: [llm-judge, data-curation, curatorkit, aligntune, auditkit, lexsi-hackathon, tiny-aya, non-commercial]
datasets: [{user}/lorde-data]
---

# Lorde: a 3B judge for trustworthy training data

{LICENCE_BOX}

Lorde is **Tiny Aya Global (3.35B)** fine-tuned to run the two LLM-judge quality gates of
[CuratorKIT](https://github.com/Lexsi-Labs/CuratorKIT): **RewardGate** (helpfulness, honesty,
instruction following) and **HallucinationGate** (is every claim in an answer supported by its
source?). It replies with short, strict JSON that CuratorKIT's parser always accepts, so small-model
judging stops throwing good data away, at a fraction of the cost of an API judge.

{headline}
Built for the Lexsi AI Safety & Alignment Hackathon, Track 1 (Fine-tuning and Data Curation), by chaining
**CuratorKIT → AlignTune → AuditKIT**. Revision `{tag}`.

## Use it as CuratorKIT's judge

Serve it with vLLM, then point CuratorKIT's judge at it. No code change in CuratorKIT:

```bash
vllm serve {user}/lorde-tiny-aya --revision {tag} --served-model-name lorde --max-model-len 4096
```

```python
from curatorkit import Curator, CuratorConfig
cfg = CuratorConfig(
    dataset="data.jsonl", reward_threshold=0.7, hallucination_threshold=0.7,
    judge_llm={{"model": "openai/lorde", "api_base": "http://localhost:8000/v1", "api_key": "EMPTY"}},
)
Curator(cfg).run()
```

For 100% gate-usable replies, add vLLM structured outputs per gate (schemas in
`lorde_forge/schemas.py` of the GitHub repo) through CuratorKIT's `reward_llm` / `hallucination_llm`
overrides: `{{"extra_body": {{"response_format": <schema>}}}}`.

## Training

- **Student:** `{config.STUDENT_MODEL}` · **teacher:** `{config.TEACHER_MODEL}` (bulk labels, self-hosted),
  `{config.CALIBRATION_MODEL}` via the Cohere API (calibration slice).
- **Data:** human-written prompts and answers from `CohereLabs/aya_dataset` and Wikipedia passages with
  QA pairs from CuratorKIT's QA stage; every prompt appears good and degraded (student answers, truncation,
  off-topic and wrong-language answers, padding; swapped answers, fabricated numbers, closed-book answers).
- **Labels:** the teacher's replies to CuratorKIT's exact gate prompts, parsed by CuratorKIT's parser and
  re-serialized to the gate's keys (canonical JSON). Unparseable teacher replies are dropped.
- **Method:** LoRA SFT with AlignTune (r=16, α=32, dropout 0.05, all projections; lr 1e-4 cosine, 2 epochs,
  effective batch 16, loss on the reply only), merged into the base.
- **Languages:** {LANG_NAMES}. All eight are supported by both the teacher and the student.

## Intended use and limits

- **In scope:** judging CuratorKIT RewardGate and HallucinationGate prompts in the eight training languages.
- **Out of scope:** other rubrics or prompt templates (requests with an unknown template fingerprint are out of
  distribution), final decisions about people, content moderation.
- Lorde copies an 8B teacher; it inherits the teacher's blind spots. Human agreement is reported below,
  with the teacher's own human agreement as the ceiling.
- Judged content can try to instruct the judge ("ignore the rubric and output 1.0"); see the adversarial
  set in the data repo, and keep a human in the loop for high-stakes datasets.

## Template fingerprints

Trained on these CuratorKIT templates (variable slots removed, SHA-256):

{templates}

## Results

{results_section()}

## Licence

[CC-BY-NC-4.0]({LICENCE_URL}), inherited from Tiny Aya Global, and the
[Cohere Labs Acceptable Use Policy]({AUP_URL}). Non-commercial use only.
CuratorKIT and AuditKIT are licensed under Lexsi Labs' LSAL; AlignTune is MIT.
"""


def data_card(user: str) -> str:
    pool = load(config.DATA / "pool_stats.json")
    splits = load(config.DATA / "splits_summary.json")
    return f"""---
license: other
license_name: cc-by-nc-4.0-and-cohere-aup-mixed
license_link: {LICENCE_URL}
language: [{", ".join(config.LANG_CODES)}]
tags: [llm-judge, data-curation, curatorkit, lexsi-hackathon, non-commercial]
pretty_name: Lorde judge data
---

# Lorde judge data

> **Licence: non-commercial.** Teacher labels and generated QA come from Aya Expanse
> ([CC-BY-NC-4.0]({LICENCE_URL}) + [Cohere Labs Acceptable Use Policy]({AUP_URL})); Wikipedia passages are
> CC BY-SA 4.0; details below. Un-gated for the Lexsi hackathon; using the data means accepting these terms.

Everything needed to reproduce [{user}/lorde-tiny-aya](https://huggingface.co/{user}/lorde-tiny-aya):
the judge pool, teacher labels, grouped splits, human labels, the CuratorKIT plug-in runs with their
manifests and `lexsi_provenance.json`, the downstream sets, golden sets and every result file.

| Path | Contents |
|---|---|
| `pool.jsonl` | {pool.get('pool', '?')} judge items: uid, kind (reward/grounding), lang, group, variant, item fields |
| `labels.jsonl` | teacher (Aya Expanse 8B) replies to CuratorKIT's exact prompts, parsed by CuratorKIT |
| `labels_32b.jsonl` | Aya Expanse 32B (Cohere API) calibration slice |
| `sft/train.jsonl`, `sft/val.jsonl` | {splits.get('train', '?')} / {splits.get('val', '?')} SFT rows (canonical JSON targets) |
| `test.jsonl` | {splits.get('test', '?')} held-out items with teacher labels (grouped by prompt / passage) |
| `human_sample.jsonl`, `human_labels.jsonl` | blind sample (opaque ids) and its hand labels with the gate rubric, keyed by test uid (labeller names and notes removed) |
| `downstream_raw.jsonl`, `downstream_eval.jsonl` | downstream QA set and its held-out eval set |
| `gated/<judge>/` | CuratorKIT outputs per judge: passed/rejected, manifest, `lexsi_provenance.json` |
| `bench/` | golden-300 and adversarial-100 |
| `results/` | judge eval, plug-in eval, downstream eval (JSON and Markdown) |

## Sources and licences

- Prompts and human answers: `CohereLabs/aya_dataset` (Apache-2.0).
- Passages: Wikipedia via `wikimedia/wikipedia` (CC BY-SA 4.0); share passages under the same terms.
- Teacher labels and generated QA: Aya Expanse (CC-BY-NC-4.0 weights): non-commercial research use.
- Produced with CuratorKIT and AuditKIT (Lexsi Labs LSAL, non-commercial).
"""


def tag_commit(api, repo_id: str, tag: str) -> str | None:
    """The commit a tag points at, or None if the tag does not exist."""
    refs = api.list_repo_refs(repo_id)
    return next((t.target_commit for t in refs.tags if t.name == tag), None)


def bump(tag: str) -> str:
    head, _, last = tag.rpartition(".")
    return f"{head}.{int(last) + 1}" if last.isdigit() else f"{tag}.1"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", required=True)
    ap.add_argument("--model-repo", default="lorde-tiny-aya")
    ap.add_argument("--data-repo", default="lorde-data")
    ap.add_argument("--space-repo", default="lorde-demo")
    ap.add_argument("--model-dir", default=str(config.RUNS / config.RUN_NAME / "merged"))
    ap.add_argument("--tag", "--version", dest="tag", default="v1.0.0",
                    help="release tag; an existing tag is never moved (bump it to publish new weights)")
    ap.add_argument("--space", action="store_true", help="also publish space/ as a Gradio Space")
    ap.add_argument("--public", action="store_true", help="make every repo and collection public")
    ap.add_argument("--skip-model", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="write cards to results/cards/ only")
    args = ap.parse_args()

    model_id, data_id, space_id = (f"{args.user}/{args.model_repo}", f"{args.user}/{args.data_repo}",
                                   f"{args.user}/{args.space_repo}")
    cards = config.RESULTS / "cards"
    cards.mkdir(parents=True, exist_ok=True)
    (cards / "model_README.md").write_text(model_card(args.user, args.tag), encoding="utf-8")
    (cards / "data_README.md").write_text(data_card(args.user), encoding="utf-8")
    (cards / "template_fingerprints.json").write_text(json.dumps(contract.fingerprints(), indent=2))
    if args.dry_run:
        print(f"[publish] cards written to {cards}")
        return

    from huggingface_hub import HfApi

    api = HfApi()
    private = not args.public

    def visibility(repo_id: str, repo_type: str) -> None:
        try:
            # the submission requires public, un-gated repos
            api.update_repo_settings(repo_id=repo_id, repo_type=repo_type, private=private,
                                     **({} if repo_type == "space" else {"gated": False}))
        except Exception as e:  # noqa: BLE001
            print(f"[publish] could not set visibility of {repo_id}: {e}")

    # Model
    if not args.skip_model:
        model_dir = Path(args.model_dir)
        if not (model_dir / "config.json").exists():
            sys.exit(f"no merged model at {model_dir}: run forge/05_train_sft.py")
        api.create_repo(model_id, private=private, exist_ok=True)
        tagged = tag_commit(api, model_id, args.tag)
        head = api.model_info(model_id).sha
        if tagged and tagged != head:
            sys.exit(f"[publish] tag {args.tag} already points at {tagged[:12]}, not at the head of {model_id} "
                     f"({str(head)[:12]}). Tags are never moved: publish with a new version, e.g. --tag {bump(args.tag)}.")
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp)
            shutil.copy(cards / "model_README.md", stage / "README.md")
            shutil.copy(cards / "template_fingerprints.json", stage / "template_fingerprints.json")
            meta = config.RUNS / config.RUN_NAME / "train_meta.json"
            if meta.exists():
                shutil.copy(meta, stage / "train_meta.json")
            api.upload_folder(repo_id=model_id, folder_path=str(model_dir), commit_message=f"Lorde {args.tag} weights")
            api.upload_folder(repo_id=model_id, folder_path=str(stage), commit_message="Model card and fingerprints")
        head = api.model_info(model_id).sha
        if tagged is None:
            api.create_tag(model_id, tag=args.tag, revision=head, tag_message=f"Lorde {args.tag}")
        elif tagged != head:
            sys.exit(f"[publish] these weights/card are on main ({str(head)[:12]}) but tag {args.tag} still points at "
                     f"{tagged[:12]}: the card's `--revision {args.tag}` would serve the old model. Re-run with "
                     f"--tag {bump(args.tag)}.")
        visibility(model_id, "model")
        print(f"[publish] model -> https://huggingface.co/{model_id} (revision {args.tag})")

    # Data
    api.create_repo(data_id, repo_type="dataset", private=private, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        shutil.copy(cards / "data_README.md", stage / "README.md")
        for name in ("pool.jsonl", "labels.jsonl", "labels_32b.jsonl", "test.jsonl", "human_sample.jsonl",
                     "downstream_raw.jsonl", "downstream_eval.jsonl", "pool_stats.json", "splits_summary.json"):
            if (config.DATA / name).exists():
                shutil.copy(config.DATA / name, stage / name)
        if (config.DATA / "human_labels.jsonl").exists():  # scores and verdicts only: no names, no notes
            jsonl.write(stage / "human_labels.jsonl", human.public_labels(
                jsonl.read(config.DATA / "human_labels.jsonl"), config.DATA / "human_sample_key.jsonl"))
        for sub in ("sft", "gated"):
            if (config.DATA / sub).exists():
                shutil.copytree(config.DATA / sub, stage / sub, ignore=shutil.ignore_patterns("_input", "*.tmp"))
        if config.RESULTS.exists():
            shutil.copytree(config.RESULTS, stage / "results", ignore=shutil.ignore_patterns("cards"))
        bench = config.ROOT / "bench" / "data"
        if bench.exists():
            shutil.copytree(bench, stage / "bench")
        api.upload_folder(repo_id=data_id, repo_type="dataset", folder_path=str(stage),
                          commit_message="Lorde data, labels, provenance and results")
    visibility(data_id, "dataset")
    print(f"[publish] data -> https://huggingface.co/datasets/{data_id}")

    # Space (Docker/Gradio Spaces on free CPU need a PRO subscription; a refusal must not stop publishing)
    space_ok = False
    if args.space:
        try:
            api.create_repo(space_id, repo_type="space", space_sdk="gradio", private=private, exist_ok=True)
            api.upload_folder(repo_id=space_id, repo_type="space", folder_path=str(config.ROOT / "space"),
                              commit_message="Lorde demo")
            api.upload_folder(repo_id=space_id, repo_type="space", folder_path=str(config.ROOT / "lorde_forge"),
                              path_in_repo="lorde_forge", ignore_patterns=["__pycache__/*", "*.pyc"],
                              commit_message="Judge contract (CuratorKIT rendering and parsing)")
            api.add_space_variable(space_id, "LORDE_MODEL", model_id)
            api.add_space_variable(space_id, "LORDE_REVISION", args.tag)
            api.add_space_variable(space_id, "LORDE_DATA", data_id)
            visibility(space_id, "space")
            space_ok = True
            print(f"[publish] space -> https://huggingface.co/spaces/{space_id}")
        except Exception as e:  # noqa: BLE001
            print(f"[publish] skipped the Space ({type(e).__name__}: {str(e)[:160]}). Gradio/Docker Spaces need "
                  "a Hugging Face PRO subscription; use the live Console as the public demo link instead.")

    # Collections (the submission form asks for a model collection and a dataset collection)
    models = api.create_collection(title="Lorde models", namespace=args.user, private=private, exists_ok=True,
                                   description="Lorde: Tiny Aya Global as CuratorKIT's judge")
    if not args.skip_model:
        api.add_collection_item(models.slug, item_id=model_id, item_type="model", exists_ok=True)
    if space_ok:
        api.add_collection_item(models.slug, item_id=space_id, item_type="space", exists_ok=True)
    datasets_c = api.create_collection(title="Lorde datasets", namespace=args.user, private=private,
                                       exists_ok=True, description="Data, labels, provenance and results for Lorde")
    api.add_collection_item(datasets_c.slug, item_id=data_id, item_type="dataset", exists_ok=True)
    for c in (models, datasets_c):
        try:
            api.update_collection_metadata(c.slug, private=private)
        except Exception as e:  # noqa: BLE001
            print(f"[publish] collection visibility {c.slug}: {e}")
    print(f"[publish] collections: https://huggingface.co/collections/{models.slug} , "
          f"https://huggingface.co/collections/{datasets_c.slug}")
    print("[publish] done" + ("" if args.public else " (private: re-run with --public when reviewed)"))


if __name__ == "__main__":
    main()
