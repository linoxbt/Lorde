"""Tenant fairness (audit N1, N2, N10): per-org upstream slots, the bounded wait queue, the daily judgment quota,
the per-IP bucket in front of key lookups, and the structured-output schema limit."""

import asyncio
import json
import os
import time
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from stub_openai import judge_reply
from test_gateway import PEPPER, mcp_call

from gateway.app import create_app
from gateway.keys import KeyStore
from gateway.quota import LocalQuota, RedisQuota, seconds_to_midnight
from gateway.settings import Settings
from gateway.slots import Busy, LocalSlots

REDIS = os.environ.get("LORDE_TEST_REDIS_URL")
REWARD = {"instruction": "Explain X", "response": "X is a thing."}


def held_upstream(release: asyncio.Event, entered: list):
    """Stub Serve: a prompt containing HOLD blocks until ``release`` is set."""
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "lorde"}]})
        req = json.loads(request.content)
        prompt = req["messages"][-1]["content"]
        entered.append(prompt)
        if "HOLD" in prompt:
            await release.wait()
        text = judge_reply(prompt, "plain") or "hello"
        return httpx.Response(200, json={"id": "x", "object": "chat.completion", "model": req["model"],
                                         "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                                      "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    return httpx.MockTransport(handler)


def make(tmp_path, orgs=("orgA", "orgB", "orgC"), transport=None, **kw):
    s = Settings(key_pepper=PEPPER, keys_file=str(tmp_path / "keys.json"), usage_file=str(tmp_path / "usage.jsonl"),
                 model_revision="v1.0.0", vllm_base_url="http://vllm/v1", redis_url=None, **kw)
    store = KeyStore(s.keys_file, PEPPER)
    heads = {}
    for org in orgs:
        key, _ = store.create(org, org, ["judge"], 6000)
        heads[org] = {"Authorization": f"Bearer {key}"}
    if transport is None:
        from test_gateway import stub_vllm

        transport = stub_vllm()
    return create_app(s, transport=transport), heads


async def until(cond, timeout=5.0):
    t0 = time.monotonic()
    while not cond():
        if time.monotonic() - t0 > timeout:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


def hold(body=REWARD):
    return {**body, "instruction": "HOLD " + body["instruction"]}


def test_per_org_cap_lets_another_org_through(tmp_path):
    async def go():
        release, entered = asyncio.Event(), []
        app, h = make(tmp_path, transport=held_upstream(release, entered), upstream_concurrency=3,
                      upstream_queue_timeout_s=10)
        assert app.gw.s.api_upstream_slots == 2  # one of three kept for the job worker
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            a1 = asyncio.create_task(c.post("/v1/judge/reward", headers=h["orgA"], json=hold()))
            await until(lambda: len(entered) == 1)
            a2 = asyncio.create_task(c.post("/v1/judge/reward", headers=h["orgA"], json=REWARD))
            await until(lambda: app.gw.upstream.slots.orgs["orgA"].waiting == 1)
            b1 = await c.post("/v1/judge/reward", headers=h["orgB"], json=REWARD)  # a global slot is free
            assert b1.status_code == 200, b1.text
            assert len(entered) == 2 and not a2.done()  # org A's second call still waits for A's own slot
            release.set()
            r1, r2 = await a1, await a2
            assert r1.status_code == r2.status_code == 200
            assert len(entered) == 3
        assert app.gw.upstream.slots.orgs == {} and app.gw.upstream.slots.global_slot.used == 0
    asyncio.run(go())


def test_queue_timeout_is_429_and_not_a_breaker_failure(tmp_path):
    async def go():
        release, entered = asyncio.Event(), []
        app, h = make(tmp_path, transport=held_upstream(release, entered), upstream_concurrency=1,
                      upstream_queue_timeout_s=0.3, breaker_failures=1)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            a1 = asyncio.create_task(c.post("/v1/judge/reward", headers=h["orgA"], json=hold()))
            await until(lambda: len(entered) == 1)
            for org in ("orgB", "orgA"):  # waiting for the global slot, and for org A's own slot
                t0 = time.monotonic()
                r = await c.post("/v1/judge/reward", headers=h[org], json=REWARD)
                assert r.status_code == 429 and 0.25 < time.monotonic() - t0 < 3
                e = r.json()["error"]
                assert e["code"] == "rate_limited" and e["message"].startswith("model busy, retry later")
                assert int(r.headers["retry-after"]) >= 1
            assert not app.gw.upstream.breaker.is_open and app.gw.upstream.breaker._count == 0
            r = await c.post("/v1/chat/completions", headers=h["orgC"], json={
                "messages": [{"role": "user", "content": "hi"}]})
            assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
            release.set()
            assert (await a1).status_code == 200
            assert (await c.post("/v1/judge/reward", headers=h["orgB"], json=REWARD)).status_code == 200
        assert not app.gw.upstream.breaker.is_open
        assert app.gw.upstream.slots.global_slot.used == 0 and not app.gw.upstream.slots.global_slot.waiters
    asyncio.run(go())


def test_max_waiters_refuses_at_once(tmp_path):
    async def go():
        release, entered = asyncio.Event(), []
        app, h = make(tmp_path, transport=held_upstream(release, entered), upstream_concurrency=1,
                      upstream_queue_timeout_s=10, upstream_max_waiters=1)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            a1 = asyncio.create_task(c.post("/v1/judge/reward", headers=h["orgA"], json=hold()))
            await until(lambda: len(entered) == 1)
            b1 = asyncio.create_task(c.post("/v1/judge/reward", headers=h["orgB"], json=REWARD))
            await until(lambda: app.gw.upstream.slots.global_slot.waiting == 1)
            t0 = time.monotonic()
            r = await c.post("/v1/judge/reward", headers=h["orgC"], json=REWARD)
            assert r.status_code == 429 and time.monotonic() - t0 < 1
            assert r.json()["error"]["code"] == "rate_limited" and "queue is full" in r.json()["error"]["message"]
            release.set()
            assert (await a1).status_code == 200 and (await b1).status_code == 200
    asyncio.run(go())


def test_batch_runs_within_its_org_slot_and_whole_batch_busy_is_429(tmp_path):
    async def go():
        release, entered = asyncio.Event(), []
        app, h = make(tmp_path, transport=held_upstream(release, entered), upstream_concurrency=1,
                      upstream_queue_timeout_s=0.3)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            # five items, one at a time through org A's single slot: no item queues against its own batch
            r = await c.post("/v1/judge/batch", headers=h["orgA"], json={"kind": "reward", "items": [REWARD] * 5})
            assert r.status_code == 200 and r.json()["summary"]["errors"] == 0
            a1 = asyncio.create_task(c.post("/v1/judge/reward", headers=h["orgA"], json=hold()))
            await until(lambda: len(entered) == 6)
            r = await c.post("/v1/judge/batch", headers=h["orgB"], json={"kind": "reward", "items": [REWARD] * 3})
            assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
            release.set()
            await a1
    asyncio.run(go())


def test_slots_survive_cancellation_and_hand_over_in_order():
    async def go():
        slots = LocalSlots(1, 1, 8, 5)
        order = []

        async def use(org, tag, hold_s=0.05):
            async with slots.acquire(org):
                order.append(tag)
                await asyncio.sleep(hold_s)

        first = asyncio.create_task(use("a", "a1", 0.2))
        await asyncio.sleep(0.01)
        waiters = [asyncio.create_task(use(o, o)) for o in ("b", "c", "d")]
        await asyncio.sleep(0.01)
        waiters[1].cancel()  # c gives up while queued: its place is not lost or leaked
        await asyncio.gather(first, *waiters, return_exceptions=True)
        assert order == ["a1", "b", "d"]
        assert slots.global_slot.used == 0 and not slots.global_slot.waiters and slots.orgs == {}
        slots0 = LocalSlots(1, 1, 8, 0)  # no waiting at all: a held slot is busy at once
        async with slots0.acquire("x"):
            with pytest.raises(Busy):
                async with slots0.acquire("y"):
                    pass
        assert slots0.global_slot.used == 0 and slots0.orgs == {}
    asyncio.run(go())


# ------------------------------------------------------------------------------------------- quota (N1b)

def test_daily_quota_covers_rest_batch_chat_and_mcp(tmp_path):
    app, h = make(tmp_path, orgs=("orgQ", "orgR"), org_daily_judgments=4)
    with TestClient(app) as c:
        assert c.post("/v1/judge/reward", headers=h["orgQ"], json=REWARD).status_code == 200
        r = c.post("/v1/judge/batch", headers=h["orgQ"], json={"kind": "reward", "items": [REWARD] * 2})
        assert r.status_code == 200
        r = c.post("/v1/judge/batch", headers=h["orgQ"], json={"kind": "reward", "items": [REWARD] * 2})
        assert r.status_code == 429  # 3 used, 1 left: a batch of 2 does not fit and spends nothing
        e = r.json()["error"]
        assert e["code"] == "quota_exceeded" and e["limit"] == 4 and e["used"] == 3 and "00:00 UTC" in e["message"]
        assert abs(int(r.headers["retry-after"]) - seconds_to_midnight()) <= 2
        assert c.post("/v1/chat/completions", headers=h["orgQ"],
                      json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 200
        r = c.post("/v1/judge/reward", headers=h["orgQ"], json=REWARD)
        assert r.status_code == 429 and r.json()["error"]["code"] == "quota_exceeded"
        r = mcp_call(c, h["orgQ"], "tools/call", {"name": "lorde_judge_reward", "arguments": REWARD}).json()
        assert r["result"]["isError"] and "quota_exceeded: " in r["result"]["content"][0]["text"]
        # another org has its own quota; a batch over the whole quota can never fit (413)
        assert c.post("/v1/judge/reward", headers=h["orgR"], json=REWARD).status_code == 200
        r = c.post("/v1/judge/batch", headers=h["orgR"], json={"kind": "reward", "items": [REWARD] * 5})
        assert r.status_code == 413


def test_quota_is_refunded_when_the_model_was_not_reached(tmp_path):
    from test_gateway import stub_vllm

    app, h = make(tmp_path, orgs=("orgF",), transport=stub_vllm(fail=True), org_daily_judgments=2)
    with TestClient(app) as c:
        for _ in range(4):  # Serve fails (503): nothing is charged
            assert c.post("/v1/judge/reward", headers=h["orgF"], json=REWARD).status_code == 503
        r = c.post("/v1/judge/batch", headers=h["orgF"], json={"kind": "reward", "items": [REWARD] * 2})
        assert r.status_code == 200 and r.json()["summary"]["errors"] == 2
        assert app.gw.quota.used_sync("orgF") == 0
    app, h = make(tmp_path / "unlimited", orgs=("orgU",), org_daily_judgments=0)
    with TestClient(app) as c:
        for _ in range(3):
            assert c.post("/v1/judge/reward", headers=h["orgU"], json=REWARD).status_code == 200


def test_local_quota_counts_per_org_and_day():
    q = LocalQuota()
    assert q.take_sync("a", 3, 4) == (True, 3) and q.take_sync("a", 2, 4) == (False, 3)
    assert q.take_sync("b", 4, 4) == (True, 4) and q.add_sync("a", -10) == 0 and q.take_sync("a", 4, 4)[0]
    assert q.take_sync("a", 10**6, 0) == (True, 4 + 10**6)  # 0 = unlimited


@pytest.mark.skipif(not REDIS, reason="set LORDE_TEST_REDIS_URL to run Redis tests")
def test_redis_quota_is_shared_and_fails_open():
    async def go():
        org = f"org-{uuid.uuid4().hex}"
        a, b = RedisQuota(REDIS), RedisQuota(REDIS)  # the Gateway and the worker
        assert await a.take(org, 3, 5) == (True, 3)
        assert await b.take(org, 3, 5) == (False, 3)
        assert await b.add(org, 2) == 5 and await a.used(org) == 5
        assert (await a.take(org, 1, 5))[0] is False and await a.add(org, -9) == 0
        ttl = await a.r.ttl(a.key(org))
        await a.close()
        await b.close()
        down = RedisQuota("redis://127.0.0.1:1/0", timeout_s=0.2)  # unreachable: in-process, still limited
        assert await down.take(org, 2, 3) == (True, 2) and (await down.take(org, 2, 3))[0] is False
        assert down.degraded
        await down.close()
        return ttl

    assert 0 < asyncio.run(go()) <= 172800


# ------------------------------------------------------------------------------------------- per-IP (N2)

def test_ip_bucket_stops_a_prefix_flood_before_any_key_lookup(tmp_path):
    app, _ = make(tmp_path, orgs=(), ip_rpm=5)
    looked = []
    real = app.gw.keys.alookup

    async def counting(token):
        looked.append(token)
        return await real(token)

    app.gw.keys.alookup = counting
    with TestClient(app) as c:
        codes = []
        for _ in range(20):  # distinct, well formed, unknown keys: the negative cache never helps
            fake = "lorde_sk_" + uuid.uuid4().hex + "Ab1"
            r = c.post("/v1/judge/reward", headers={"Authorization": f"Bearer {fake}"}, json=REWARD)
            codes.append(r.status_code)
        assert codes[:5] == [401] * 5 and codes[5:] == [429] * 15
        assert len(looked) == 5
        r = c.get("/v1/models", headers={"Authorization": "Bearer lorde_sk_" + "x" * 40})
        assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited" and "address" in \
            r.json()["error"]["message"] and int(r.headers["retry-after"]) >= 1
        assert c.get("/healthz").status_code == 200  # unkeyed routes are not limited


def test_client_ip_trusts_forwarded_headers_only_from_trusted_proxies(tmp_path):
    app, _ = make(tmp_path, orgs=(), trusted_proxies=["10.0.0.0/8"], relay_secret="r" * 32)
    gw = app.gw
    xff = {"x-forwarded-for": "203.0.113.5, 10.1.2.3"}
    assert gw.client_ip({"client": ("10.9.9.9", 1)}, xff) == "203.0.113.5"
    assert gw.client_ip({"client": ("198.51.100.7", 1)}, xff) == "198.51.100.7"  # not a proxy: header ignored
    assert gw.client_ip({"client": ("10.9.9.9", 1)}, {"x-forwarded-for": "junk"}) == "10.9.9.9"
    relay = {"x-lorde-relay-secret": "r" * 32, "x-lorde-client-ip": "192.0.2.44"}
    assert gw.client_ip({"client": ("54.1.2.3", 1)}, relay) == "192.0.2.44"
    assert gw.client_ip({"client": ("54.1.2.3", 1)}, {**relay, "x-lorde-relay-secret": "nope"}) == "54.1.2.3"
    assert gw.client_ip({"client": ("2001:db8:1:2:3:4:5:6", 1)}, {}) == "2001:db8:1:2::/64"
    assert gw.client_ip({"client": ("::ffff:198.51.100.9", 1)}, {}) == "198.51.100.9"
    app, _ = make(tmp_path / "all", orgs=(), trusted_proxies=["*"])
    assert app.gw.client_ip({"client": ("100.64.0.2", 1)}, xff) == "203.0.113.5"
    app, _ = make(tmp_path / "none", orgs=())  # default: the peer as uvicorn reports it
    assert app.gw.client_ip({"client": ("100.64.0.2", 1)}, xff) == "100.64.0.2"


# ------------------------------------------------------------------------------------------- schemas (N10)

def test_structured_output_schema_size_and_depth_are_limited(tmp_path):
    app, h = make(tmp_path, orgs=("orgS",))
    msgs = [{"role": "user", "content": "hi"}]
    deep: dict = {"type": "string"}
    for _ in range(13):
        deep = {"type": "object", "properties": {"x": deep}}
    big = {"type": "json_schema", "json_schema": {"name": "x", "schema": {
        "type": "object", "properties": {f"p{i}": {"type": "string", "description": "d" * 100} for i in range(200)}}}}
    with TestClient(app) as c:
        ok = {"type": "json_schema", "json_schema": {"name": "x", "schema": {"type": "object"}}}
        assert c.post("/v1/chat/completions", headers=h["orgS"], json={"messages": msgs, "response_format": ok}
                      ).status_code == 200
        for field, value, why in [("response_format", big, "16 KB"), ("guided_json", deep, "12 levels"),
                                  ("structured_outputs", {"json": deep}, "12 levels"),
                                  ("guided_json", json.dumps(deep), "12 levels"),
                                  ("guided_json", "x" * 17_000, "16 KB")]:
            r = c.post("/v1/chat/completions", headers=h["orgS"], json={"messages": msgs, field: value})
            assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request", (field, r.text)
            assert why in r.json()["error"]["message"]


# --- regressions from the fairness review -------------------------------------------------------------------

def test_cancelled_org_waiter_does_not_evict_a_newer_slot():
    """A waiter that gives up must not drop the org's slot entry if a newer request already replaced it,
    or two requests of one org would hold upstream slots at once (limit 1)."""
    import asyncio

    from gateway.slots import LocalSlots

    async def main():
        s = LocalSlots(global_slots=4, org_slots=1, max_waiters=32, timeout_s=5)
        inside = peak = 0
        a_go, c_go = asyncio.Event(), asyncio.Event()

        async def hold(ev):
            nonlocal inside, peak
            async with s.acquire("org"):
                inside += 1
                peak = max(peak, inside)
                await ev.wait()
                inside -= 1

        a = asyncio.create_task(hold(a_go))
        await asyncio.sleep(0)
        b = asyncio.create_task(hold(asyncio.Event()))
        await asyncio.sleep(0)
        a_go.set()
        c = asyncio.create_task(hold(c_go))
        b.cancel()
        await a
        await asyncio.gather(b, return_exceptions=True)
        d = asyncio.create_task(hold(asyncio.Event()))
        await asyncio.sleep(0.01)
        assert peak == 1
        for t in (c, d):
            t.cancel()
        await asyncio.gather(c, d, return_exceptions=True)

    asyncio.run(main())


def test_quota_raise_to_is_idempotent_under_concurrency():
    """Seeding the counter from metered usage is an atomic max, so concurrent first requests cannot add the
    difference many times (it used to reach 20x the metered value)."""
    import asyncio

    from gateway.quota import LocalQuota

    async def main():
        q = LocalQuota()
        await q.add("o", 10)
        await asyncio.gather(*(q.raise_to("o", 100) for _ in range(20)))
        assert await q.used("o") == 100
        assert await q.raise_to("o", 50) == 100  # never lowers

    asyncio.run(main())


def test_redis_quota_raise_to_is_atomic():
    import asyncio
    import os

    url = os.environ.get("LORDE_TEST_REDIS_URL")
    if not url:
        pytest.skip("set LORDE_TEST_REDIS_URL")
    from gateway.quota import RedisQuota

    async def main():
        q = RedisQuota(url)
        org = "raise-" + os.urandom(4).hex()
        await q.add(org, 10)
        await asyncio.gather(*(q.raise_to(org, 100) for _ in range(20)))
        assert await q.used(org) == 100
        await q.close()

    asyncio.run(main())


def test_ip_buckets_are_hard_capped_lru():
    from gateway.limits import TokenBuckets

    b = TokenBuckets()
    b.MAX_KEYS = 50
    for i in range(500):
        b.take(f"ip:{i}", 300)
    assert len(b._state) == 50 and "ip:499" in b._state and "ip:0" not in b._state


def test_upstream_timeouts_are_charged_but_unreached_calls_refunded():
    from gateway.errors import ModelUnavailable, RateLimited, refundable

    timeout = ModelUnavailable("upstream timeout")
    timeout.charged = True
    assert not refundable(timeout)
    assert refundable(ModelUnavailable("upstream connection failed"))
    assert refundable(RateLimited("model busy, retry later"))


def test_relay_secret_must_be_long(monkeypatch):
    from gateway.settings import Settings

    monkeypatch.setenv("LORDE_KEY_PEPPER", "p" * 40)
    monkeypatch.setenv("LORDE_RELAY_SECRET", "short")
    with pytest.raises(RuntimeError, match="LORDE_RELAY_SECRET"):
        Settings().check()
