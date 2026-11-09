"""Reply schemas for vLLM structured outputs (spec Section 8.3).

They describe exactly the keys CuratorKIT's gates read, with long text clipped,
so constrained decoding always yields a gate-usable reply.
"""

from __future__ import annotations

from typing import Any

REWARD_DIMENSIONS = ("helpfulness", "honesty", "instruction_following")
VERDICTS = ("grounded", "partially_grounded", "hallucinated")
TEXT_MAX = 200
CLAIM_MAX = 140
CLAIMS_MAX = 4

_unit = {"type": "number", "minimum": 0, "maximum": 1}

REWARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["overall_score", "dimension_scores", "strengths", "weaknesses"],
    "properties": {
        "overall_score": _unit,
        "dimension_scores": {
            "type": "object",
            "additionalProperties": False,
            "required": list(REWARD_DIMENSIONS),
            "properties": {d: _unit for d in REWARD_DIMENSIONS},
        },
        "strengths": {"type": "string", "maxLength": TEXT_MAX},
        "weaknesses": {"type": "string", "maxLength": TEXT_MAX},
    },
}

GROUNDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["grounding_score", "supported_claims", "unsupported_claims", "verdict"],
    "properties": {
        "grounding_score": _unit,
        "supported_claims": {
            "type": "array",
            "maxItems": CLAIMS_MAX,
            "items": {"type": "string", "maxLength": CLAIM_MAX},
        },
        "unsupported_claims": {
            "type": "array",
            "maxItems": CLAIMS_MAX,
            "items": {"type": "string", "maxLength": CLAIM_MAX},
        },
        "verdict": {"enum": list(VERDICTS)},
    },
}

SCHEMAS = {"reward": REWARD_SCHEMA, "grounding": GROUNDING_SCHEMA}
PRIMARY_KEY = {"reward": "overall_score", "grounding": "grounding_score"}


def response_format(kind: str) -> dict[str, Any]:
    """OpenAI `response_format` body for vLLM structured outputs."""
    return {
        "type": "json_schema",
        "json_schema": {"name": f"lorde_{kind}", "schema": SCHEMAS[kind], "strict": True},
    }
