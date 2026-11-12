"""Prediction caches keyed by model identity (audit B4) and never caching call errors (B15)."""

import pytest
from stub_openai import serve

from lorde_forge import fingerprint, jsonl, llm
from lorde_forge.judge import as_metric_row, cache_meta, error_counts, items_sha, open_cache, predict


@pytest.fixture(scope="module")
def stub():
    srv, url = serve("plain")
    yield url
    srv.shutdown()


def items(n=6):
    return [{"uid": f"r-en-{i:04d}:gold", "kind": "reward", "instruction": f"Question {i}?",
             "response": "A complete and helpful answer with enough detail to be useful." * (i % 2 + 1)}
            for i in range(n)]


def test_cache_reused_for_same_model_and_recomputed_when_model_changes(tmp_path, stub):
    ep = llm.Endpoint.parse(f"lorde=openai/lorde@{stub}")
    cache = tmp_path / "lorde.jsonl"
    its = items()
    m1 = cache_meta({"sha": "aaa", "source": "test"}, its, False)
    p1 = predict(ep, its, False, cache, 4, meta=m1)
    assert len(p1) == 6 and all(p["ok"] for p in p1.values())
    assert fingerprint.read_meta(cache)["model"]["sha"] == "aaa"
    jsonl.write(cache, [{**r, "raw": r["raw"], "marker": 1} for r in jsonl.read(cache)])
    predict(ep, its, False, cache, 4, meta=m1)  # same model: rows are reused, nothing re-requested
    assert all(r.get("marker") == 1 for r in jsonl.read(cache))

    m2 = cache_meta({"sha": "bbb", "source": "test"}, its, False)  # retrained model
    predict(ep, its, False, cache, 4, meta=m2)
    assert not any(r.get("marker") for r in jsonl.read(cache)) and len(jsonl.read(cache)) == 6
    assert (tmp_path / "lorde.jsonl.stale").exists() and fingerprint.read_meta(cache)["model"]["sha"] == "bbb"

    m3 = cache_meta({"sha": "bbb", "source": "test"}, its[:5], False)  # different test set
    assert open_cache(cache, m3).startswith("recomputed (items_sha changed")


def test_legacy_cache_needs_explicit_trust(tmp_path, stub):
    ep = llm.Endpoint.parse(f"lorde=openai/lorde@{stub}")
    its = items()
    legacy = tmp_path / "base.jsonl"
    rows = [{"uid": r["uid"], "raw": "{}", "ok": True, "score": 0.9, "verdict": None, "call_error": None,
             "legacy": 1} for r in its]
    meta = cache_meta({"sha": "aaa"}, its, False)

    jsonl.write(legacy, rows)
    usable, status = fingerprint.check(legacy, meta)
    assert not usable and "--trust-legacy-cache" in status
    predict(ep, its, False, legacy, 4, meta=meta, trust_legacy=True)
    assert all(r.get("legacy") for r in jsonl.read(legacy))  # reused as is
    assert fingerprint.read_meta(legacy)["legacy_trusted"] is True
    assert fingerprint.check(legacy, meta, trust_legacy=True) == (True, "legacy (no fingerprint), trusted by "
                                                                        "--trust-legacy-cache")
    assert not fingerprint.check(legacy, meta)[0]  # trusting once does not launder it

    predict(ep, its, False, legacy, 4, meta=meta)  # without the flag: recomputed
    assert not any(r.get("legacy") for r in jsonl.read(legacy))


def test_call_errors_are_not_cached(tmp_path, stub):
    its = items(3)
    down = llm.Endpoint.parse("lorde=openai/lorde@http://127.0.0.1:9/v1")  # nothing listens there
    cache = tmp_path / "lorde.jsonl"
    meta = cache_meta({"sha": "aaa"}, its, False)
    p = predict(down, its, False, cache, 3, meta=meta)
    assert all(v["call_error"] for v in p.values()) and not jsonl.read(cache)
    assert error_counts(p, [r["uid"] for r in its]) == {"call_errors": 3, "parse_failures": 0, "missing": 0}
    assert as_metric_row(p[its[0]["uid"]])["ok"] is False

    # a legacy cache that did store a call error retries that item
    jsonl.write(cache, [{"uid": its[0]["uid"], "raw": None, "ok": False, "score": None, "call_error": "boom"}])
    up = llm.Endpoint.parse(f"lorde=openai/lorde@{stub}")
    p = predict(up, its, False, cache, 3, meta=meta, trust_legacy=True)
    assert all(v["ok"] for v in p.values()) and error_counts(p, list(p)) == {"call_errors": 0, "parse_failures": 0,
                                                                              "missing": 0}


def test_model_dir_fingerprint_detects_new_weights(tmp_path):
    d = tmp_path / "merged"
    d.mkdir()
    (d / "config.json").write_text("{}")
    w = d / "model.safetensors"
    w.write_bytes(b"\0" * (40 << 20))
    a = fingerprint.model_sha(str(d))[0]
    data = bytearray(w.read_bytes())
    data[18 << 20: 21 << 20] = b"\1" * (3 << 20)  # one 3 MiB tensor changed (a merge changes all of them)
    w.write_bytes(bytes(data))
    assert fingerprint.model_sha(str(d))[0] != a
    assert items_sha(items()) == items_sha(list(reversed(items())))
