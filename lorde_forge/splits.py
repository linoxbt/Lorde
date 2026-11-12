"""Grouped splits: every item of a prompt (or source chunk) lands in one split."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Sequence
from typing import Any


def bucket(group: str, salt: str = "lorde-v1") -> float:
    h = hashlib.sha256(f"{salt}|{group}".encode()).hexdigest()
    return int(h[:12], 16) / float(16**12)


def grouped_split(
    rows: Sequence[dict[str, Any]],
    val: float = 0.05,
    test: float = 0.15,
    key: str = "group",
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {"train": [], "val": [], "test": []}
    for r in rows:
        b = bucket(str(r[key]))
        name = "test" if b < test else "val" if b < test + val else "train"
        out[name].append(r)
    return out


def stratified_draws(
    rows: Sequence[dict[str, Any]],
    n: int,
    k: int = 1,
    keys: Sequence[str] = ("kind", "lang"),
    seed: int = 0,
) -> list[list[dict[str, Any]]]:
    """``k`` disjoint random samples of ``n`` rows (fewer if there are not enough), each stratified by
    ``keys`` with proportional allocation (largest remainder), in random order. Seeded: same rows, same draws."""
    import random

    rng = random.Random(seed)
    strata: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for r in sorted(rows, key=lambda r: str(r["uid"])):
        strata[tuple(r.get(x) for x in keys)].append(r)
    order = sorted(strata, key=str)
    for s in order:
        rng.shuffle(strata[s])
    n = max(0, min(n, len(rows) // max(1, k)))
    total = len(rows)
    quota = {s: n * len(strata[s]) / total for s in order} if total else {}
    alloc = {s: min(int(q), len(strata[s]) // k) for s, q in quota.items()}
    for s in sorted(order, key=lambda s: quota[s] - int(quota[s]), reverse=True):
        if sum(alloc.values()) >= n:
            break
        if (alloc[s] + 1) * k <= len(strata[s]):
            alloc[s] += 1
    draws: list[list[dict[str, Any]]] = [[] for _ in range(k)]
    for s in order:
        for j in range(k):
            draws[j] += strata[s][j * alloc[s]:(j + 1) * alloc[s]]
    for d in draws:
        rng.shuffle(d)
    return draws


def stratified_sample(
    rows: Sequence[dict[str, Any]],
    n: int,
    keys: Sequence[str] = ("kind", "lang"),
    salt: str = "human",
) -> list[dict[str, Any]]:
    """Deterministic, round-robin sample across strata (e.g. kind × lang)."""
    strata: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for r in sorted(rows, key=lambda r: bucket(str(r["uid"]), salt)):
        strata[tuple(r[k] for k in keys)].append(r)
    order = sorted(strata)
    out: list[dict[str, Any]] = []
    i = 0
    while len(out) < n and any(strata[s] for s in order):
        s = order[i % len(order)]
        if strata[s]:
            out.append(strata[s].pop(0))
        i += 1
    return out
