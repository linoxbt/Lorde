"""Deterministic degradations. Every prompt appears once good (gold) and once degraded.

LLM-made variants (student answers, closed-book answers, translations) are
produced in ``forge/01_build_pool.py``; the rule-based ones live here.
"""

from __future__ import annotations

import hashlib
import random
import re

REWARD_DEGRADED = ("student", "truncated", "off_topic", "wrong_language", "padded_repetition")
GROUNDING_DEGRADED = ("swapped_answer", "fabricated_numbers", "truncated", "closed_book")


def rng_for(*keys: str) -> random.Random:
    seed = int(hashlib.sha256("|".join(keys).encode()).hexdigest()[:16], 16)
    return random.Random(seed)


def pick_degraded(kind: str, uid: str) -> str:
    choices = REWARD_DEGRADED if kind == "reward" else GROUNDING_DEGRADED
    return rng_for("variant", kind, uid).choice(choices)


def truncate(text: str, uid: str) -> str:
    """Cut to 30-50% of its length at a word boundary, mid-thought."""
    text = text.strip()
    frac = rng_for("trunc", uid).uniform(0.3, 0.5)
    cut = max(1, int(len(text) * frac))
    space = text.rfind(" ", 0, cut)
    if space > len(text) * 0.15:
        cut = space
    return text[:cut].rstrip(" ,;:.-")


_SENT = re.compile(r"(?<=[.!?।؟。])\s+")


def pad_repetition(text: str, uid: str) -> str:
    """Repeat sentences so the answer is bloated and repetitive."""
    sents = [s for s in _SENT.split(text.strip()) if s]
    if not sents:
        return text
    r = rng_for("pad", uid)
    first = sents[0]
    body = " ".join(sents + [first] * r.randint(2, 4) + sents[: max(1, len(sents) // 2)])
    return body


_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def fabricate_numbers(text: str, uid: str) -> str:
    """Change every number; if there is none, add a confident, fabricated figure."""
    r = rng_for("fab", uid)

    def repl(m: re.Match[str]) -> str:
        s = m.group(0)
        digits = re.sub(r"\D", "", s)
        try:
            n = int(digits)
        except ValueError:  # non-ASCII digits that int() rejects
            n = r.randint(2, 99)
        new = n + r.choice([-1, 1]) * max(1, int(n * r.uniform(0.2, 0.9)) or r.randint(1, 9))
        if new == n or new < 0:
            new = n + r.randint(3, 40)
        return str(new)

    out, k = _NUM.subn(repl, text)
    if k:
        return out
    year = r.randint(1820, 1990)
    pct = r.randint(12, 87)
    return f"{text.rstrip()} ({year}; {pct}%)"


def swap_from(pool: list[str], uid: str, exclude: str) -> str:
    """Pick another text from the same language, never the original."""
    cands = [p for p in pool if p.strip() and p.strip() != exclude.strip()]
    if not cands:
        return exclude
    return rng_for("swap", uid).choice(cands)


def wrong_language_target(lang: str, uid: str, langs: list[str]) -> str:
    others = [x for x in langs if x != lang]
    return rng_for("wl", uid).choice(others)
