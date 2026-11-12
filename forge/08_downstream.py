#!/usr/bin/env python
"""Step 8: does data gated by Lorde train models as well as data gated by the teacher?

    python forge/08_downstream.py all --sets ungated,teacher,lorde
    python forge/08_downstream.py train --sets lorde          # one stage at a time
    python forge/08_downstream.py generate --sets ungated,teacher,lorde --include-base
    python forge/08_downstream.py score --sets ungated,teacher,lorde

For each gated set (data/gated/<set>/passed.jsonl from step 7) a small LoRA SFT of
Tiny Aya Base learns to answer questions from a source passage (AlignTune, or
``--engine trl``). Each model then answers the held-out downstream eval set
(Wikipedia articles never used anywhere else), and AuditKIT scores the answers:
``f1_score`` against the reference answer and ``lexical_groundedness`` against
the source. AuditKIT's paired bootstrap tests lorde vs ungated (should be better,
p < 0.05) and lorde vs teacher (should be within ±2% relative; undefined if the teacher's F1 is 0).

Writes runs/downstream/<set>/, results/downstream/preds_<set>.jsonl and
results/downstream.{json,md}.

Nothing stale is reused: a trained set is kept only if runs/downstream/<set>/meta.json records the same
gated data (hash of passed.jsonl), and predictions only if preds_<set>.meta.json records the same adapter
(hash of its weights) and eval set. Outputs from before these fingerprints existed are reused only with
``--trust-legacy-cache`` (e.g. to score a finished run: ``score --trust-legacy-cache``).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lorde_forge import audit, config, fingerprint, jsonl, variants  # noqa: E402
from lorde_forge import train as T  # noqa: E402
from lorde_forge.report import fmt, table  # noqa: E402

PROMPT = ("Answer the question using only the source text. If the source does not contain the answer, "
          "say so.\n\nSource:\n{context}\n\nQuestion: {question}\nAnswer:")

RUNS = config.RUNS / "downstream"
OUT = config.RESULTS / "downstream"


def sft_rows(rows: list[dict]) -> list[dict]:
    return [{"prompt": PROMPT.format(context=r["input"], question=r["instruction"]),
             "completion": " " + r["output"].strip()} for r in rows]


EVAL_FIELDS = ("uid", "question", "context", "answer")


def data_sha(rows: list[dict]) -> str:
    return fingerprint.rows_sha(rows, ("uid", "instruction", "input", "output"))


def preds_meta(args, name: str, adapter: Path | None, ev: list[dict]) -> dict:
    a = None
    if adapter is not None and any(adapter.rglob("adapter_config.json")):
        a = fingerprint.dir_sha(T.find_adapter(adapter))
    return {"base_model": args.base_model, "adapter_sha": "none" if adapter is None else a,
            "eval_sha": fingerprint.rows_sha(ev, EVAL_FIELDS), "n_eval": len(ev), "max_new_tokens": args.max_new_tokens,
            "prompt_sha": fingerprint.sha256(PROMPT)}


def trained(out: Path, sha: str, trust_legacy: bool) -> tuple[bool, str]:
    """Is runs/downstream/<set> a finished training run on exactly this gated data?"""
    meta_p = out / "meta.json"
    if not meta_p.exists():
        return False, "not trained"
    meta = json.loads(meta_p.read_text())
    if meta.get("data_sha") is None:
        return (True, "legacy run, trusted by --trust-legacy-cache") if trust_legacy else (
            False, "legacy run without a data fingerprint (pass --trust-legacy-cache to keep it)")
    return (True, "same gated data") if meta["data_sha"] == sha else (False, "the gated data changed")


def stage_train(args) -> None:
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(args.base_model)
    sizes = {s: len(jsonl.read(config.DATA / "gated" / s / "passed.jsonl")) for s in args.sets}
    n_equal = min(sizes.values()) if args.equal_size else None
    # Batch 1 x 16 like Lorde's own recipe: with Tiny Aya's 261k-token vocabulary, 4 sequences of logits do not fit
    # on a 16 GB T4 (CUDA OOM at batch 4 x 4). Same effective batch of 16.
    recipe = T.Recipe(epochs=args.epochs, batch_size=1, grad_accum=16, lr=2e-4, max_len=args.max_len)
    for s in args.sets:
        if args.only_sets and s not in args.only_sets:
            continue  # another process (GPU) trains this set; --sets still decides the equal size
        out = RUNS / s
        rows = jsonl.read(config.DATA / "gated" / s / "passed.jsonl")
        if not rows:
            sys.exit(f"no gated set for {s}: run forge/07_curatorkit_plugin.py first")
        if n_equal:
            rows = sorted(rows, key=lambda r: variants.rng_for("eq", r["uid"]).random())[:n_equal]
        if args.max_rows and len(rows) > args.max_rows:
            # Seeded, so every set is capped the same way and re-runs see the same rows.
            rows = sorted(rows, key=lambda r: variants.rng_for("cap", r["uid"]).random())[:args.max_rows]
        sha = data_sha(rows)
        done, why = trained(out, sha, args.trust_legacy_cache)
        if done and not args.force:
            print(f"[downstream] {s}: already trained ({why}; {out / 'adapter'})")
            continue
        # Checkpoints may only be resumed when they were trained on these very rows.
        pending = out / "pending.json"
        started = json.loads(pending.read_text()).get("data_sha") if pending.exists() else None
        if (out / "meta.json").exists() or args.force or (started and started != sha):
            print(f"[downstream] {s}: training from scratch ({why if (out / 'meta.json').exists() else 'new data'})")
            shutil.rmtree(out / "adapter", ignore_errors=True)
            (out / "meta.json").unlink(missing_ok=True)
        jsonl.write_json(pending, {"data_sha": sha})
        keep, dropped = T.filter_by_tokens(sft_rows(rows), tok, recipe.max_len)
        path = out / "train.jsonl"
        jsonl.write(path, keep)
        print(f"[downstream] {s}: training on {len(keep)} rows ({dropped} too long)")
        t0 = time.time()
        if args.engine == "aligntune":
            T.train_aligntune(args.base_model, path, out / "adapter", len(keep), recipe)
        else:
            T.train_trl(args.base_model, path, None, out / "adapter", len(keep), recipe)
        # No merge: generation loads base + adapter, which saves ~6.7 GB of disk per set on Kaggle.
        jsonl.write_json(out / "meta.json", {"rows": len(keep), "dropped": dropped, "seconds": round(time.time() - t0),
                                             "data_sha": sha, "base_model": args.base_model})


def generate(base_model: str, adapter: Path | None, rows: list[dict], batch: int, max_new_tokens: int) -> list[str]:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(base_model, padding_side="left")
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(base_model, dtype=T.torch_dtype(), device_map="auto")
    if adapter is not None:
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, str(T.find_adapter(adapter)))
    model = model.eval()
    outs: list[str] = []
    for i in range(0, len(rows), batch):
        prompts = [PROMPT.format(context=r["context"], question=r["question"]) for r in rows[i: i + batch]]
        enc = tok(prompts, return_tensors="pt", padding=True).to(model.device)
        with torch.no_grad():
            gen = model.generate(**enc, max_new_tokens=max_new_tokens, do_sample=False,
                                 pad_token_id=tok.pad_token_id)
        for seq in gen[:, enc["input_ids"].shape[1]:]:
            text = tok.decode(seq, skip_special_tokens=True).strip()
            outs.append(text.split("\nQuestion:")[0].split("\n\n")[0].strip())
        print(f"[downstream] generated {min(i + batch, len(rows))}/{len(rows)}")
    del model
    torch.cuda.empty_cache()
    return outs


def stage_generate(args) -> None:
    ev = jsonl.read(config.DATA / "downstream_eval.jsonl")
    if args.limit:
        ev = ev[: args.limit]
    targets: list[tuple[str, Path | None]] = [(s, RUNS / s / "adapter") for s in args.sets]
    if args.include_base:
        targets.insert(0, ("base", None))
    for name, adapter in targets:
        dst = OUT / f"preds_{name}.jsonl"
        meta = preds_meta(args, name, adapter, ev)
        usable, status = fingerprint.check(dst, meta, args.trust_legacy_cache)
        if usable and not args.force:
            print(f"[downstream] {name}: predictions exist ({dst}; {status})")
            continue
        if dst.exists():
            fingerprint.discard(dst, status)
        outs = generate(args.base_model, adapter, ev, args.gen_batch, args.max_new_tokens)
        jsonl.write(dst, ({**r, "output": o} for r, o in zip(ev, outs)))
        fingerprint.write_meta(dst, meta)


def stage_score(args) -> None:
    names = (["base"] if (OUT / "preds_base.jsonl").exists() else []) + list(args.sets)
    runs, report = {}, {"metrics": {}, "by_lang": {}, "train_rows": {}, "significance": {}, "preds": {}}
    ev = jsonl.read(config.DATA / "downstream_eval.jsonl")
    if args.limit:
        ev = ev[: args.limit]
    for name in names:
        dst = OUT / f"preds_{name}.jsonl"
        rows = jsonl.read(dst)
        if not rows:
            print(f"[downstream] no predictions for {name}; skipping")
            continue
        # Predictions must come from the current adapter on the current eval set.
        usable, status = fingerprint.check(dst, preds_meta(args, name, None if name == "base" else RUNS / name / "adapter",
                                                           ev), args.trust_legacy_cache)
        if not usable:
            print(f"[downstream] {name}: predictions not scored ({status}); re-run generate")
            continue
        report["preds"][name] = status
        runs[name] = audit.answer_run(name, rows)
        report["metrics"][name] = dict(runs[name].headline)
        f1 = audit.per_sample(runs[name], "f1_score")
        gr = audit.per_sample(runs[name], "lexical_groundedness")
        by_lang: dict[str, dict] = {}
        for lang in sorted({r["lang"] for r in rows}):
            ids = [r["uid"] for r in rows if r["lang"] == lang]
            by_lang[lang] = {"f1_score": round(sum(f1.get(i, 0) for i in ids) / len(ids), 4),
                             "lexical_groundedness": round(sum(gr.get(i, 0) for i in ids) / len(ids), 4)}
        report["by_lang"][name] = by_lang
        meta = RUNS / name / "meta.json"
        if meta.exists():
            report["train_rows"][name] = json.loads(meta.read_text())["rows"]

    cand = "lorde"
    for base in ("ungated", "teacher", "base"):
        if cand in runs and base in runs:
            report["significance"][f"{cand} vs {base}"] = {
                m: audit.run_significance(runs[base], runs[cand], m) for m in ("f1_score", "lexical_groundedness")}
    if cand in report["metrics"] and "teacher" in report["metrics"]:
        t, c = report["metrics"]["teacher"]["f1_score"], report["metrics"][cand]["f1_score"]
        report["f1_relative_to_teacher"], report["within_2pct_of_teacher"] = within_2pct(t, c)
    s = report["significance"].get(f"{cand} vs ungated", {}).get("f1_score", {})
    if s:
        report["better_than_ungated_p05"] = bool(s.get("significant") and s.get("delta", 0) > 0)
    # The training setup behind these numbers: one run (one seed) per set, so the article can state its limits.
    report["setup"] = {"epochs": args.epochs, "max_rows": args.max_rows or None, "equal_size": args.equal_size,
                       "max_len": args.max_len, "engine": args.engine, "seeds": 1, "train_seed": T.SEED,
                       "max_new_tokens": args.max_new_tokens, "n_eval": len(ev)}
    jsonl.write_json(config.RESULTS / "downstream.json", report)
    (config.RESULTS / "downstream.md").write_text(render_md(report), encoding="utf-8")
    print(f"[downstream] wrote {config.RESULTS / 'downstream.md'}")


def within_2pct(teacher_f1: float, lorde_f1: float) -> tuple[float | None, bool | None]:
    """(relative difference, criterion met). Two-sided ("within 2% relative"), and undefined, not vacuously
    met, when the teacher-gated F1 is 0."""
    if not teacher_f1:
        return None, None
    rel = (lorde_f1 - teacher_f1) / teacher_f1
    return round(rel, 4), abs(rel) <= 0.02


def render_md(r: dict) -> str:
    m = r["metrics"]
    lines = ["# Downstream test: models trained on gated data\n",
             "Tiny Aya Base + LoRA trained on each gated set, evaluated on held-out Wikipedia QA "
             "(articles never used elsewhere). AuditKIT `f1_score` vs the reference answer, "
             "`lexical_groundedness` vs the source passage.\n",
             table(["Training data", "Rows", "F1", "Groundedness"],
                   [[n, r["train_rows"].get(n, "–"), fmt(v.get("f1_score")), fmt(v.get("lexical_groundedness"))]
                    for n, v in m.items()]), ""]
    if r.get("f1_relative_to_teacher") is not None:
        lines.append(f"Lorde-gated F1 relative to teacher-gated: **{r['f1_relative_to_teacher'] * 100:+.1f}%** "
                     f"(criterion: within ±2%; {'met' if r['within_2pct_of_teacher'] else 'not met'}).\n")
    elif "f1_relative_to_teacher" in r:
        lines.append("Lorde-gated F1 relative to teacher-gated: undefined (teacher-gated F1 is 0), so the "
                     "within-2% criterion is not measured.\n")
    if "better_than_ungated_p05" in r:
        lines.append(f"Lorde-gated beats ungated on F1 with p < 0.05: **{fmt(r['better_than_ungated_p05'])}**.\n")
    worse = [(k.split(" vs ")[1], s) for k, d in r.get("significance", {}).items()
             if (s := d.get("lexical_groundedness")) and s.get("significant") and (s.get("delta") or 0) < 0]
    if worse:
        lines.append("Groundedness: Lorde-gated answers are **significantly less grounded** in the source than "
                     + " and ".join(f"{'the ungated set' if b == 'ungated' else b + '-gated'} ({fmt(s.get('mean_candidate'))} vs {fmt(s.get('mean_baseline'))}, "
                                    f"p = {s.get('p_value', 0):.3f})" for b, s in worse) + ".\n")
    if r.get("setup"):
        st = r["setup"]
        lines.append(f"Setup: {st.get('seeds', 1)} training run (seed {st.get('train_seed', '?')}) per set, "
                     f"{st.get('epochs')} epoch(s)"
                     + (f", at most {st['max_rows']:,} rows per set" if st.get("max_rows") else "")
                     + (", every set subsampled to the same size" if st.get("equal_size") else "")
                     + "; differences of this size need more seeds before they can be called parity.\n")
    if r["significance"]:
        rows = []
        for k, d in r["significance"].items():
            for metric, s in d.items():
                rows.append([k, metric, fmt(s.get("mean_baseline")), fmt(s.get("mean_candidate")),
                             fmt(s.get("delta")), s.get("p_value"), fmt(s.get("significant"))])
        lines += ["## Significance (AuditKIT paired bootstrap)\n",
                  table(["Comparison", "Metric", "baseline", "candidate", "Δ", "p", "significant"], rows), ""]
    langs = sorted({lg for v in r["by_lang"].values() for lg in v})
    lines += ["## F1 by language\n",
              table(["Training data", *langs], [[n, *(fmt(v.get(lg, {}).get("f1_score")) for lg in langs)]
                                                for n, v in r["by_lang"].items()]), ""]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["train", "generate", "score", "all"])
    ap.add_argument("--sets", default="ungated,teacher,lorde")
    ap.add_argument("--base-model", default=config.DOWNSTREAM_BASE_MODEL)
    ap.add_argument("--engine", choices=["aligntune", "trl"], default="aligntune")
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--equal-size", action="store_true", help="subsample every set to the smallest size")
    ap.add_argument("--only-sets", default="", help="train only these of --sets (e.g. one GPU per process)")
    ap.add_argument("--max-rows", type=int, default=0, help="seeded cap on training rows per set (0 = no cap)")
    ap.add_argument("--max-len", type=int, default=1536, help="drop training rows longer than this many tokens")
    ap.add_argument("--include-base", action="store_true", help="also score the untrained base model")
    ap.add_argument("--gen-batch", type=int, default=16)
    ap.add_argument("--max-new-tokens", type=int, default=128)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--trust-legacy-cache", action="store_true",
                    help="reuse trained sets and predictions written before fingerprints existed")
    args = ap.parse_args()
    args.sets = [s.strip() for s in args.sets.split(",") if s.strip()]
    args.only_sets = [s.strip() for s in args.only_sets.split(",") if s.strip()]
    if args.stage in ("train", "all"):
        stage_train(args)
    if args.stage in ("generate", "all"):
        stage_generate(args)
    if args.stage in ("score", "all"):
        stage_score(args)


if __name__ == "__main__":
    main()
