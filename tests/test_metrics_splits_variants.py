import math

from lorde_forge import metrics, splits, variants


def row(ok, score, verdict=None):
    return {"ok": ok, "score": score, "verdict": verdict}


def test_compare_basic_and_unparseable_is_reject():
    ref = [row(True, 0.9), row(True, 0.2), row(True, 0.8), row(True, 0.1)]
    pred = [row(True, 0.85), row(True, 0.3), row(False, None), row(True, 0.75)]
    m = metrics.compare(pred, ref)
    assert m["gate_usable_rate"] == 0.75
    assert m["decision_agreement"] == 0.5
    assert m["mae"] == round((0.05 + 0.1 + 0.65) / 3, 4)


def test_kappa_known_value():
    a = [True] * 20 + [False] * 15 + [True] * 5 + [False] * 10
    b = [True] * 20 + [False] * 15 + [False] * 5 + [True] * 10
    # po = 35/50, pa=25/50, pb=30/50, pe=0.5*0.6+0.5*0.4=0.5 -> kappa=0.4
    assert math.isclose(metrics.cohen_kappa(a, b), 0.4)


def test_costs_match_spec_example():
    assert metrics.cost_per_million_api(700, 150, 0.50, 1.50) == 575.0
    assert metrics.cost_per_million_gpu(0.80, 10) == 22.22


def test_latency_stats():
    s = metrics.latency_stats([0.1] * 95 + [2.0] * 5, wall_s=10, completion_tokens=1000)
    assert s["p50_s"] == 0.1 and s["p95_s"] == 0.1 and s["judgments_per_s"] == 10
    assert metrics.latency_stats([0.1] * 94 + [2.0] * 6, wall_s=10)["p95_s"] == 2.0


def test_grouped_split_has_no_leakage():
    rows = [{"uid": f"{g}-{v}", "group": f"g{g}"} for g in range(500) for v in range(2)]
    s = splits.grouped_split(rows)
    groups = {k: {r["group"] for r in v} for k, v in s.items()}
    assert not (groups["train"] & groups["test"]) and not (groups["val"] & groups["test"])
    assert 0.1 < len(s["test"]) / len(rows) < 0.2


def test_stratified_sample_balanced():
    rows = [{"uid": f"{k}{l}{i}", "kind": k, "lang": l} for k in ("reward", "grounding")
            for l in ("en", "hi") for i in range(100)]
    out = splits.stratified_sample(rows, 40)
    assert len(out) == 40
    assert sum(r["kind"] == "reward" and r["lang"] == "hi" for r in out) == 10


def test_variants_deterministic():
    t = "The Niger River is about 4,180 km long. It flows through five countries."
    assert variants.fabricate_numbers(t, "u1") == variants.fabricate_numbers(t, "u1")
    assert variants.fabricate_numbers(t, "u1") != t
    assert "(" in variants.fabricate_numbers("No numbers here.", "u2")
    assert len(variants.truncate(t, "u3")) < len(t)
    assert len(variants.pad_repetition(t, "u4")) > len(t)
    assert variants.pick_degraded("reward", "x") in variants.REWARD_DEGRADED
    assert variants.swap_from(["a", "b"], "u", exclude="a") == "b"
