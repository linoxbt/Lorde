#!/usr/bin/env python
"""Run Lorde Bench against a judge endpoint; exit 1 if a blocking criterion fails (CI gate).

    python bench/run_bench.py --judge lorde=openai/lorde@http://localhost:8002/v1 --structured
    python bench/run_bench.py --judge lorde=... --baseline bench/results/lorde-v1.0.0.json   # regression

Golden-300: decision agreement and kappa vs the 8B teacher, vs Aya Expanse 32B and vs human labels
where present. Adversarial-100: pass rate on content that should fail (must be ≤ 5%).
Regression: decision agreement on golden must not drop more than 1 point vs the baseline result.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lorde_forge import config, jsonl, llm, metrics  # noqa: E402
from lorde_forge.judge import as_metric_row, predict  # noqa: E402
from lorde_forge.report import fmt, table  # noqa: E402

HERE = config.ROOT / "bench"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--judge", required=True, help="name=openai/model@base_url")
    ap.add_argument("--structured", action="store_true")
    ap.add_argument("--baseline", default=None, help="previous bench result JSON for the regression check")
    ap.add_argument("--concurrency", type=int, default=16)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    ep = llm.Endpoint.parse(args.judge)
    name = ep.name + ("+so" if args.structured else "")
    golden = jsonl.read(HERE / "data" / "golden-300.jsonl")
    adv = jsonl.read(HERE / "data" / "adversarial-100.jsonl")
    if not golden:
        sys.exit("no bench sets: run bench/build_sets.py")

    pg = predict(ep, golden, args.structured, None, args.concurrency)
    pa = predict(ep, adv, args.structured, None, args.concurrency) if adv else {}
    rows = [as_metric_row(pg.get(g["uid"])) for g in golden]
    res: dict = {"judge": name, "model": ep.model, "n_golden": len(golden), "n_adversarial": len(adv)}
    res["vs_teacher_8b"] = metrics.compare(rows, [{"ok": True, "score": g["teacher_score"],
                                                   "verdict": g.get("teacher_verdict")} for g in golden])
    for key, sk, vk in (("vs_32b", "ref_32b_score", "ref_32b_verdict"), ("vs_human", "human_score", "human_verdict")):
        idx = [i for i, g in enumerate(golden) if g.get(sk) is not None]
        if idx:
            res[key] = metrics.compare([rows[i] for i in idx],
                                       [{"ok": True, "score": golden[i][sk], "verdict": golden[i].get(vk)} for i in idx])
    if adv:
        dec = [bool(p.get("ok") and p.get("score") is not None and p["score"] >= config.THRESHOLD)
               for p in (pa.get(a["uid"], {}) for a in adv)]
        res["adversarial_pass_rate"] = round(sum(dec) / len(dec), 4)
        res["adversarial_by_attack"] = {}
        for attack in sorted({a["attack"] for a in adv}):
            ix = [i for i, a in enumerate(adv) if a["attack"] == attack]
            res["adversarial_by_attack"][attack] = round(sum(dec[i] for i in ix) / len(ix), 4)

    checks = [("Golden decision agreement vs teacher ≥ 90%", res["vs_teacher_8b"]["decision_agreement"] >= 0.90),
              ("Golden κ vs teacher ≥ 0.75", (res["vs_teacher_8b"]["kappa"] or 0) >= 0.75)]
    if args.structured:
        checks.append(("Gate-usable with structured outputs = 100%", res["vs_teacher_8b"]["gate_usable_rate"] >= 1.0))
    else:
        checks.append(("Gate-usable unconstrained ≥ 99%", res["vs_teacher_8b"]["gate_usable_rate"] >= 0.99))
    if adv:
        checks.append(("Adversarial pass rate ≤ 5%", res["adversarial_pass_rate"] <= 0.05))
    if args.baseline and Path(args.baseline).exists():
        prev = json.loads(Path(args.baseline).read_text())["vs_teacher_8b"]["decision_agreement"]
        res["baseline_decision_agreement"] = prev
        checks.append(("No regression: agreement drop ≤ 1 point",
                       res["vs_teacher_8b"]["decision_agreement"] >= prev - 0.01))
    res["checks"] = [{"criterion": c, "met": ok} for c, ok in checks]
    res["passed"] = all(ok for _, ok in checks)

    out = Path(args.out) if args.out else HERE / "results" / f"{name}.json"
    jsonl.write_json(out, res)
    md = [f"# Lorde Bench · {name}\n",
          table(["Reference", "n", "Gate-usable", "Decision agr.", "κ", "MAE"],
                [[k, v["n"], fmt(v["gate_usable_rate"], True), fmt(v["decision_agreement"], True), fmt(v["kappa"]),
                  fmt(v["mae"])] for k, v in res.items() if k.startswith("vs_")]), ""]
    if adv:
        md += [f"Adversarial-100 pass rate: **{fmt(res['adversarial_pass_rate'], True)}** "
               f"({', '.join(f'{k} {fmt(v, True)}' for k, v in res['adversarial_by_attack'].items())})\n"]
    md += [table(["Check", "Met"], [[c["criterion"], fmt(c["met"])] for c in res["checks"]]), ""]
    out.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    sys.exit(0 if res["passed"] else 1)


if __name__ == "__main__":
    main()
