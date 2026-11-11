"""Canonical training targets: the teacher's reply re-serialized to the gate's keys.

The student learns short, strict JSON with exactly the keys the gate reads.
Long text is clipped so every target also satisfies the structured-output schema.
"""

from __future__ import annotations

import json
from typing import Any

import jsonschema

from .config import THRESHOLD
from .contract import Judgment
from .schemas import CLAIM_MAX, CLAIMS_MAX, REWARD_DIMENSIONS, SCHEMAS, TEXT_MAX, VERDICTS


def _unit(x: Any) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    x = float(x)
    if not 0.0 <= x <= 1.0:
        return None
    return round(x, 2)


def _clip(s: Any, n: int) -> str:
    s = "" if s is None else (s if isinstance(s, str) else json.dumps(s, ensure_ascii=False))
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _claims(v: Any) -> list[str]:
    if isinstance(v, str):
        v = [v] if v.strip() and v.strip() != "..." else []
    if not isinstance(v, list):
        return []
    out = [_clip(c, CLAIM_MAX) for c in v if isinstance(c, (str, int, float)) and str(c).strip()]
    return [c for c in out if c != "..."][:CLAIMS_MAX]


def verdict_from_score(score: float) -> str:
    if score >= THRESHOLD:
        return "grounded"
    if score >= 0.4:
        return "partially_grounded"
    return "hallucinated"


def canonical(j: Judgment) -> dict[str, Any] | None:
    """Canonical target dict for a gate-usable judgment, or None if not usable."""
    if not j.ok or j.score is None:
        return None
    p = j.parsed if isinstance(j.parsed, dict) else {}
    score = round(j.score, 2)
    if j.kind == "reward":
        raw_dims = p.get("dimension_scores") if isinstance(p.get("dimension_scores"), dict) else p
        dims = {}
        for d in REWARD_DIMENSIONS:
            v = _unit(raw_dims.get(d)) if isinstance(raw_dims, dict) else None
            dims[d] = score if v is None else v
        out = {
            "overall_score": score,
            "dimension_scores": dims,
            "strengths": _clip(p.get("strengths", ""), TEXT_MAX),
            "weaknesses": _clip(p.get("weaknesses", ""), TEXT_MAX),
        }
    else:
        verdict = p.get("verdict")
        if verdict not in VERDICTS:
            verdict = verdict_from_score(score)
        out = {
            "grounding_score": score,
            "supported_claims": _claims(p.get("supported_claims")),
            "unsupported_claims": _claims(p.get("unsupported_claims")),
            "verdict": verdict,
        }
    jsonschema.validate(out, SCHEMAS[j.kind])
    return out


def dumps(target: dict[str, Any]) -> str:
    """Compact, stable serialization used as the assistant turn."""
    return json.dumps(target, ensure_ascii=False, separators=(", ", ": "))


def reference_verdict(item: dict[str, Any]) -> str | None:
    """The teacher's verdict as the student was trained to give it (the canonical target's verdict).

    Test items written by the fixed step 3 carry it as ``teacher_verdict`` (with ``teacher_verdict_raw``
    next to it); older ones carry the raw teacher verdict, so it is re-derived from ``teacher_raw``.
    """
    if item.get("kind") != "grounding":
        return None
    if "teacher_verdict_raw" in item or item.get("teacher_raw") is None:
        return item.get("teacher_verdict")
    from .contract import parse_reply

    target = canonical(parse_reply("grounding", item["teacher_raw"]))
    return target["verdict"] if target else item.get("teacher_verdict")
