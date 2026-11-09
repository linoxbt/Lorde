"""Gateway: auth, limits, native judge endpoints, OpenAI proxy with schema injection, MCP, errors."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from stub_openai import judge_reply

from gateway.app import create_app
from gateway.keys import KeyStore
from gateway.settings import Settings
from lorde_forge import contract

PEPPER = "p" * 40
SRC = "The Niger River is the main river of West Africa, about 4,180 km long. It flows through Mali and Niger."


def stub_vllm(style="plain", seen=None, fail=False):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "lorde"}]})
        if fail:
            return httpx.Response(500, text="boom")
        req = json.loads(request.content)
        if seen is not None:
            seen.append(req)
        prompt = req["messages"][-1]["content"]
        text = judge_reply(prompt, "plain" if req.get("response_format") else style) or "hello"
        return httpx.Response(200, json={"id": "x", "object": "chat.completion", "model": req["model"],
                                         "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                                      "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 100, "completion_tokens": 20}})
    return httpx.MockTransport(handler)


@pytest.fixture()
def gw(tmp_path):
    def make(style="plain", rpm=600, fail=False, scopes=("judge",)):
        s = Settings(key_pepper=PEPPER, keys_file=str(tmp_path / "keys.json"), usage_file=str(tmp_path / "usage.jsonl"),
                     model_revision="v1.0.0", vllm_base_url="http://vllm/v1")
        key, _ = KeyStore(s.keys_file, PEPPER).create("test", "org1", list(scopes), rpm)
        seen: list = []
        app = create_app(s, transport=stub_vllm(style, seen, fail))
        return app, {"Authorization": f"Bearer {key}"}, seen, tmp_path
    return make


def test_auth_required_and_errors_have_shape(gw):
    app, h, _, _ = gw()
    with TestClient(app) as c:
        r = c.post("/v1/judge/reward", json={"instruction": "a", "response": "b"})
        assert r.status_code == 401
        e = r.json()["error"]
        assert e["code"] == "unauthenticated" and e["request_id"] == r.headers["x-request-id"]
        assert c.post("/v1/judge/reward", json={"instruction": "a", "response": "b"},
                      headers={"Authorization": "Bearer lorde_sk_nope"}).status_code == 401
        r = c.post("/v1/judge/reward", json={"instruction": "a"}, headers=h)
        assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request"
        r = c.post("/v1/judge/grounding", json={"source_text": "x" * 24_001, "question": "q", "answer": "a"}, headers=h)
        assert r.status_code == 413
        assert c.get("/nope").json()["error"]["code"] == "not_found"


def test_judge_grounding_matches_spec_example(gw):
    app, h, seen, tmp = gw()
    with TestClient(app) as c:
        r = c.post("/v1/judge/grounding", headers=h,
                   json={"source_text": SRC, "question": "How long is the Niger River?",
                         "answer": "It is about 6,650 km long.", "threshold": 0.7})
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["verdict"] == "hallucinated" and b["decision"] == "reject" and b["grounding_score"] == 0.2
        assert b["model"] == "lorde-tiny-aya@v1.0.0" and b["template"] == contract.template_id("grounding")
        assert r.headers["x-lorde-template"] == b["template"] and b["request_id"] == r.headers["x-request-id"]
        # the gateway always asks vLLM for the gate's schema and renders CuratorKIT's exact prompt
        assert seen[-1]["response_format"]["json_schema"]["name"] == "lorde_grounding"
        assert seen[-1]["messages"][0]["content"] == contract.render_grounding(SRC, "How long is the Niger River?",
                                                                                "It is about 6,650 km long.")
        ok = c.post("/v1/judge/reward", headers=h, json={"instruction": "Explain X",
                                                          "response": "A careful, complete and well-structured explanation of X."})
        assert ok.json()["decision"] == "pass" and ok.json()["dimension_scores"]["honesty"] == 0.8
    usage = [json.loads(x) for x in (tmp / "usage.jsonl").read_text().splitlines()]
    assert usage and all("instruction" not in u and "source_text" not in u for u in usage)
    assert usage[0]["judgments"] == 1 and usage[0]["key_prefix"].startswith("lorde_sk_")


def test_batch_and_limits(gw):
    app, h, _, _ = gw(rpm=5)
    with TestClient(app) as c:
        items = [{"instruction": f"q{i}", "response": "A long enough answer to be judged as good."} for i in range(3)]
        r = c.post("/v1/judge/batch", headers=h, json={"kind": "reward", "items": items})
        assert r.status_code == 200 and r.json()["summary"] == {"passed": 3, "rejected": 0, "errors": 0, "total": 3}
        r = c.post("/v1/judge/batch", headers=h, json={"kind": "reward", "items": items})
        assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
        r = c.post("/v1/judge/batch", headers=h, json={"kind": "grounding", "items": [{"instruction": "x"}]})
        assert r.status_code in (400, 429)


def test_openai_proxy_injects_schema_by_fingerprint(gw):
    app, h, seen, _ = gw(style="chatty")
    with TestClient(app) as c:
        prompt = contract.render_reward("Explain X", "A careful, complete and well-structured explanation of X.")
        r = c.post("/v1/chat/completions", headers=h,
                   json={"model": "anything", "temperature": 0.9, "messages": [{"role": "user", "content": prompt}]})
        assert r.status_code == 200 and r.headers["x-lorde-template"] == contract.template_id("reward")
        assert seen[-1]["response_format"]["json_schema"]["name"] == "lorde_reward"
        assert seen[-1]["temperature"] == 0.3 and seen[-1]["model"] == "lorde"
        # the chatty stub only returns clean JSON because the schema was injected: CuratorKIT can parse it
        assert contract.parse_reply("reward", r.json()["choices"][0]["message"]["content"]).ok
        r = c.post("/v1/chat/completions", headers=h, json={"messages": [{"role": "user", "content": "hi"}]})
        assert r.headers["x-lorde-template"] == "unknown" and "response_format" not in seen[-1]
        assert c.get("/v1/models", headers=h).json()["data"][0]["id"] == "lorde"
        m = c.get("/metrics").text
        assert 'lorde_template_total{match="unknown"} 1.0' in m and "lorde_judgments_total" in m


def test_scopes_and_unavailable_upstream(gw):
    app, h, _, _ = gw(scopes=("jobs:read",))
    with TestClient(app) as c:
        assert c.post("/v1/judge/reward", headers=h, json={"instruction": "a", "response": "b"}).status_code == 403
    app, h, _, _ = gw(fail=True)
    with TestClient(app) as c:
        r = c.post("/v1/judge/reward", headers=h, json={"instruction": "a", "response": "b"})
        assert r.status_code == 503 and r.json()["error"]["code"] == "model_unavailable" and r.headers["retry-after"]
        assert c.get("/healthz").json()["status"] == "ok"


def test_revocation_takes_effect(gw, tmp_path):
    app, h, _, _ = gw()
    store = KeyStore(tmp_path / "keys.json", PEPPER)
    with TestClient(app) as c:
        assert c.get("/v1/models", headers=h).status_code == 200
        prefix = store.list()[0].prefix
        assert store.revoke(prefix)
        app.gw.keys._cache.clear()  # another process revoked it; the 60 s cache would otherwise hold it
        assert c.get("/v1/models", headers=h).status_code == 401


def mcp_call(c, h, method, params=None, i=1):
    hh = {**h, "Accept": "application/json, text/event-stream", "Content-Type": "application/json",
          "MCP-Protocol-Version": "2025-06-18"}
    return c.post("/mcp", headers=hh, json={"jsonrpc": "2.0", "id": i, "method": method, "params": params or {}})


def test_mcp_tools(gw):
    app, h, _, tmp = gw()
    with TestClient(app) as c:
        assert mcp_call(c, {}, "tools/list").status_code == 401
        init = mcp_call(c, h, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                              "clientInfo": {"name": "t", "version": "0"}})
        assert init.status_code == 200, init.text
        tools = mcp_call(c, h, "tools/list", i=2).json()["result"]["tools"]
        assert {t["name"] for t in tools} >= {"lorde_judge_grounding", "lorde_judge_reward", "lorde_judge_batch",
                                               "lorde_model_info"}
        r = mcp_call(c, h, "tools/call", {"name": "lorde_judge_grounding", "arguments": {
            "source_text": SRC, "question": "How long?", "answer": "It is about 6,650 km long."}}, i=3).json()
        out = r["result"].get("structuredContent") or json.loads(r["result"]["content"][0]["text"])
        assert out["decision"] == "reject" and out["verdict"] == "hallucinated"
        info = mcp_call(c, h, "tools/call", {"name": "lorde_model_info", "arguments": {}}, i=4).json()
        assert "lorde-tiny-aya@v1.0.0" in json.dumps(info)
    assert any(json.loads(x)["route"] == "mcp:grounding" for x in (tmp / "usage.jsonl").read_text().splitlines())
