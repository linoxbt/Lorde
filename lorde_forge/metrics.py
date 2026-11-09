"""Lorde Bench metrics (spec Section 9.1)."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from .config import THRESHOLD


def _safe(x: float) -> float | None:
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(x, 4)


def cohen_kappa(a: Sequence[bool], b: Sequence[bool]) -> float | None:
    n = len(a)
    if n == 0:
        return None
    po = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe >= 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    if len(x) < 3:
        return None
    from scipy.stats import spearmanr

    r = spearmanr(x, y).statistic
    return None if r is None or math.isnan(r) else float(r)


def compare(
    pred: Sequence[dict[str, Any]],
    ref: Sequence[dict[str, Any]],
    threshold: float = THRESHOLD,
) -> dict[str, Any]:
    """Compare aligned judge outputs.

    Each row: ``{"ok": bool, "score": float|None, "verdict": str|None}``.
    Unparseable = reject (CuratorKIT's default ``judge_on_error``).
    """
    assert len(pred) == len(ref)
    n = len(pred)
    if n == 0:
        return {"n": 0}
    dp = [bool(p["ok"] and p["score"] is not None and p["score"] >= threshold) for p in pred]
    dr = [bool(r["ok"] and r["score"] is not None and r["score"] >= threshold) for r in ref]
    both = [(p["score"], r["score"]) for p, r in zip(pred, ref) if p["ok"] and r["ok"]]
    vpairs = [(p.get("verdict"), r.get("verdict")) for p, r in zip(pred, ref)
              if r.get("verdict") is not None]
    out = {
        "n": n,
        "gate_usable_rate": sum(bool(p["ok"]) for p in pred) / n,
        "decision_agreement": sum(x == y for x, y in zip(dp, dr)) / n,
        "kappa": cohen_kappa(dp, dr),
        "pass_rate": sum(dp) / n,
        "ref_pass_rate": sum(dr) / n,
        "spearman": spearman([a for a, _ in both], [b for _, b in both]) if both else None,
        "mae": (sum(abs(a - b) for a, b in both) / len(both)) if both else None,
        "verdict_accuracy": (sum(a == b for a, b in vpairs) / len(vpairs)) if vpairs else None,
        "n_verdict": len(vpairs),
    }
    return {k: (_safe(v) if isinstance(v, float) else v) for k, v in out.items()}


def grouped(
    pred: Sequence[dict[str, Any]],
    ref: Sequence[dict[str, Any]],
    keys: Sequence[str],
    threshold: float = THRESHOLD,
) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[int]] = defaultdict(list)
    for i, k in enumerate(keys):
        buckets[k].append(i)
    return {
        k: compare([pred[i] for i in idx], [ref[i] for i in idx], threshold)
        for k, idx in sorted(buckets.items())
    }


def latency_stats(latencies: Sequence[float], wall_s: float, n_ok: int | None = None,
                  completion_tokens: int = 0) -> dict[str, Any]:
    if not latencies:
        return {}
    s = sorted(latencies)

    def pct(q: float) -> float:
        return s[min(len(s) - 1, max(0, math.ceil(q * len(s)) - 1))]

    n = len(s) if n_ok is None else n_ok
    return {
        "p50_s": round(pct(0.50), 3),
        "p95_s": round(pct(0.95), 3),
        "judgments_per_s": round(n / wall_s, 3) if wall_s > 0 else None,
        "completion_tokens_per_s": round(completion_tokens / wall_s, 1) if wall_s > 0 else None,
        "wall_s": round(wall_s, 2),
    }


def cost_per_million_gpu(gpu_hourly: float, judgments_per_s: float) -> float | None:
    """GPU $/h ÷ (throughput × 3,600) × 10^6."""
    if not judgments_per_s:
        return None
    return round(gpu_hourly / (judgments_per_s * 3600) * 1e6, 2)


def cost_per_million_api(prompt_tokens: float, completion_tokens: float,
                         price_in: float, price_out: float) -> float:
    """Mean tokens per judgment × price per 1M tokens × 10^6 judgments."""
    return round((prompt_tokens * price_in + completion_tokens * price_out), 2)
