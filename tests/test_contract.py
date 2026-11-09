import json

import pytest
from curatorkit.gates.hallucination import HallucinationGate
from curatorkit.gates.reward import RewardGate

from lorde_forge import contract
from lorde_forge.contract import _NullLLM


def test_render_matches_curatorkit_gates():
    llm = _NullLLM(model="x")
    assert contract.render_reward("Hi?", "Hello.") == RewardGate(llm=llm)._build_reward_prompt("Hi?", "Hello.")
    h = HallucinationGate(llm=llm)
    assert contract.render_grounding("S" * 60, "Q", "A") == h.prompt_template.format(
        source_text="S" * 60, question="Q", answer="A"
    )


def test_messages_single_user_turn():
    m = contract.messages({"kind": "reward", "instruction": "i", "response": "r"})
    assert m == [{"role": "user", "content": contract.render_reward("i", "r")}]


@pytest.mark.parametrize("kind", contract.KINDS)
def test_fingerprint_stable_and_template_roundtrip(kind):
    assert contract.fingerprint(kind) == contract.fingerprint(kind)
    assert len(contract.fingerprint(kind)) == 64
    for slot in contract.SLOTS[kind]:
        assert "{" + slot + "}" in contract.template_text(kind)
    assert contract.template_id(kind).startswith("curatorkit-")


def test_match_template_recovers_slots():
    p = contract.render_grounding("Source with {braces} and\nnewlines " * 3, "How long?", "6,650 km.")
    kind, slots = contract.match_template(p)
    assert kind == "grounding"
    assert slots["question"] == "How long?" and slots["answer"] == "6,650 km."
    p2 = contract.render_reward("Write a haiku", "Autumn moonlight—")
    assert contract.match_template(p2) == ("reward", {"instruction": "Write a haiku", "response": "Autumn moonlight—"})
    assert contract.match_template("Rate this: " + p2) is None


def test_parse_reply_follows_curatorkit():
    good = json.dumps({"grounding_score": 0.1, "supported_claims": [], "unsupported_claims": ["x"],
                       "verdict": "hallucinated"})
    j = contract.parse_reply("grounding", good)
    assert j.ok and j.score == 0.1 and j.verdict == "hallucinated" and not j.decision()
    fenced = "```json\n" + json.dumps({"overall_score": 0.8}) + "\n```"
    assert contract.parse_reply("reward", fenced).decision()
    # prose around JSON is not JSON for CuratorKIT
    assert not contract.parse_reply("reward", 'Sure! {"overall_score": 0.9}').ok
    assert not contract.parse_reply("reward", '{"overall_score": 1.2}').ok
    assert not contract.parse_reply("grounding", None).ok
    # dimension fallback is CuratorKIT behaviour: averaged, flagged
    fb = contract.parse_reply("reward", json.dumps({"helpfulness": 1.0, "honesty": 0.5}))
    assert fb.ok and fb.used_fallback and fb.score == 0.75
