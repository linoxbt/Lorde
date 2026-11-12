"""Request and response models for the native judge API (spec Section 11)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Decision = Literal["pass", "reject"]


class _Req(BaseModel):
    model_config = ConfigDict(extra="forbid")
    threshold: float | None = Field(None, ge=0, le=1)


class RewardRequest(_Req):
    instruction: str = Field(..., min_length=1, max_length=32_000)
    response: str = Field(..., max_length=32_000)


class GroundingRequest(_Req):
    source_text: str = Field(..., min_length=1, max_length=24_000)
    question: str = Field(..., min_length=1, max_length=32_000)
    answer: str = Field(..., max_length=32_000)


class RewardItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str = Field(..., min_length=1, max_length=32_000)
    response: str = Field(..., max_length=32_000)
    id: str | None = None


class GroundingItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_text: str = Field(..., min_length=1, max_length=24_000)
    question: str = Field(..., min_length=1, max_length=32_000)
    answer: str = Field(..., max_length=32_000)
    id: str | None = None


class BatchRequest(_Req):
    kind: Literal["reward", "grounding"]
    items: list[dict] = Field(..., min_length=1)


class _Meta(BaseModel):
    decision: Decision
    threshold: float
    model: str
    template: str
    latency_ms: int
    request_id: str


class DimensionScores(BaseModel):
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


class BatchItemResult(BaseModel):
    index: int
    id: str | None = None
    result: RewardResult | GroundingResult | None = None
    error: dict | None = None


class BatchResult(BaseModel):
    kind: str
    results: list[BatchItemResult]
    summary: dict[str, int]
    model: str
    template: str
    request_id: str
