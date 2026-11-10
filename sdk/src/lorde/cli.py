"""``lorde`` command line: judge items from the shell, or run the stdio MCP server."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _text(v: str) -> str:
    """A literal string, or the contents of a file if the value names one."""
    p = Path(v)
    return p.read_text(encoding="utf-8") if len(v) < 4096 and p.is_file() else v


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lorde", description="Lorde: a 3B judge for CuratorKIT's quality gates")
    ap.add_argument("--api-key")
    ap.add_argument("--base-url")
    sub = ap.add_subparsers(dest="cmd", required=True)
    j = sub.add_parser("judge", help="judge one item")
    js = j.add_subparsers(dest="kind", required=True)
    g = js.add_parser("grounding")
    g.add_argument("--source", required=True, help="source text or a file containing it")
    g.add_argument("--question", required=True)
    g.add_argument("--answer", required=True)
    g.add_argument("--threshold", type=float)
    r = js.add_parser("reward")
    r.add_argument("--instruction", required=True)
    r.add_argument("--response", required=True, help="response text or a file containing it")
    r.add_argument("--threshold", type=float)
    jb = sub.add_parser("jobs", help="gating jobs over whole datasets")
    jbs = jb.add_subparsers(dest="action", required=True)
    jc = jbs.add_parser("create", help="upload a dataset and gate it")
    jc.add_argument("dataset", help="local .jsonl/.csv/.parquet file, or hf://datasets/<owner>/<name>")
    jc.add_argument("--grounding", type=float, default=0.7)
    jc.add_argument("--reward", type=float, default=0.7)
    jc.add_argument("--no-grounding", action="store_true")
    jc.add_argument("--no-reward", action="store_true")
    jc.add_argument("--pii", action="store_true", help="pseudonymize PII")
    jc.add_argument("--dedup", choices=["exact", "minhash", "none"], default="exact")
    jc.add_argument("--wait", action="store_true")
    jc.add_argument("--out", help="download results here when the job succeeds (implies --wait)")
    for name in ("status", "cancel"):
        x = jbs.add_parser(name)
        x.add_argument("job_id")
    jd = jbs.add_parser("download")
    jd.add_argument("job_id")
    jd.add_argument("--out", required=True)
    sub.add_parser("mcp", help="run the stdio MCP server (needs lorde[mcp])")
    a = ap.parse_args(argv)
    if a.cmd == "jobs":
        return jobs_cmd(a)

    if a.cmd == "mcp":
        from .mcp import serve

        serve(a.api_key, a.base_url)
        return 0

    from . import Lorde, LordeAPIError

    try:
        with Lorde(api_key=a.api_key, base_url=a.base_url) as c:
            if a.kind == "grounding":
                res = c.judge.grounding(_text(a.source), a.question, _text(a.answer), a.threshold)
            else:
                res = c.judge.reward(a.instruction, _text(a.response), a.threshold)
    except (LordeAPIError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(res.model_dump(), ensure_ascii=False, indent=2))
    return 0 if res.decision == "pass" else 1


def jobs_cmd(a) -> int:
    from . import Lorde, LordeAPIError

    try:
        with Lorde(api_key=a.api_key, base_url=a.base_url) as c:
            if a.action == "create":
                gates = {"grounding": None if a.no_grounding else a.grounding,
                         "reward": None if a.no_reward else a.reward}
                remote = a.dataset.startswith("hf://")
                job = c.jobs.create(None if remote else a.dataset, dataset_url=a.dataset if remote else None,
                                    gates=gates, pii=a.pii, dedup=a.dedup)
                print(f"job {job.id} {job.status}", file=sys.stderr)
                if a.wait or a.out:
                    job.wait()
                    print(f"job {job.id} {job.status}", file=sys.stderr)
                    if job.status == "succeeded" and a.out:
                        for name, p in job.download(a.out).items():
                            print(f"  {name}: {p}", file=sys.stderr)
            elif a.action == "status":
                job = c.jobs.get(a.job_id)
            elif a.action == "cancel":
                job = c.jobs.get(a.job_id).cancel()
            else:
                job = c.jobs.get(a.job_id)
                for name, p in job.download(a.out).items():
                    print(f"  {name}: {p}", file=sys.stderr)
            print(json.dumps(job.data, indent=2, default=str))
            return 0 if job.status in ("succeeded", "queued", "running", "cancelled") else 1
    except (LordeAPIError, ValueError, TimeoutError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
