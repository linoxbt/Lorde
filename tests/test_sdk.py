"""SDK, CLI and CuratorKIT preset against a live Gateway (uvicorn) whose vLLM is a chatty stub."""

import asyncio
import json
import socket
import threading
import time

import pytest
import uvicorn
from lorde import AsyncLorde, Lorde, LordeAPIError
from lorde.cli import main as cli
from lorde.curatorkit import judge_llm
from test_gateway import PEPPER, SRC, stub_vllm

from gateway.app import create_app
from gateway.keys import KeyStore
from gateway.settings import Settings


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gw")
    s = Settings(key_pepper=PEPPER, keys_file=str(tmp / "keys.json"), model_revision="v1.0.0")
    store = KeyStore(s.keys_file, PEPPER)
    key, _ = store.create("sdk", "org1", ["judge"], 6000)
    slow, _ = store.create("slow", "org1", ["judge"], 1)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(s, transport=stub_vllm("chatty")), port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield {"url": f"http://127.0.0.1:{port}", "key": key, "slow": slow}
    server.should_exit = True
    t.join(5)


def test_sync_client(live):
    with Lorde(api_key=live["key"], base_url=live["url"]) as c:
        r = c.judge.grounding(SRC, "How long is the Niger River?", "It is about 6,650 km long.")
        assert r.verdict == "hallucinated" and not r.passed and r.model == "lorde-tiny-aya@v1.0.0"
        r = c.judge.reward("Explain X", "A careful, complete and well-structured explanation of X.")
        assert r.passed and r.dimension_scores.helpfulness == 0.8
        b = c.judge.batch("grounding", [{"source_text": SRC, "question": "q", "answer": "The Niger River is the main river"},
                                        {"source_text": SRC, "question": "q", "answer": "It is 9 km long."}])
        assert b.summary["passed"] == 1 and b.summary["rejected"] == 1


def test_errors_and_rate_limits(live):
    with pytest.raises(LordeAPIError) as e:
        Lorde(api_key="lorde_sk_" + "x" * 43, base_url=live["url"]).judge.reward("a", "b")
    assert e.value.status == 401 and e.value.code == "unauthenticated" and e.value.request_id
    c = Lorde(api_key=live["slow"], base_url=live["url"], max_retries=0)
    c.judge.reward("a", "b")
    with pytest.raises(LordeAPIError) as e:
        c.judge.reward("a", "b")
    assert e.value.status == 429 and e.value.retryable


def test_async_client(live):
    async def go():
        async with AsyncLorde(api_key=live["key"], base_url=live["url"]) as c:
            rs = await asyncio.gather(*(c.judge.reward(f"q{i}", "A long enough answer to be judged as good.")
                                        for i in range(5)))
            return [r.decision for r in rs]
    assert asyncio.run(go()) == ["pass"] * 5


def test_cli(live, monkeypatch, capsys):
    monkeypatch.setenv("LORDE_API_KEY", live["key"])
    monkeypatch.setenv("LORDE_BASE_URL", live["url"])
    assert cli(["judge", "grounding", "--source", SRC, "--question", "q", "--answer", "It is 9 km long."]) == 1
    assert json.loads(capsys.readouterr().out)["verdict"] == "hallucinated"
    assert cli(["judge", "reward", "--instruction", "x", "--response", "A long enough answer to be judged as good."]) == 0


def test_curatorkit_preset_through_gateway(live, tmp_path, monkeypatch):
    """FR-04: CuratorKIT points at Lorde with no code change; schema injection makes every reply usable."""
    from curatorkit import Curator, CuratorConfig

    monkeypatch.setenv("LITELLM_LOG", "ERROR")
    rows = [{"instruction": "How long is the Niger River?", "input": SRC,
             "output": "The Niger River is the main river of West Africa, about 4,180 km long."},
            {"instruction": "How long is the Niger River?", "input": SRC, "output": "It is about 6,650 km long."}]
    data = tmp_path / "d.jsonl"
    data.write_text("\n".join(json.dumps(r) for r in rows))
    cfg = CuratorConfig(dataset=str(data), output_dir=str(tmp_path / "out"), hallucination_threshold=0.7,
                        reward_threshold=0.7, judge_llm=judge_llm(live["key"], live["url"], concurrency=2),
                        dedup="none", clean=False)
    res = Curator(cfg).run()
    reasons = [r.rejection_reason for r in res.rejected]
    assert not any(x.startswith("judge_error") for x in reasons), reasons  # the stub is chatty: proof of injection
    assert len(res.passed) == 1 and reasons[0].startswith("hallucination")


def test_stdio_mcp_builds():
    pytest.importorskip("mcp")
    from lorde.mcp import build

    tools = asyncio.run(build(api_key="lorde_sk_test", base_url="http://x").list_tools())
    assert {t.name for t in tools} == {"lorde_judge_grounding", "lorde_judge_reward", "lorde_judge_batch",
                                       "lorde_create_gating_job", "lorde_job_status", "lorde_model_info"}


def test_stdio_mcp_model_info_against_gateway(live):
    """M10: the stdio server's lorde_model_info reaches the Gateway's model endpoint."""
    pytest.importorskip("mcp")
    from lorde.mcp import build

    async def go():
        m = build(api_key=live["key"], base_url=live["url"])
        return await m.call_tool("lorde_model_info", {})
    out = json.dumps(asyncio.run(go()), default=str)
    assert "lorde-tiny-aya@v1.0.0" in out and "api_version" in out


def scripted(responses):
    """An httpx client that answers each request from a script and records what it saw."""
    import httpx

    seen = []

    def handler(request):
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"api_version": 1})
        seen.append((request.method, request.url.path, request.headers.get("idempotency-key")))
        step = responses.pop(0)
        if isinstance(step, Exception):
            raise step
        return step
    return httpx.Client(base_url="http://gw", transport=httpx.MockTransport(handler)), seen


def test_sdk_does_not_resend_posts_that_may_have_run(monkeypatch):
    """Perf 12: a read timeout on a judge POST is not retried; connect errors, 429 and 503+Retry-After are."""
    import httpx
    import lorde.client as lc

    monkeypatch.setattr(lc.time, "sleep", lambda s: None)
    ok = httpx.Response(200, json={"overall_score": 0.8, "decision": "pass", "threshold": 0.7, "model": "m",
                                   "template": "t", "latency_ms": 1, "request_id": "r"})
    http, seen = scripted([httpx.ReadTimeout("slow")])
    with pytest.raises(httpx.ReadTimeout):
        Lorde(api_key="k", http_client=http).judge.reward("a", "b")
    assert len(seen) == 1
    http, seen = scripted([httpx.ConnectError("down"), httpx.Response(429, headers={"retry-after": "1"}, json={}),
                           httpx.Response(503, headers={"retry-after": "1"}, json={}), ok])
    assert Lorde(api_key="k", http_client=http).judge.reward("a", "b").passed
    assert len(seen) == 4
    http, seen = scripted([httpx.Response(502, json={})])
    with pytest.raises(LordeAPIError):
        Lorde(api_key="k", http_client=http).judge.reward("a", "b")
    assert len(seen) == 1
    # job creation carries an Idempotency-Key, so it is retried after a read timeout, with the same key
    job = {"id": "j1", "status": "queued"}
    http, seen = scripted([httpx.ReadTimeout("slow"), httpx.Response(200, json=job)])
    assert Lorde(api_key="k", http_client=http).jobs.create(dataset_url="hf://datasets/a/b").id == "j1"
    assert len(seen) == 2 and seen[0][2] and seen[0][2] == seen[1][2]
