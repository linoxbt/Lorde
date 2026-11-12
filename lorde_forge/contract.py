"""The judge contract: CuratorKIT renders the prompts and parses the replies.

Nothing here re-implements CuratorKIT. Prompts come from
``RewardGate._build_reward_prompt`` and ``HallucinationGate.prompt_template``;
replies go through ``parse_judge_reply`` and ``extract_score``. A reply is
*gate-usable* if and only if that parser returns a score.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from functools import cache, lru_cache
from typing import Any

import curatorkit
from curatorkit.gates._score_parsing import JudgeParseError, extract_score, parse_judge_reply
from curatorkit.gates.hallucination import HallucinationGate
from curatorkit.gates.reward import RewardGate
from curatorkit.llm.base import BaseLLM, LLMResponse

from .config import THRESHOLD
from .schemas import REWARD_DIMENSIONS

KINDS = ("reward", "grounding")
SLOTS = {"reward": ("instruction", "response"), "grounding": ("source_text", "question", "answer")}


class _NullLLM(BaseLLM):
    """Gates need an LLM object to be constructed; rendering never calls it."""

    def _call(self, messages, **kwargs) -> LLMResponse:  # pragma: no cover
        raise RuntimeError("rendering only")


@lru_cache(maxsize=1)
def _gates() -> tuple[RewardGate, HallucinationGate]:
    llm = _NullLLM(model="null")
    return (
        RewardGate(llm=llm, dimensions=list(REWARD_DIMENSIONS)),
        HallucinationGate(llm=llm),
    )


def render_reward(instruction: str, response: str) -> str:
    return _gates()[0]._build_reward_prompt(instruction, response)


def render_grounding(source_text: str, question: str, answer: str) -> str:
    return _gates()[1].prompt_template.format(
        source_text=source_text, question=question, answer=answer
    )


def render(item: dict[str, Any]) -> str:
    """Render the exact gate prompt for a pool/test item."""
    if item["kind"] == "reward":
        return render_reward(item["instruction"], item["response"])
    if item["kind"] == "grounding":
        return render_grounding(item["source_text"], item["question"], item["answer"])
    raise ValueError(f"unknown kind {item['kind']!r}")


def messages(item: dict[str, Any]) -> list[dict[str, str]]:
    """CuratorKIT sends the rendered prompt as a single user message."""
    return [{"role": "user", "content": render(item)}]


# --------------------------------------------------------------------------- parsing


@dataclass
class Judgment:
    kind: str
    ok: bool
    score: float | None = None
    parsed: Any = None
    error: str | None = None
    used_fallback: bool = False
    extras: dict[str, Any] = field(default_factory=dict)

    def decision(self, threshold: float = THRESHOLD) -> bool:
        """Pass iff the reply is gate-usable and score >= threshold.

        An unparseable reply is a reject: CuratorKIT's default ``judge_on_error``.
        """
        return bool(self.ok and self.score is not None and self.score >= threshold)

    @property
    def verdict(self) -> str | None:
        if self.kind != "grounding" or not isinstance(self.parsed, dict):
            return None
        v = self.parsed.get("verdict")
        return v if isinstance(v, str) else None


def parse_reply(kind: str, raw: str | None) -> Judgment:
    """Parse a judge reply exactly as the CuratorKIT gate for ``kind`` would."""
    if raw is None:
        return Judgment(kind=kind, ok=False, error="no_reply")
    text, parsed = parse_judge_reply(raw)
    try:
        if kind == "reward":
            score, fb = extract_score(parsed, text, "overall_score", dimension_keys=REWARD_DIMENSIONS)
        elif kind == "grounding":
            score, fb = extract_score(parsed, text, "grounding_score")
        else:
            raise ValueError(f"unknown kind {kind!r}")
    except JudgeParseError as e:
        return Judgment(kind=kind, ok=False, parsed=parsed, error=f"JudgeParseError: {e}"[:300])
    return Judgment(kind=kind, ok=True, score=score, parsed=parsed, used_fallback=fb)


# --------------------------------------------------------------------- fingerprints

_SENTINEL = "\u0000{}\u0000"


@cache
def template_text(kind: str) -> str:
    """The rendered template with its variable slots replaced by ``{slot}`` markers."""
    filled = {s: _SENTINEL.format(s.upper()) for s in SLOTS[kind]}
    if kind == "reward":
        out = render_reward(filled["instruction"], filled["response"])
    else:
        out = render_grounding(filled["source_text"], filled["question"], filled["answer"])
    for s in SLOTS[kind]:
        out = out.replace(filled[s], "{" + s + "}")
    return out


@cache
def fingerprint(kind: str) -> str:
    """SHA-256 of the slot-free template (spec Section 8.2)."""
    return hashlib.sha256(template_text(kind).encode("utf-8")).hexdigest()


def template_id(kind: str) -> str:
    """Human-readable template id, e.g. ``curatorkit-0.1.23/reward@3f9c1a2b``."""
    gate = "reward" if kind == "reward" else "hallucination"
    return f"curatorkit-{curatorkit.__version__}/{gate}@{fingerprint(kind)[:8]}"


@cache
def _matcher(kind: str) -> re.Pattern[str]:
    parts = re.split(r"(\{(?:%s)\})" % "|".join(SLOTS[kind]), template_text(kind))
    rx = "".join(
        f"(?P<{p[1:-1]}>.*)" if p.startswith("{") and p[1:-1] in SLOTS[kind] else re.escape(p)
        for p in parts
    )
    return re.compile(rx, re.DOTALL)


def match_template(prompt: str) -> tuple[str, dict[str, str]] | None:
    """Return (kind, slot values) if ``prompt`` is a known judge prompt, else None."""
    for kind in KINDS:
        m = _matcher(kind).fullmatch(prompt)
        if m:
            return kind, m.groupdict()
    return None


def fingerprints() -> dict[str, Any]:
    return {
        "curatorkit_version": curatorkit.__version__,
        "templates": {k: {"id": template_id(k), "sha256": fingerprint(k)} for k in KINDS},
    }
