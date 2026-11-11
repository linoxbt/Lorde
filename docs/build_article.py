#!/usr/bin/env python
"""Fill docs/HF_ARTICLE.md from the template and the measured results in results/.

    python docs/build_article.py [--rows 100000] [--gpu t4|l4]

Only measured numbers go into the article; sections whose results are missing say so. Throughput is
reported for the GPU it was measured on (judge_eval.json "hardware"); ``--gpu`` only supplies the hourly
price when the run recorded none, and it must match the measured GPU.
"""

from __future__ import annotations

import argparse
import json
import re
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
    hw = meta.get("hardware")
    hours = (f", {meta['train_seconds'] / 3600:.1f} h on one {hw}" if hw else
             f", {meta['train_seconds'] / 3600:.1f} h of training") if meta.get("train_seconds") else ""
    prec = f"; {meta['precision']}" if meta.get("precision") else ""
    return (f"LoRA r={r.get('lora_r', 16)}, α={r.get('lora_alpha', 32)} on all attention and MLP projections; "
            f"lr {r.get('lr', 1e-4)} cosine, {epochs} epoch(s) over {rows or 'the'} training rows, effective batch "
            f"{r.get('batch_size', 1) * r.get('grad_accum', 16)} (1 x 16 accumulation), max "
            f"{r.get('max_len', config.MAX_SEQ_LEN):,} tokens (longer rows dropped, never truncated){prec}{hours}. "
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


def gpu_class(name: str | None) -> str | None:
    """'t4' for "Tesla T4", 'l4' for "NVIDIA L4", None for anything else."""
    tokens = re.split(r"[^A-Z0-9]+", (name or "").upper())
    return next((g for g in config.GPU_HOURLY if g.upper() in tokens), None)


def roi(rows: int, gpu: str | None) -> str:
    p = config.RESULTS / "judge_eval.json"
    if not p.exists():
        return "_Pending: run forge/06_judge_eval.py._"
    r = json.loads(p.read_text())
    lorde = r["judges"].get("lorde") or {}
    perf, api = lorde.get("perf", {}), r.get("api_reference")
    jps = perf.get("judgments_per_s")
    if not (jps and api):
        return "_Pending: needs measured throughput and API token counts._"
    hw = (r.get("hardware") or {}).get("name")
    measured = gpu_class(hw)
    if gpu and measured and gpu != measured:
        return (f"_Not shown: throughput was measured on {hw}; a {gpu.upper()} price would describe hardware "
                "that was not measured. Re-run with --gpu " + measured + "._")
    price = lorde.get("gpu_hourly") or (config.GPU_HOURLY.get(measured or gpu or "") if (measured or gpu) else None)
    if not price:
        return "_Pending: no GPU price for the measured hardware (pass --gpu-hourly to step 6)._"
    where = f"1× {hw}" if hw else "the evaluation GPU (not recorded by this run)"
    judgments = rows * 2  # both gates judge every row
    hours = judgments / jps / 3600
    self_cost = hours * price
    api_cost = judgments * api["cost_per_1M_judgments"] / 1e6
    smp = perf.get("sample")
    how = ("stratified random sample, timed before any prediction" if smp else
           "first test items after the predictions (warm cache, mostly short reward items: optimistic)")
    out = [f"Scenario: curating a **{rows:,}-row** dataset through both gates = **{judgments:,} judgments**.\n",
           table(["", "Aya Expanse 32B via Cohere API", f"Lorde on {where} (measured)"],
                 [["Price", f"${api['price_per_1M_tokens']['input']}/1M in, ${api['price_per_1M_tokens']['output']}/1M out",
                   f"${price}/GPU-hour"],
                  ["Tokens per judgment", f"{api['mean_prompt_tokens']} in + {api['mean_completion_tokens']} out",
                   f"{perf.get('mean_prompt_tokens')} in + {perf.get('mean_completion_tokens')} out"],
                  ["Throughput", "–", f"{jps} judgments/s, {perf.get('completion_tokens_per_s')} output tokens/s "
                                      f"(measured, {perf.get('concurrency')} concurrent; {how})"],
                  ["GPU runtime", "–", f"{hours:.2f} h"],
                  ["Cost for this dataset", f"${api_cost:,.2f}", f"${self_cost:,.2f}"],
                  ["Cost per 1M judgments", f"${api['cost_per_1M_judgments']:,.2f}",
                   f"${price / (jps * 3600) * 1e6:,.2f}"]]), "",
           f"**{api_cost / self_cost:.0f}× cheaper** at equal volume, on the hardware the throughput was measured on "
           f"({where}, ${price}/h). Other GPUs will differ; we did not measure them."]
    return "\n".join(out)


def acceptance_md() -> str:
    p = config.RESULTS / "judge_eval.json"
    if not p.exists():
        return ""
    r = json.loads(p.read_text())
    crit = r.get("acceptance") or []
    if not crit:
        return ""
    prom = r.get("promotable")
    if prom is None:  # report from before the explicit flag: derive it the same way
        blocking = [c for c in crit if c.get("blocking")]
        prom = bool(blocking) and all(c.get("met") is True for c in blocking)
    unmet = [c["criterion"] for c in crit if c.get("blocking") and c.get("met") is not True]
    return (f"**Promotion verdict (spec 9.2): {'promotable' if prom else 'not promotable'}.**"
            + ("" if prom else f" Blocking criteria not met or not measured: {'; '.join(unmet)}.") + "\n")


def tldr() -> str:
    p = config.RESULTS / "judge_eval.json"
    if not p.exists():
        return ""
    r = json.loads(p.read_text())
    j = r["judges"].get("lorde", {}).get("vs_teacher")
    b = r["judges"].get("base", {}).get("vs_teacher")
    if not j:
        return ""
    kappa = f" (κ = {j['kappa']:.2f})" if j.get("kappa") is not None else " (κ undefined)"
    s = (f"Lorde's replies are **{j['gate_usable_rate'] * 100:.1f}%** gate-usable"
         + (f" (base Tiny Aya Global: {b['gate_usable_rate'] * 100:.1f}%)" if b else "")
         + f", it agrees with the teacher on **{j['decision_agreement'] * 100:.1f}%** of decisions{kappa}")
    c = r["judges"]["lorde"].get("cost_per_1M")
    a = r.get("api_reference", {}).get("cost_per_1M_judgments")
    if c and a:
        hw = (r.get("hardware") or {}).get("name")
        s += f", and it costs **${c:.2f} per 1M judgments**{f' on {hw}' if hw else ''} vs ${a:.2f} through the API"
    return s + "."


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=100_000)
    ap.add_argument("--gpu", choices=list(config.GPU_HOURLY), default=None,
                    help="price class when judge_eval.json has no gpu_hourly; must match the measured GPU")
    args = ap.parse_args()
    t = TEMPLATE.read_text()
    t = t.replace("<!-- tldr -->", tldr())
    for name in ("judge_eval", "plugin_eval", "downstream"):
        t = t.replace(f"<!-- results:{name} -->", section(name))
    t = t.replace("<!-- roi -->", roi(args.rows, args.gpu))
    t = t.replace("<!-- acceptance -->", acceptance_md())
    t = t.replace("<!-- labels_note -->", labels_note())
    t = t.replace("<!-- train_recipe -->", train_recipe())
    t = t.replace("<!-- teacher_audit -->", teacher_audit())
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "HF_ARTICLE.md").write_text(t, encoding="utf-8")
    print(f"wrote {DOCS / 'HF_ARTICLE.md'}")


if __name__ == "__main__":
    main()
