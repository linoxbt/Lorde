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
    sub.add_parser("mcp", help="run the stdio MCP server (needs lorde[mcp])")
    a = ap.parse_args(argv)

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


if __name__ == "__main__":
    sys.exit(main())
