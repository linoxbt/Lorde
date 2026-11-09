#!/usr/bin/env python
"""Step 2: label the pool with the teacher, using CuratorKIT's exact gate prompts.

    # bulk labels: Aya Expanse 8B on local vLLM
    python forge/02_teacher_label.py --model openai/teacher --base http://localhost:8000/v1 --workers 32

    # calibration slice: Aya Expanse 32B through the Cohere Compatibility API
    python forge/02_teacher_label.py --model c4ai-aya-expanse-32b \
        --base https://api.cohere.ai/compatibility/v1 --api-key-env COHERE_API_KEY \
        --sample 300 --max-calls 900 --workers 4 --out data/labels_32b.jsonl

Rows are appended as they arrive, so the step resumes after a crash. Replies are
parsed by CuratorKIT's parser; ``ok`` means gate-usable.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lorde_forge import config, contract, jsonl, llm, splits  # noqa: E402


def label_row(item: dict, res: llm.Result, teacher: str) -> dict:
    j = contract.parse_reply(item["kind"], res.text)
    return {
        "uid": item["uid"],
        "kind": item["kind"],
        "teacher": teacher,
        "raw": res.text,
        "ok": j.ok,
        "score": j.score,
        "verdict": j.verdict,
        "parsed": j.parsed if j.ok else None,
        "used_fallback": j.used_fallback,
        "parse_error": None if j.ok or res.text is None else j.error,
        "latency_s": round(res.latency_s, 4),
        "prompt_tokens": res.prompt_tokens,
        "completion_tokens": res.completion_tokens,
        "call_error": res.error,
    }


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    called = [r for r in rows if not r.get("call_error")]
    ok = [r for r in called if r["ok"]]
    bins = Counter("low" if r["score"] < 0.4 else "mid" if r["score"] < config.THRESHOLD else "high" for r in ok)
    return {
        "n": n,
        "call_errors": n - len(called),
        "parse_ok_rate": round(len(ok) / len(called), 4) if called else None,
        "score_bins": dict(bins),
        "pass_rate": round(sum(r["score"] >= config.THRESHOLD for r in ok) / len(ok), 4) if ok else None,
        "mean_prompt_tokens": round(sum(r["prompt_tokens"] for r in called) / len(called), 1) if called else None,
        "mean_completion_tokens": round(sum(r["completion_tokens"] for r in called) / len(called), 1)
        if called else None,
        "by_kind_parse_ok": {
            k: round(sum(r["ok"] for r in called if r["kind"] == k) / max(1, sum(r["kind"] == k for r in called)), 4)
            for k in contract.KINDS
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="openai/teacher")
    ap.add_argument("--base", default="http://localhost:8000/v1")
    ap.add_argument("--api-key-env", default="VLLM_API_KEY")
    ap.add_argument("--pool", default=str(config.DATA / "pool.jsonl"))
    ap.add_argument("--out", default=str(config.DATA / "labels.jsonl"))
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--sample", type=int, default=0, help="label a stratified sample of N items (calibration)")
    ap.add_argument("--sample-from", default=None,
                    help="restrict the sample to uids in this JSONL (e.g. data/test.jsonl)")
    ap.add_argument("--max-calls", type=int, default=0, help="hard cap on API calls this run (0 = none)")
    ap.add_argument("--retry-errors", action="store_true", help="re-call items whose earlier call failed")
    args = ap.parse_args()

    pool = jsonl.read(args.pool)
    if not pool:
        sys.exit(f"empty pool: {args.pool} (run forge/01_build_pool.py first)")
    if args.sample_from:
        keep = {r["uid"] for r in jsonl.read(args.sample_from)}
        pool = [r for r in pool if r["uid"] in keep]
    if args.sample:
        pool = splits.stratified_sample(pool, args.sample, keys=("kind", "lang"), salt="calibration")

    out_path = Path(args.out)
    if args.retry_errors and out_path.exists():
        kept = [r for r in jsonl.read(out_path) if not r.get("call_error")]
        jsonl.write(out_path, kept)

    ep = llm.Endpoint.parse(f"teacher={args.model}@{args.base}", api_key_env=args.api_key_env)
    by_uid = {r["uid"]: r for r in pool}
    with jsonl.Appender(out_path) as out:
        todo = [r for r in pool if r["uid"] not in out]
        if args.max_calls:
            todo = todo[: args.max_calls]
        print(f"[label] {len(todo)} to label with {ep.model} ({len(out.done)} cached) -> {out_path}")
        reqs = [llm.Request(uid=r["uid"], messages=contract.messages(r), params=llm.judge_params())
                for r in todo]
        if reqs:
            llm.run(ep, reqs, concurrency=args.workers, desc="teacher",
                    on_result=lambda res: out.add(label_row(by_uid[res.uid], res, ep.model)))

    rows = [r for r in jsonl.read(out_path) if r["uid"] in by_uid]
    s = summarize(rows)
    s["teacher"] = ep.model
    s["templates"] = contract.fingerprints()
    jsonl.write_json(config.RESULTS / f"{out_path.stem}_summary.json", s)
    print(f"[label] summary: {s}")
    if s["parse_ok_rate"] is not None and s["parse_ok_rate"] < 0.95:
        print("[label] WARNING: parse-OK below 95% exit check; inspect parse_error in", out_path)
    if len(s["score_bins"]) < 3:
        print("[label] WARNING: score bins do not span low/mid/high; degradations may be too easy")


if __name__ == "__main__":
    main()
