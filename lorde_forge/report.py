"""Markdown helpers for results/*.md and the HF Article."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def fmt(v: Any, pct: bool = False) -> str:
    if v is None:
        return "–"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v * 100:.1f}%" if pct else f"{v:.3f}"
    return str(v)


def table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    h = "| " + " | ".join(headers) + " |"
    sep = "|" + "|".join("---" for _ in headers) + "|"
    body = ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join([h, sep, *body])
