#!/usr/bin/env python
"""Fill docs/HF_ARTICLE.md from the template and the measured results in results/.

    python docs/build_article.py [--rows 100000] [--gpu l4|t4]

Only measured numbers go into the article; sections whose results are missing say so.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lorde_forge import config, human  # noqa: E402
from lorde_forge.report import table  # noqa: E402

TEMPLATE = Path(__file__).resolve().parent / "HF_ARTICLE.template.md"
DOCS = config.ROOT / "docs"


def section(name: str) -> str:
    p = config.RESULTS / f"{name}.md"
    if not p.exists():
        return f"_Pending: run the step that writes results/{name}.md._"
    return "\n".join("##" + line if line.startswith("#") else line for line in p.read_text().splitlines())


def _read_jsonl(p: Path) -> list[dict]:
    return [json.loads(line) for line in p.open(encoding="utf-8") if line.strip()] if p.exists() else []


def labels_note() -> str:
    has_32b = (config.DATA / "labels_32b.jsonl").exists()
    hl = human.load_labels(config.DATA / "human_labels.jsonl", config.DATA / "human_sample_key.jsonl")
    raw = _read_jsonl(config.DATA / "human_labels.jsonl")
    unblind = sum(not str(r.get("uid", "")).startswith("h-") for r in raw)
    parts = ["- **Labels:** Aya Expanse 8B (self-hosted vLLM, fp16 on 2xT4) for every item"]
    parts.append("; Aya Expanse 32B via the Cohere API on the golden-300 calibration slice" if has_32b
                 else "; the planned Aya Expanse 32B calibration slice was not run (no Cohere API key in the run)")
    if hl:
        blind = ("blind: labellers saw neither the item id nor its variant" if not unblind else
                 f"{unblind} of {len(raw)} label rows were made before the label tool was blinded")
        parts.append(f"; {len(hl)} hand-labelled items with the gate's own rubric ({blind}).")
    else:
        parts.append("; hand labels were not collected in time, so human agreement is not reported.")
    return "".join(parts)


def train_recipe() -> str:
    p = config.RUNS / config.RUN_NAME / "train_meta.json"
    meta = json.loads(p.read_text()) if p.exists() else {}
    r = meta.get("recipe", {})
    epochs = r.get("epochs", "?")
    rows = meta.get("train_rows")
    hours = f", {meta['train_seconds'] / 3600:.1f} h on one T4" if meta.get("train_seconds") else ""
    return (f"LoRA r={r.get('lora_r', 16)}, α={r.get('lora_alpha', 32)} on all attention and MLP projections; "
            f"lr {r.get('lr', 1e-4)} cosine, {epochs} epoch(s) over {rows or 'the'} training rows, effective batch "
            f"{r.get('batch_size', 1) * r.get('grad_accum', 16)} (1 x 16 accumulation), max "
            f"{r.get('max_len', config.MAX_SEQ_LEN):,} tokens (longer rows dropped, never truncated); fp16{hours}. "
            "Tiny Aya's 261k-token vocabulary makes the logits the memory peak on a 16 GB T4, hence batch 1.")


def teacher_audit() -> str:
    pool = {r["uid"]: r for r in _read_jsonl(config.DATA / "pool.jsonl")}
    labels = _read_jsonl(config.DATA / "labels.jsonl")
    if not pool or not labels:
        return "_Pending: teacher labels (forge/02_teacher_label.py)._"
    agg: dict[tuple[str, str], list[float]] = {}
    for r in labels:
        p = pool.get(r["uid"])
        if p and r.get("ok") and r.get("score") is not None:
            agg.setdefault((p["kind"], p["variant"]), []).append(float(r["score"]))
    rows = [[k, v, len(s), f"{sum(x >= config.THRESHOLD for x in s) / len(s):.0%}", f"{sum(s) / len(s):.2f}"]
            for (k, v), s in sorted(agg.items())]
    return ("### What the teacher lets through\n\nLorde learns the teacher's decisions, so its blind spots matter. "
            "Teacher pass rate (score at least 0.7) by item variant; every non-gold variant is degraded on purpose.\n\n"
            + table(["Gate", "Variant", "Items", "Teacher pass rate", "Mean score"], rows))


def roi(rows: int, gpu: str) -> str:
    p = config.RESULTS / "judge_eval.json"
    if not p.exists():
        return "_Pending: run forge/06_judge_eval.py._"
    r = json.loads(p.read_text())
    lorde = r["judges"].get("lorde") or {}
    perf, api = lorde.get("perf", {}), r.get("api_reference")
    jps = perf.get("judgments_per_s")
    if not (jps and api):
        return "_Pending: needs measured throughput and API token counts._"
    price = config.GPU_HOURLY[gpu]
    measured_price = lorde.get("gpu_hourly")
    judgments = rows * 2  # both gates judge every row
    hours = judgments / jps / 3600
    self_cost = hours * price
    api_cost = judgments * api["cost_per_1M_judgments"] / 1e6
    out = [f"Scenario: curating a **{rows:,}-row** dataset through both gates = **{judgments:,} judgments**.\n",
           table(["", "Aya Expanse 32B via Cohere API", f"Lorde on 1× {gpu.upper()} (HF Inference Endpoints)"],
                 [["Price", f"${api['price_per_1M_tokens']['input']}/1M in, ${api['price_per_1M_tokens']['output']}/1M out",
                   f"${price}/GPU-hour"],
                  ["Tokens per judgment", f"{api['mean_prompt_tokens']} in + {api['mean_completion_tokens']} out",
                   f"{perf.get('mean_prompt_tokens')} in + {perf.get('mean_completion_tokens')} out"],
                  ["Throughput", "–", f"{jps} judgments/s, {perf.get('completion_tokens_per_s')} output tokens/s "
                                      f"(measured, {perf.get('concurrency')} concurrent)"],
                  ["GPU runtime", "–", f"{hours:.2f} h"],
                  ["Cost for this dataset", f"${api_cost:,.2f}", f"${self_cost:,.2f}"],
                  ["Cost per 1M judgments", f"${api['cost_per_1M_judgments']:,.2f}",
                   f"${price / (jps * 3600) * 1e6:,.2f}"]]), "",
           f"**{api_cost / self_cost:.0f}× cheaper** at equal volume. "
           f"Throughput was measured on the GPU used for evaluation (${measured_price}/h reference); "
           "on an L4 expect higher throughput, so the L4 row is conservative if measured on a T4."]
    return "\n".join(out)


def tldr() -> str:
    p = config.RESULTS / "judge_eval.json"
    if not p.exists():
        return ""
    r = json.loads(p.read_text())
    j = r["judges"].get("lorde", {}).get("vs_teacher")
    b = r["judges"].get("base", {}).get("vs_teacher")
    if not j:
        return ""
    s = (f"Lorde's replies are **{j['gate_usable_rate'] * 100:.1f}%** gate-usable"
         + (f" (base Tiny Aya Global: {b['gate_usable_rate'] * 100:.1f}%)" if b else "")
         + f", it agrees with the teacher on **{j['decision_agreement'] * 100:.1f}%** of decisions "
           f"(κ = {j['kappa']:.2f})")
    c = r["judges"]["lorde"].get("cost_per_1M")
    a = r.get("api_reference", {}).get("cost_per_1M_judgments")
    if c and a:
        s += f", and it costs **${c:.2f} per 1M judgments** vs ${a:.2f} through the API"
    return s + "."


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=100_000)
    ap.add_argument("--gpu", choices=list(config.GPU_HOURLY), default="l4")
    args = ap.parse_args()
    t = TEMPLATE.read_text()
    t = t.replace("<!-- tldr -->", tldr())
    for name in ("judge_eval", "plugin_eval", "downstream"):
        t = t.replace(f"<!-- results:{name} -->", section(name))
    t = t.replace("<!-- roi -->", roi(args.rows, args.gpu))
    t = t.replace("<!-- labels_note -->", labels_note())
    t = t.replace("<!-- train_recipe -->", train_recipe())
    t = t.replace("<!-- teacher_audit -->", teacher_audit())
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "HF_ARTICLE.md").write_text(t, encoding="utf-8")
    print(f"wrote {DOCS / 'HF_ARTICLE.md'}")


if __name__ == "__main__":
    main()
