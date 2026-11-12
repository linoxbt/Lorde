#!/usr/bin/env python
"""Step 7: plug each judge into a real CuratorKIT run on the downstream raw set.

    python forge/07_curatorkit_plugin.py \
        --judge base=openai/student@http://localhost:8001/v1 \
        --judge lorde=openai/lorde@http://localhost:8002/v1
    python forge/07_curatorkit_plugin.py --judge teacher=openai/teacher@http://localhost:8000/v1

CuratorKIT runs exactly as a user would: RewardGate and HallucinationGate at 0.7,
``judge_on_error="reject"``, ``judge_llm`` pointed at the judge's OpenAI-compatible
endpoint. ``--structured`` adds vLLM structured outputs per gate through
CuratorKIT's per-role ``reward_llm`` / ``hallucination_llm`` overrides
(``extra_body.response_format``), so each gate gets its own schema.

Writes data/gated/<judge>/ (CuratorKIT outputs, manifest, lexsi_provenance.json,
plus passed.jsonl for step 8, with its sidecar passed.meta.json) and results/plugin_eval.{json,md}.

Runs of different judges are merged into one report, so every judge entry records the sha256 of the raw set it
gated (``raw_sha``, order-independent over instruction/input/output) and the threshold. Entries from another raw
set or threshold are dropped when the report is merged (their passed_keys would describe other rows); entries
written before this fingerprint existed are dropped too unless ``--trust-legacy-entries`` keeps them (marked
``legacy_trusted``). data/gated/<judge>/passed.meta.json records the same fingerprint for step 8.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lorde_forge import config, fingerprint, jsonl, llm, metrics  # noqa: E402
from lorde_forge.report import fmt, table  # noqa: E402
from lorde_forge.schemas import response_format  # noqa: E402


def key(instruction: str, output: str) -> str:
    return hashlib.sha256(f"{instruction.strip()}\x00{output.strip()}".encode()).hexdigest()


RAW_FIELDS = ("instruction", "input", "output")


def raw_sha(raw: list[dict]) -> str:
    """Identity of the raw set a judge gated (order-independent)."""
    return fingerprint.rows_sha(raw, RAW_FIELDS)


def merge_report(old: dict, sha: str, threshold: float, trust_legacy: bool = False) -> dict:
    """The previous report's judge entries that describe this raw set and threshold; the rest are dropped."""
    keep = {}
    for name, j in (old.get("judges") or {}).items():
        if j.get("raw_sha") is None:
            if trust_legacy:
                keep[name] = {**j, "legacy_trusted": True}
                print(f"[plugin] keeping {name}: written before raw-set fingerprints (--trust-legacy-entries)")
            else:
                print(f"[plugin] dropping {name}: no raw-set fingerprint (re-run it, or --trust-legacy-entries)")
            continue
        if j["raw_sha"] != sha:
            print(f"[plugin] dropping {name}: it gated another raw set ({j['raw_sha'][:12]} != {sha[:12]})")
            continue
        if j.get("threshold") is not None and j["threshold"] != threshold:
            print(f"[plugin] dropping {name}: threshold {j['threshold']} != {threshold}")
            continue
        keep[name] = j
    return {**old, "judges": keep}


