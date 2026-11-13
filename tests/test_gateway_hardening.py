"""Gateway hardening after the audit: CORS for uploads, streaming body limits, early auth, key-cache bounds,
the OpenAI proxy allow-list, the error envelope, readiness, docs, access logs and MCP scopes."""

import json
import logging

import pytest
from fastapi.testclient import TestClient
from test_gateway import PEPPER, mcp_call

from gateway.app import create_app
from gateway.keys import KeyStore
from gateway.settings import Settings
from lorde_forge import contract

MB = 1024 * 1024
ORIGIN = "https://console.example"


def chunks(total: int, size: int = 256 * 1024):
    sent = 0
    while sent < total:
        n = min(size, total - sent)
        sent += n
        yield b"x" * n


def test_cors_preflight_allows_put_and_errors_carry_cors(gw):
    app, h, _, _ = gw(allowed_origins=[ORIGIN])
    with TestClient(app) as c:
        r = c.options("/v1/_storage/sometoken", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "PUT",
                                                         "Access-Control-Request-Headers": "content-type"})
        assert r.status_code == 200 and "PUT" in r.headers["access-control-allow-methods"]
        r = c.options("/v1/_storage/sometoken", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET"})
        assert r.status_code == 200 and "GET" in r.headers["access-control-allow-methods"]
        r = c.options("/v1/jobs", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST",
                                           "Access-Control-Request-Headers": "authorization,idempotency-key"})
        assert r.status_code == 200
        r = c.post("/v1/judge/reward", json={"instruction": "a", "response": "b"}, headers={"Origin": ORIGIN})
        assert r.status_code == 401 and r.headers["access-control-allow-origin"] == ORIGIN
        assert "x-request-id" in r.headers["access-control-expose-headers"]
        r = c.options("/v1/jobs", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
        assert r.status_code == 400


def test_storage_put_allows_large_uploads_while_api_bodies_stay_capped(gw):
    app, h, _, tmp = gw(storage_dir=None)
    app.gw.s.storage_dir = str(tmp / "storage")
    from gateway.storage import LocalStorage

    app.gw.storage = LocalStorage(tmp / "storage", PEPPER + ":storage")
    six = 6 * MB
    with TestClient(app) as c:
        token = app.gw.storage.token("put", "datasets", "org1/d1/data.jsonl")
        r = c.put(f"/v1/_storage/{token}", content=b"x" * six)
        assert r.status_code == 200 and r.json()["bytes"] == six
        assert (tmp / "storage" / "datasets" / "org1/d1/data.jsonl").stat().st_size == six
        token = app.gw.storage.token("put", "datasets", "org1/d2/data.jsonl")  # chunked, no Content-Length
        assert c.put(f"/v1/_storage/{token}", content=chunks(six)).json()["bytes"] == six
        # the same 6 MB on a judge route is refused, with or without Content-Length
        big = json.dumps({"instruction": "x" * six, "response": "y"}).encode()
        r = c.post("/v1/judge/reward", content=big, headers={**h, "Content-Type": "application/json"})
        assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"
        r = c.post("/v1/judge/reward", content=chunks(six), headers={**h, "Content-Type": "application/json"})
        assert r.status_code == 413
        # without a key the body is never read: 401, not 413 and not a parse
        r = c.post("/v1/judge/reward", content=chunks(12 * MB), headers={"Content-Type": "application/json"})
        assert r.status_code == 401
        # bad or expired storage tokens are 404
        assert c.put("/v1/_storage/nope.nope", content=b"x").status_code == 404
        # over the upload limit while streaming: 413 and no partial object left behind
        app.gw.s.max_upload_bytes = 2 * MB
        token = app.gw.storage.token("put", "datasets", "org1/d3/data.jsonl")
        r = c.put(f"/v1/_storage/{token}", content=chunks(3 * MB))
        assert r.status_code == 413
        assert not list((tmp / "storage" / "datasets" / "org1").glob("d3/*"))


def test_validation_runs_after_auth_and_scope(gw):
    app, h, _, _ = gw(scopes=("jobs:read",))
    with TestClient(app) as c:
        assert c.post("/v1/judge/reward", json={"nope": 1}).status_code == 401
        r = c.post("/v1/judge/reward", json={"nope": 1}, headers=h)
        assert r.status_code == 403 and "judge" in r.json()["error"]["message"]


def test_malformed_keys_never_reach_the_store_and_cache_is_bounded(gw, monkeypatch):
    app, h, _, _ = gw(key_cache_size=5)
    store = app.gw.keys
    loads = []
    real = store._load
    monkeypatch.setattr(store, "_load", lambda: loads.append(1) or real())
    with TestClient(app) as c:
        for bad in ["lorde_sk_nope", "lorde_sk_" + "!" * 43, "sk_" + "a" * 43, "lorde_sk_" + "a" * 300]:
            r = c.get("/v1/models", headers={"Authorization": f"Bearer {bad}"})
            assert r.status_code == 401 and r.json()["error"]["message"] == "malformed API key"
        assert loads == []
        for i in range(40):  # well-formed but unknown keys: looked up once each, cache stays bounded
            assert c.get("/v1/models", headers={"Authorization": f"Bearer lorde_sk_{i:08d}{'z' * 35}"}).status_code == 401
        assert len(loads) == 40 and len(store._cache) <= 10
        assert c.get("/v1/models", headers=h).status_code == 200


def test_openai_proxy_allow_list_and_clamps(gw):
    app, h, seen, _ = gw()
    prompt = contract.render_reward("Explain X", "A careful, complete and well-structured explanation of X.")
    msgs = [{"role": "user", "content": prompt}]
    with TestClient(app) as c:
        r = c.post("/v1/chat/completions", headers=h, json={
            "model": "x", "messages": msgs, "max_tokens": 100_000, "temperature": -3, "logit_bias": {"1": 100},
            "mirostat": 2, "n_probs": 50, "top_p": 0.9, "seed": 7, "stop": "END"})
        assert r.status_code == 200, r.text
        sent = seen[-1]
        assert sent["max_tokens"] == 512 and sent["temperature"] == 0.0 and sent["seed"] == 7
        assert sent["stop"] == ["END", "<|END_OF_TURN_TOKEN|>"] and sent["top_p"] == 0.9 and sent["model"] == "lorde"
        assert not {"logit_bias", "mirostat", "n_probs"} & set(sent)
        c.post("/v1/chat/completions", headers=h, json={"messages": msgs, "max_completion_tokens": 64})
        assert seen[-1]["max_tokens"] == 64
        n_calls = len(seen)
        bad = [{"messages": msgs, "n": 2}, {"messages": msgs, "temperature": "hot"},
               {"messages": msgs, "temperature": True}, {"messages": msgs, "max_tokens": "lots"},
               {"messages": msgs, "stream": True}, {"messages": [{"role": "tool", "content": "x"}]},
               {"messages": [{"role": "user", "content": {"a": 1}}]}, {"messages": []}]
        for b in bad:
            r = c.post("/v1/chat/completions", headers=h, json=b)
            assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request", b
        r = c.post("/v1/chat/completions", headers={**h, "Content-Type": "application/json"},
                   content=b'{"messages": [{"role": "user", "content": "hi"}], "temperature": NaN}')
        assert r.status_code == 400
        r = c.post("/v1/chat/completions", headers=h,
                   json={"messages": [{"role": "user", "content": "x" * 130_000}]})
        assert r.status_code == 413
        assert len(seen) == n_calls  # nothing invalid reached the model


def test_batch_larger_than_rpm_is_413_not_429(gw):
    app, h, _, _ = gw(rpm=2)
    with TestClient(app) as c:
        items = [{"instruction": f"q{i}", "response": "A long enough answer to be judged as good."} for i in range(3)]
        r = c.post("/v1/judge/batch", headers=h, json={"kind": "reward", "items": items})
        assert r.status_code == 413 and "judgments/minute" in r.json()["error"]["message"]


def test_unhandled_errors_use_the_envelope(gw, monkeypatch):
    app, h, _, _ = gw()

    async def boom(*a, **kw):
        raise RuntimeError("secret internals")

    monkeypatch.setattr(app.gw.service, "judge", boom)
    with TestClient(app) as c:
        r = c.post("/v1/judge/reward", headers=h, json={"instruction": "a", "response": "b"})
        assert r.status_code == 500
        e = r.json()["error"]
        assert e["code"] == "internal_error" and e["request_id"] == r.headers["x-request-id"]
        assert "secret" not in r.text


def test_readyz_reports_each_dependency_and_redis_fails_open(gw):
    app, h, _, _ = gw()
    with TestClient(app) as c:
        b = c.get("/readyz").json()
        assert b["status"] == "ready" and b["serve"] is True and b["database"] is None and b["redis"] is None
    app, h, _, _ = gw(redis_url="redis://127.0.0.1:1/0")  # nothing listens there
    with TestClient(app) as c:
        r = c.get("/readyz")
        assert r.status_code == 503 and r.json()["redis"] is False
        ok = c.post("/v1/judge/reward", headers=h, json={"instruction": "Explain X",
                                                          "response": "A careful, complete and well-structured answer."})
        assert ok.status_code == 200  # fail-open: judged, limited by the in-process bucket


def test_docs_are_off_unless_enabled(gw):
    app, _, _, _ = gw()
    with TestClient(app) as c:
        assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404
    app, _, _, _ = gw(public_docs=True)
    with TestClient(app) as c:
        assert c.get("/openapi.json").status_code == 200


def test_metrics_token_and_mcp_hosts_default():
    s = Settings(key_pepper=PEPPER, public_url="https://api.lorde.example")
    assert s.mcp_allowed_hosts == ["api.lorde.example", "api.lorde.example:*"]
    assert Settings(key_pepper=PEPPER, public_url="").mcp_allowed_hosts == []
    s = Settings(key_pepper=PEPPER, upstream_concurrency=2)
    assert s.worker_judge_concurrency == 1 and s.api_upstream_slots == 1   # one job judgment, the rest for the API
    assert Settings(key_pepper=PEPPER, upstream_concurrency=1).api_upstream_slots == 1
    assert Settings(key_pepper=PEPPER, upstream_concurrency=16, worker_shares_upstream=False).api_upstream_slots == 16


def test_metrics_need_token_when_set(gw):
    app, _, _, _ = gw(metrics_token="m" * 20)
    with TestClient(app) as c:
        assert c.get("/metrics").status_code == 404
        assert c.get("/metrics", headers={"Authorization": "Bearer " + "m" * 20}).status_code == 200


def test_access_log_is_json_without_secrets(gw):
    app, h, _, _ = gw()
    got: list[logging.LogRecord] = []

    class H(logging.Handler):
        def emit(self, record):
            got.append(record)

    handler = H()
    logging.getLogger("lorde.access").addHandler(handler)
    try:
        with TestClient(app) as c:
            c.post("/v1/judge/reward", headers=h, json={"instruction": "SECRET-TEXT", "response": "b"})
            c.get("/v1/jobs/123", headers=h)
            c.put("/v1/_storage/TOKEN-VALUE", content=b"x")
    finally:
        logging.getLogger("lorde.access").removeHandler(handler)
    fields = [r.fields for r in got]
    first = fields[0]
    assert first["route"] == "/v1/judge/reward" and first["status"] == 200 and first["org_id"] == "org1"
    assert first["key_prefix"].startswith("lorde_sk_") and first["duration_ms"] >= 0 and first["request_id"]
    assert any(f["route"] == "/v1/jobs/{job_id}" for f in fields)
    dump = json.dumps(fields)
    assert "SECRET-TEXT" not in dump and "TOKEN-VALUE" not in dump and h["Authorization"][7:] not in dump


def test_mcp_scopes_per_tool(gw, tmp_path):
    app, h, _, _ = gw(scopes=("jobs:read",))
    with TestClient(app) as c:
        assert mcp_call(c, h, "tools/list").status_code == 200   # any valid key may list tools
        r = mcp_call(c, h, "tools/call", {"name": "lorde_judge_reward",
                                          "arguments": {"instruction": "a", "response": "b"}}, i=2).json()
        assert r["result"]["isError"] and "forbidden" in r["result"]["content"][0]["text"]
        r = mcp_call(c, h, "tools/call", {"name": "lorde_job_status", "arguments": {"job_id": "x"}}, i=3).json()
        assert r["result"]["isError"]


@pytest.fixture()
def gw(tmp_path):
    def make(style="plain", rpm=600, fail=False, scopes=("judge",), **kw):
        from test_gateway import stub_vllm

        s = Settings(key_pepper=PEPPER, keys_file=str(tmp_path / "keys.json"), usage_file=str(tmp_path / "usage.jsonl"),
                     model_revision="v1.0.0", vllm_base_url="http://vllm/v1", **kw)
        key, _ = KeyStore(s.keys_file, PEPPER).create("test", "org1", list(scopes), rpm)
        seen: list = []
        app = create_app(s, transport=stub_vllm(style, seen, fail))
        return app, {"Authorization": f"Bearer {key}"}, seen, tmp_path
    return make


def test_chat_proxy_always_stops_at_the_cohere_turn_end():
    """llama.cpp renders Tiny Aya's <|END_OF_TURN_TOKEN|> as text in schema-less chat replies; the proxy stops on it
    (added once, after the caller's own stop strings)."""
    from gateway.service import TURN_END_STOPS, JudgeService
    from gateway.settings import Settings

    svc = JudgeService.__new__(JudgeService)
    svc.s = Settings()
    msgs = [{"role": "user", "content": "hi"}]
    assert svc.sanitize_chat({"messages": msgs})["stop"] == list(TURN_END_STOPS)
    assert svc.sanitize_chat({"messages": msgs, "stop": "<|END_OF_TURN_TOKEN|>"})["stop"] == ["<|END_OF_TURN_TOKEN|>"]
