# Lorde

**A 3B judge model for trustworthy training data.** Lorde is Cohere's **Tiny Aya Global (3.35B)**
fine-tuned to run [CuratorKIT](https://github.com/Lexsi-Labs/CuratorKIT)'s two LLM-judge quality gates:
**RewardGate** (helpfulness, honesty, instruction following) and **HallucinationGate** (is every claim in an
answer supported by its source?). It gives API-grade gate decisions from a model that runs on one inexpensive
GPU, in replies CuratorKIT's strict parser always accepts.

Built for the **Lexsi AI Safety & Alignment Hackathon, Track 1 (Fine-tuning and Data Curation)** by chaining
all three Track 1 toolkits: **CuratorKIT → AlignTune → AuditKIT**. This repo holds Phase 0 (spec
LORDE-SPEC-001 §7, §9, §18.1): the **Lorde Forge** pipeline, **Lorde Bench**, the demo Space and the
HF Article tooling.

| | |
|---|---|
| Student | `CohereLabs/tiny-aya-global` (3.35B, CC-BY-NC-4.0) |
| Teacher | `CohereLabs/aya-expanse-8b` (self-hosted vLLM, bulk labels) · `c4ai-aya-expanse-32b` via the Cohere API (calibration) |
| Languages | English, Hindi, French, Arabic, Spanish, Portuguese, Indonesian, Turkish |
| Problem statements | *Replacing a closed-model API* · *Data-scarce domains* (curated data measurably beats raw) |
| Outputs | HF model `<user>/lorde-tiny-aya`, data `<user>/lorde-data`, Space `<user>/lorde-demo`, two HF collections |

**Why these eight languages.** Each is supported by *both* the teacher (Aya Expanse lists 23 languages) and
the student, so every label comes from a teacher that actually reads the language. Together they cover a
wide spread of scripts and regions (Latin, Devanagari, Arabic; Europe, South Asia, the Middle East,
Southeast Asia, Latin America, Africa). The spec's earlier set included Swahili, Yoruba and Hausa, which Aya
Expanse does not support, so the teacher would have been guessing in them.

## How it works

```
aya_dataset prompts ─┐                         ┌─ CuratorKIT renders the exact gate prompts
Wikipedia passages ──┼─ CuratorKIT QA stage ─► pool (good + degraded) ─► teacher labels (CuratorKIT parser)
                     │                                                        │ canonical JSON targets
                     │                                       AlignTune LoRA SFT on Tiny Aya Global ─► Lorde
                     │                                                        │
                     └─ downstream set ─► real CuratorKIT runs with each judge ─► 3 small SFTs ─► AuditKIT
```

The judge contract is CuratorKIT's own code: prompts come from `RewardGate._build_reward_prompt` and
`HallucinationGate.prompt_template`, replies go through `parse_judge_reply` and `extract_score`. A reply is
**gate-usable** if and only if that parser returns a score. Decisions are pass iff score ≥ 0.7; unparseable
replies are rejects, exactly like CuratorKIT's default `judge_on_error="reject"`.

## Repository layout

```
lorde_forge/     shared library: contract (render/parse/fingerprints), schemas, canonical targets, variants,
                 OpenAI-compatible client, metrics, grouped splits, AuditKIT glue, LoRA training
forge/           Phase 0 pipeline: steps 00-09 and kaggle_runbook.ipynb
bench/           Lorde Bench: golden-300, adversarial-100, CI runner with blocking criteria
space/           Gradio demo Space (playground, Bench results)
docs/            HF Article template and builder, submission checklist
requirements/    forge-train, forge-serve, forge-data (three separate environments) and dev
tests/           contract and parser fuzzing, metrics, and an end-to-end run on stub OpenAI servers
```

## Run it

The full run fits on a free **Kaggle 2×T4** notebook: open [`forge/kaggle_runbook.ipynb`](forge/kaggle_runbook.ipynb).
Step by step:

| Step | Command | Env | Output |
|---|---|---|---|
| 0 | `bash forge/00_serve.sh teacher\|student\|lorde\|stop` | serve | vLLM on :8000 / :8001 / :8002 |
| 1 | `python forge/01_build_pool.py --phase teacher\|student\|assemble` | data | `data/pool.jsonl`, `downstream_raw/eval.jsonl` |
| 2 | `python forge/02_teacher_label.py --workers 32` | data | `data/labels.jsonl` |
| 3 | `python forge/03_make_splits.py --human-langs en,fr,es` | data | `data/sft/*`, `data/test.jsonl`, `human_sample.jsonl` |
| 4 | `python forge/04_label_tool.py --labeler you --share` | data | `data/human_labels.jsonl` |
| 5 | `python forge/05_train_sft.py [--engine trl]` | train | `runs/lorde-v1/merged` |
| 6 | `python forge/06_judge_eval.py --judge base=... --judge lorde=... --structured` | data | `results/judge_eval.md` |
| 7 | `python forge/07_curatorkit_plugin.py --judge lorde=... [--structured]` | data | `data/gated/*`, `results/plugin_eval.md` |
| 8 | `python forge/08_downstream.py all --sets ungated,teacher,lorde` | train | `results/downstream.md` |
| 9 | `python forge/09_publish.py --user you --space [--public]` | data | HF repos, cards, collections |
| Bench | `python bench/build_sets.py` · `python bench/run_bench.py --judge lorde=... --structured` | data | `bench/results/*` |
| Article | `python docs/build_article.py` | data | `docs/HF_ARTICLE.md` |

A judge is named `name=openai/<served-model>@<base_url>`, e.g. `lorde=openai/lorde@http://localhost:8002/v1`.
Calibration with Aya Expanse 32B (300 Cohere API calls on golden-300):

```bash
python forge/02_teacher_label.py --model c4ai-aya-expanse-32b --base https://api.cohere.ai/compatibility/v1 \
  --api-key-env COHERE_API_KEY --sample-from bench/data/golden-300.jsonl --max-calls 300 --workers 4 \
  --out data/labels_32b.jsonl
```

## Use Lorde as CuratorKIT's judge

```python
from curatorkit import Curator, CuratorConfig
from lorde_forge.schemas import response_format

cfg = CuratorConfig(
    dataset="data.jsonl", reward_threshold=0.7, hallucination_threshold=0.7,
    judge_llm={"model": "openai/lorde", "api_base": "http://localhost:8002/v1", "api_key": "EMPTY"},
    # optional: vLLM structured outputs, one schema per gate, through CuratorKIT's per-role overrides
    reward_llm={"extra_body": {"response_format": response_format("reward")}},
    hallucination_llm={"extra_body": {"response_format": response_format("grounding")}},
)
Curator(cfg).run()
```

## Reproducibility notes

- **Every step resumes.** Model calls append to JSONL caches as replies arrive (`data/cache/`, `data/labels.jsonl`,
  `results/preds/`); re-running a step skips finished items. The runbook syncs `data/` to a private HF dataset
  between Kaggle sessions.
- **Deterministic data.** Prompt sampling, variant assignment, degradations, splits and the human sample are
  derived from SHA-256 hashes of stable ids, not from a global RNG. Generation uses temperature 0.1 (judges) and
  fixed seeds (training).
- **No leakage.** Splits are grouped by prompt (reward) and source passage (grounding). The downstream raw and
  eval sets come from Wikipedia articles never used for judge training.
- **Pinned versions.** `requirements/*.txt`; three separate environments because AlignTune pins `trl==1.7.1`
  and `torch<2.14` while vLLM brings its own torch.
- **Template fingerprints.** SHA-256 of each CuratorKIT template with its slots removed is recorded in
  `results/labels_summary.json`, every eval report and the model card. A CuratorKIT upgrade that changes the
  prompts shows up as a new fingerprint.
- **Provenance.** Every CuratorKIT run writes its manifest and `lexsi_provenance.json` into `data/gated/<judge>/`,
  published in the data repo.

## Tests

```bash
pip install -r requirements/dev.txt
pytest -q      # contract vs CuratorKIT, parser fuzzing (Hypothesis), metrics, splits, end-to-end on stub servers
```

The end-to-end test runs steps 1-3 and 6-9, Bench and the article builder against small OpenAI-compatible stub
servers (no GPU). Training (step 5) and downstream training and generation (step 8) need a GPU and run on Kaggle.

## Changes from the spec

- **Languages:** the spec's sw/yo/ha were replaced by pt/id/tr (see above).
- **Structured outputs in CuratorKIT runs:** the spec's worker sent `judge_llm` straight to Serve, so it got no
  schemas. Step 7 adds them per gate through CuratorKIT's `reward_llm` / `hallucination_llm` overrides.
- **`CuratorConfig` has no `pii` field.** The PII option is `pii_pseudonymize`; Phase 1's worker must use that.
- **Calibration on golden-300:** the 32B slice labels golden-300 (also used for CI), so cost and agreement
  numbers compare against the same 32B judge the ROI section prices.
- **Closed-model reference:** step 6 accepts any OpenAI-compatible API judge (`--judge-key`, `--api-price`)
  for the closed-source ROI comparison the rules prefer.

## Licences

Code in this repo: MIT. **Lorde's weights are CC-BY-NC-4.0**, inherited from Tiny Aya Global (Cohere Labs
Acceptable Use Policy applies): non-commercial use only. CuratorKIT (LSAL v1.1) and AuditKIT (LSAL v1.2) are
Lexsi Labs source-available licences for non-commercial use; AlignTune is MIT. Any paid launch needs
commercial licences from Cohere and Lexsi Labs first (spec §15).
