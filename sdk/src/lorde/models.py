"""Typed results, mirroring the Gateway's OpenAPI schema."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class _M(BaseModel):
    model_config = ConfigDict(extra="allow")


class _Meta(_M):
    decision: Literal["pass", "reject"]
    threshold: float
    model: str
    template: str
    latency_ms: int
    request_id: str

    @property
    def passed(self) -> bool:
        return self.decision == "pass"


class DimensionScores(_M):
    helpfulness: float
    honesty: float
    instruction_following: float


class RewardResult(_Meta):
    overall_score: float
    dimension_scores: DimensionScores | None = None
    strengths: str = ""
    weaknesses: str = ""


class GroundingResult(_Meta):
    grounding_score: float
    supported_claims: list[str] = []
    unsupported_claims: list[str] = []
    verdict: str | None = None


class BatchItem(_M):
    index: int
    id: str | None = None
    result: RewardResult | GroundingResult | None = None
    error: dict | None = None


class BatchResult(_M):
    kind: str
    results: list[BatchItem]
    summary: dict[str, int]
    model: str
    template: str
    request_id: str
