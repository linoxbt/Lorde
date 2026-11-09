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
p < 0.05) and lorde vs teacher (should be within 2% relative).

Writes runs/downstream/<set>/, results/downstream/preds_<set>.jsonl and
results/downstream.{json,md}.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lorde_forge import audit, config, jsonl, variants  # noqa: E402
from lorde_forge import train as T  # noqa: E402
from lorde_forge.report import fmt, table  # noqa: E402

PROMPT = ("Answer the question using only the source text. If the source does not contain the answer, "
          "say so.\n\nSource:\n{context}\n\nQuestion: {question}\nAnswer:")

RUNS = config.RUNS / "downstream"
OUT = config.RESULTS / "downstream"


def sft_rows(rows: list[dict]) -> list[dict]:
    return [{"prompt": PROMPT.format(context=r["input"], question=r["instruction"]),
             "completion": " " + r["output"].strip()} for r in rows]


def stage_train(args) -> None:
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(args.base_model)
    sizes = {s: len(jsonl.read(config.DATA / "gated" / s / "passed.jsonl")) for s in args.sets}
    n_equal = min(sizes.values()) if args.equal_size else None
    recipe = T.Recipe(epochs=args.epochs, batch_size=4, grad_accum=4, lr=2e-4, max_len=1536)
    for s in args.sets:
        out = RUNS / s
        if (out / "merged" / "config.json").exists() and not args.force:
            print(f"[downstream] {s}: already trained ({out / 'merged'})")
            continue
        rows = jsonl.read(config.DATA / "gated" / s / "passed.jsonl")
        if not rows:
            sys.exit(f"no gated set for {s}: run forge/07_curatorkit_plugin.py first")
        if n_equal:
            rows = sorted(rows, key=lambda r: variants.rng_for("eq", r["uid"]).random())[:n_equal]
        keep, dropped = T.filter_by_tokens(sft_rows(rows), tok, recipe.max_len)
        path = out / "train.jsonl"
        jsonl.write(path, keep)
        print(f"[downstream] {s}: training on {len(keep)} rows ({dropped} too long)")
        t0 = time.time()
        if args.engine == "aligntune":
            T.train_aligntune(args.base_model, path, out / "adapter", len(keep), recipe)
        else:
            T.train_trl(args.base_model, path, None, out / "adapter", len(keep), recipe)
        T.merge(args.base_model, out / "adapter", out / "merged")
        jsonl.write_json(out / "meta.json", {"rows": len(keep), "dropped": dropped, "seconds": round(time.time() - t0)})


def generate(model_path: str, rows: list[dict], batch: int, max_new_tokens: int) -> list[str]:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model_path, padding_side="left")
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(model_path, dtype=T.torch_dtype(),
                                                 device_map="auto").eval()
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
    targets = [(s, str(RUNS / s / "merged")) for s in args.sets]
    if args.include_base:
        targets.insert(0, ("base", args.base_model))
    for name, path in targets:
        dst = OUT / f"preds_{name}.jsonl"
        if dst.exists() and not args.force:
            print(f"[downstream] {name}: predictions exist ({dst})")
            continue
        outs = generate(path, ev, args.gen_batch, args.max_new_tokens)
        jsonl.write(dst, ({**r, "output": o} for r, o in zip(ev, outs)))


def stage_score(args) -> None:
    names = (["base"] if (OUT / "preds_base.jsonl").exists() else []) + list(args.sets)
    runs, report = {}, {"metrics": {}, "by_lang": {}, "train_rows": {}, "significance": {}}
    for name in names:
        rows = jsonl.read(OUT / f"preds_{name}.jsonl")
        if not rows:
            print(f"[downstream] no predictions for {name}; skipping")
            continue
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
            import json

            report["train_rows"][name] = json.loads(meta.read_text())["rows"]

    cand = "lorde"
    for base in ("ungated", "teacher", "base"):
        if cand in runs and base in runs:
            report["significance"][f"{cand} vs {base}"] = {
                m: audit.run_significance(runs[base], runs[cand], m) for m in ("f1_score", "lexical_groundedness")}
    if cand in report["metrics"] and "teacher" in report["metrics"]:
        t, c = report["metrics"]["teacher"]["f1_score"], report["metrics"][cand]["f1_score"]
        report["f1_relative_to_teacher"] = round((c - t) / t, 4) if t else None
        report["within_2pct_of_teacher"] = t == 0 or (c - t) / t >= -0.02
    s = report["significance"].get(f"{cand} vs ungated", {}).get("f1_score", {})
    if s:
        report["better_than_ungated_p05"] = bool(s.get("significant") and s.get("delta", 0) > 0)
    jsonl.write_json(config.RESULTS / "downstream.json", report)
    (config.RESULTS / "downstream.md").write_text(render_md(report), encoding="utf-8")
    print(f"[downstream] wrote {config.RESULTS / 'downstream.md'}")


def render_md(r: dict) -> str:
    m = r["metrics"]
    lines = ["# Downstream test: models trained on gated data\n",
             "Tiny Aya Base + LoRA trained on each gated set, evaluated on held-out Wikipedia QA "
             "(articles never used elsewhere). AuditKIT `f1_score` vs the reference answer, "
             "`lexical_groundedness` vs the source passage.\n",
             table(["Training data", "Rows", "F1", "Groundedness"],
                   [[n, r["train_rows"].get(n, "–"), fmt(v.get("f1_score")), fmt(v.get("lexical_groundedness"))]
                    for n, v in m.items()]), ""]
    if "f1_relative_to_teacher" in r:
        lines.append(f"Lorde-gated F1 relative to teacher-gated: **{r['f1_relative_to_teacher'] * 100:+.1f}%** "
                     f"(criterion: within 2%; {'met' if r['within_2pct_of_teacher'] else 'not met'}).\n")
    if "better_than_ungated_p05" in r:
        lines.append(f"Lorde-gated beats ungated with p < 0.05: **{fmt(r['better_than_ungated_p05'])}**.\n")
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
    ap.add_argument("--include-base", action="store_true", help="also score the untrained base model")
    ap.add_argument("--gen-batch", type=int, default=16)
    ap.add_argument("--max-new-tokens", type=int, default=128)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    args.sets = [s.strip() for s in args.sets.split(",") if s.strip()]
    if args.stage in ("train", "all"):
        stage_train(args)
    if args.stage in ("generate", "all"):
        stage_generate(args)
    if args.stage in ("score", "all"):
        stage_score(args)


if __name__ == "__main__":
    main()
