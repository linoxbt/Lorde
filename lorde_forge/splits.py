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


# ----------------------------------------------------------------------------- near duplicates
#
# Grouping by prompt / source chunk keeps identical prompts out of the test set, but aya_dataset has
# near-duplicates (the same task with a different chapter number, the same question re-punctuated). Two texts
# of the same kind and language whose normalized similarity is at least the threshold are near-duplicates.

NEAR_DUP_THRESHOLD = 0.9


def normalize_text(text: str | None) -> str:
    """NFKC, case-folded, whitespace collapsed: formatting differences do not hide a duplicate."""
    import re
    import unicodedata

    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "").casefold()).strip()


def similarity(a: str, b: str, floor: float = 0.0) -> float:
    """difflib ratio of two normalized texts (no autojunk, so long passages are compared fully); returns 0 early
    when the cheap upper bounds are already below ``floor``."""
    import difflib

    if a == b:
        return 1.0
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    if sm.real_quick_ratio() < floor or sm.quick_ratio() < floor:
        return 0.0
    return sm.ratio()


def _key_text(r: dict[str, Any]) -> str:
    """What makes two items the same task: the reward prompt, or the grounding source passage."""
    return normalize_text(r.get("instruction") if r.get("kind") == "reward" else r.get("source_text"))


def near_duplicate_groups(rows: Sequence[dict[str, Any]], threshold: float = NEAR_DUP_THRESHOLD,
                          key: str = "group") -> dict[str, str]:
    """group -> representative group, merging groups whose key texts (same kind and language) are at least
    ``threshold`` similar. Groups with no near-duplicate map to themselves, so splitting on the result changes
    nothing for them. ``threshold`` <= 0 or > 1 disables merging."""
    parent: dict[str, str] = {str(r[key]): str(r[key]) for r in rows}

    def find(g: str) -> str:
        while parent[g] != g:
            parent[g] = parent[parent[g]]
            g = parent[g]
        return g

    if not 0 < threshold <= 1:
        return parent
    texts: dict[tuple, dict[str, str]] = defaultdict(dict)  # (kind, lang) -> text -> first group
    for r in rows:
        t = _key_text(r)
        if t:
            bucket_ = texts[(r.get("kind"), r.get("lang"))]
            if t in bucket_:
                parent[find(str(r[key]))] = find(bucket_[t])
            else:
                bucket_[t] = str(r[key])
    for by_text in texts.values():
        items = sorted(by_text.items())
        for i, (a, ga) in enumerate(items):
            for b, gb in items[i + 1:]:
                if find(ga) != find(gb) and similarity(a, b, threshold) >= threshold:
                    ra, rb = sorted((find(ga), find(gb)))
                    parent[rb] = ra
    return {g: find(g) for g in parent}


def leakage_report(train: Sequence[dict[str, Any]], test: Sequence[dict[str, Any]],
                   threshold: float = NEAR_DUP_THRESHOLD) -> dict[str, Any]:
    """How close the test set comes to training: distinct test reward prompts / grounding passages with a
    near-duplicate (similarity >= ``threshold``, same kind and language) in training, and test gold reward
    responses identical (after normalization) to a training gold response."""
    out: dict[str, Any] = {"threshold": threshold}
    for kind, name in (("reward", "prompts"), ("grounding", "passages")):
        tr: dict[str, set[str]] = defaultdict(set)
        for r in train:
            if r.get("kind") == kind and _key_text(r):
                tr[r.get("lang")].add(_key_text(r))
        te = {(r.get("lang"), _key_text(r)) for r in test if r.get("kind") == kind and _key_text(r)}
        hits = [t for lang, t in te if any(similarity(t, x, threshold) >= threshold for x in tr[lang])]
        out[f"test_{name}"] = len(te)
        out[f"test_{name}_near_train"] = len(hits)
    gold = {normalize_text(r.get("response")) for r in train if r.get("kind") == "reward" and r.get("variant") == "gold"}
    out["test_gold_responses_identical_to_train"] = sum(
        1 for r in test if r.get("kind") == "reward" and r.get("variant") == "gold"
        and normalize_text(r.get("response")) in gold)
    return out
