import json

import jsonschema
from hypothesis import given, settings
from hypothesis import strategies as st

from lorde_forge import canonical, contract
from lorde_forge.schemas import GROUNDING_SCHEMA, REWARD_SCHEMA, response_format


def test_reward_canonical_clips_and_validates():
    raw = json.dumps({"overall_score": 0.83, "dimension_scores": {"helpfulness": 0.9, "honesty": 0.8,
                      "instruction_following": 0.7}, "strengths": "x" * 500, "weaknesses": "none",
                      "extra": "dropped"})
    t = canonical.canonical(contract.parse_reply("reward", raw))
    jsonschema.validate(t, REWARD_SCHEMA)
    assert set(t) == {"overall_score", "dimension_scores", "strengths", "weaknesses"}
    assert len(t["strengths"]) <= 200


def test_grounding_canonical_fixes_verdict_and_claims():
    raw = json.dumps({"grounding_score": 0.55, "supported_claims": ["..."],
                      "unsupported_claims": "one claim", "verdict": "grounded | partially_grounded | hallucinated"})
    t = canonical.canonical(contract.parse_reply("grounding", raw))
    jsonschema.validate(t, GROUNDING_SCHEMA)
    assert t["verdict"] == "partially_grounded"
    assert t["supported_claims"] == [] and t["unsupported_claims"] == ["one claim"]


def test_unusable_has_no_target():
    assert canonical.canonical(contract.parse_reply("reward", "I think 0.8")) is None


def test_response_format_shape():
    rf = response_format("grounding")
    assert rf["type"] == "json_schema" and rf["json_schema"]["schema"] is GROUNDING_SCHEMA


texts = st.text(max_size=400)
unit = st.floats(min_value=0, max_value=1, allow_nan=False)


@settings(max_examples=200, deadline=None)
@given(score=unit, s=texts, w=texts, h=unit, o=unit, i=unit)
def test_canonical_reward_always_gate_usable(score, s, w, h, o, i):
    raw = json.dumps({"overall_score": score, "dimension_scores": {"helpfulness": h, "honesty": o,
                      "instruction_following": i}, "strengths": s, "weaknesses": w})
    t = canonical.canonical(contract.parse_reply("reward", raw))
    back = contract.parse_reply("reward", canonical.dumps(t))
    assert back.ok and back.score == round(score, 2)


@settings(max_examples=200, deadline=None)
@given(score=unit, sup=st.lists(texts, max_size=8), uns=st.lists(texts, max_size=8),
       verdict=st.one_of(st.sampled_from(["grounded", "partially_grounded", "hallucinated"]), texts))
def test_canonical_grounding_always_gate_usable(score, sup, uns, verdict):
    raw = json.dumps({"grounding_score": score, "supported_claims": sup, "unsupported_claims": uns,
                      "verdict": verdict})
    t = canonical.canonical(contract.parse_reply("grounding", raw))
    back = contract.parse_reply("grounding", canonical.dumps(t))
    assert back.ok and back.score == round(score, 2) and back.verdict in ("grounded", "partially_grounded", "hallucinated")


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=300))
def test_parser_never_crashes(raw):
    for kind in contract.KINDS:
        j = contract.parse_reply(kind, raw)
        assert j.ok in (True, False)
        if j.ok:
            assert 0.0 <= j.score <= 1.0