def run_curator(name: str, ep: llm.Endpoint, raw_path: Path, out_dir: Path, args) -> dict:
    from curatorkit import Curator, CuratorConfig

    judge = {"model": f"openai/{ep.model}", "api_base": ep.base_url, "api_key": ep.api_key,
             "temperature": config.JUDGE_TEMPERATURE, "max_tokens": config.JUDGE_MAX_TOKENS,
             "concurrency": args.concurrency}
    extra = {}
    if args.structured:
        extra = {"reward_llm": {"extra_body": {"response_format": response_format("reward")}},
                 "hallucination_llm": {"extra_body": {"response_format": response_format("grounding")}}}
    cfg = CuratorConfig(
        dataset=str(raw_path),
        output_dir=str(out_dir),
        name=f"lorde_plugin_{name}",
        hallucination_threshold=args.threshold,
        reward_threshold=args.threshold,
        judge_llm=judge,
        judge_on_error="reject",
        judge_concurrency=args.concurrency,
        dedup="none",
        clean=False,
        export_formats=["alpaca"],
        **extra,
    )
    t0 = time.time()
    result = Curator(cfg).run()
    wall = time.time() - t0
    return {"result": result, "wall_s": round(wall, 1)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--judge", action="append", required=True, help="name=openai/model@base_url")
    ap.add_argument("--raw", default=str(config.DATA / "downstream_raw.jsonl"))
    ap.add_argument("--threshold", type=float, default=config.THRESHOLD)
    ap.add_argument("--structured", action="store_true")
    ap.add_argument("--concurrency", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=str(config.RESULTS / "plugin_eval"))
    ap.add_argument("--trust-legacy-entries", action="store_true",
                    help="keep judge entries of the existing report that have no raw-set fingerprint")
    args = ap.parse_args()

    raw = jsonl.read(args.raw)
    if args.limit:
        raw = raw[: args.limit]
    if not raw:
        sys.exit("empty downstream raw set: run forge/01_build_pool.py --phase assemble")
    by_key = {key(r["instruction"], r["output"]): r for r in raw}

    gated_root = config.DATA / "gated"
    # CuratorKIT sees only the fields a user's dataset would have.
    cur_in = gated_root / "_input" / "downstream_raw.jsonl"
    jsonl.write(cur_in, ({"instruction": r["instruction"], "input": r["input"], "output": r["output"]} for r in raw))
    sha = raw_sha(raw)
    jsonl.write(gated_root / "ungated" / "passed.jsonl", raw)
    fingerprint.write_meta(gated_root / "ungated" / "passed.jsonl",
                           {"judge": "ungated", "raw_sha": sha, "n_raw": len(raw), "n_passed": len(raw)})

    results_path = Path(f"{args.out}.json")
    old = json.loads(results_path.read_text()) if results_path.exists() else {"judges": {}}
    report = merge_report(old, sha, args.threshold, args.trust_legacy_entries)
    report["raw_sha"] = sha
    report["n_raw"] = len(raw)
    report["raw_variants"] = dict(Counter(r["variant"] for r in raw))

    for spec in args.judge:
        ep = llm.Endpoint.parse(spec)
        name = ep.name + ("+so" if args.structured else "")
        out_dir = gated_root / name
        print(f"[plugin] CuratorKIT run with judge {name} ({ep.model} @ {ep.base_url})")
        run = run_curator(name, ep, cur_in, out_dir, args)
        res = run["result"]
        passed_keys = set()
        passed_rows = []
        for s in res.passed:
            k = key(s.instruction, s.output)
            if k in by_key:
                passed_keys.add(k)
                passed_rows.append(by_key[k])
        jsonl.write(out_dir / "passed.jsonl", passed_rows)
        fingerprint.write_meta(out_dir / "passed.jsonl", {
            "judge": name, "model": ep.model, "base_url": ep.base_url, "structured": args.structured,
            "threshold": args.threshold, "raw_sha": sha, "n_raw": len(raw), "n_passed": len(passed_rows),
            "passed_sha": fingerprint.rows_sha(passed_rows, ("uid", *RAW_FIELDS))})
        reasons = Counter((r.rejection_reason or "?").split(":")[0] if not (r.rejection_reason or "").startswith(
            "judge_error") else r.rejection_reason for r in res.rejected)
        n_judge_err = sum(v for k, v in reasons.items() if k.startswith("judge_error"))
        by_variant = {}
        for v in sorted({r["variant"] for r in raw}):
            ks = [k for k, r in by_key.items() if r["variant"] == v]
            by_variant[v] = round(sum(k in passed_keys for k in ks) / max(1, len(ks)), 4)
        report["judges"][name] = {
            "model": ep.model, "passed": len(res.passed), "rejected": len(res.rejected),
            "pass_rate": round(len(res.passed) / max(1, len(raw)), 4),
            "judge_error_rate": round(n_judge_err / max(1, len(raw)), 4),
            "rejection_reasons": dict(reasons), "pass_rate_by_variant": by_variant,
            "stage_counts": res.stage_counts, "wall_s": run["wall_s"],
            "gold_pass_rate": by_variant.get("gold"),
            "passed_keys": sorted(passed_keys),
            "raw_sha": sha, "threshold": args.threshold, "structured": args.structured,
        }
        print(f"[plugin] {name}: passed {len(res.passed)}/{len(raw)}, judge errors {n_judge_err}, "
              f"{run['wall_s']} s")

    # Pairwise agreement of pass/reject decisions between judges (teacher is the reference when present).
    names = list(report["judges"])
    agree = {}
    for a in names:
        for b in names:
            if a < b:
                pa, pb = set(report["judges"][a]["passed_keys"]), set(report["judges"][b]["passed_keys"])
                da = [k in pa for k in by_key]
                db = [k in pb for k in by_key]
                agree[f"{a} vs {b}"] = {"decision_agreement": round(sum(x == y for x, y in zip(da, db)) / len(da), 4),
                                        "kappa": metrics.cohen_kappa(da, db)}
    report["pairwise"] = agree
    jsonl.write_json(results_path, report)
    Path(f"{args.out}.md").write_text(render_md(report), encoding="utf-8")
    print(f"[plugin] wrote {args.out}.md / .json")


def render_md(r: dict) -> str:
    js = r["judges"]
    var = sorted(r.get("raw_variants", {}))
    lines = [f"# CuratorKIT plug-in run\n\nDownstream raw set: **{r['n_raw']}** grounded QA items "
             f"({', '.join(f'{k}: {v}' for k, v in r.get('raw_variants', {}).items())}). Each judge gated it "
             "through RewardGate and HallucinationGate at 0.7 inside a real CuratorKIT run.\n",
             table(["Judge", "Passed", "Pass rate", "Judge-error rate", "Run time (s)"],
                   [[n, j["passed"], fmt(j["pass_rate"], True), fmt(j["judge_error_rate"], True), j["wall_s"]]
                    for n, j in js.items()]), "",
             "## Pass rate by variant\n",
             table(["Judge", *var], [[n, *(fmt(j["pass_rate_by_variant"].get(v), True) for v in var)]
                                     for n, j in js.items()]), "",
             "## Rejection reasons\n",
             table(["Judge", "Reasons"], [[n, ", ".join(f"{k}: {v}" for k, v in j["rejection_reasons"].items())]
                                          for n, j in js.items()]), ""]
    if r.get("pairwise"):
        lines += ["## Pairwise decision agreement\n",
                  table(["Pair", "Agreement", "κ"], [[k, fmt(v["decision_agreement"], True), fmt(v["kappa"])]
                                                     for k, v in r["pairwise"].items()]), ""]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
