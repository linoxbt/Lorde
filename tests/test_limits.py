"""Token buckets: in-process, and Redis-backed (shared across Gateway replicas)."""

import asyncio
import os

import pytest

from gateway.limits import LocalBuckets, RedisBuckets

REDIS = os.environ.get("LORDE_TEST_REDIS_URL")


def test_local_bucket():
    b = LocalBuckets()
    assert [b.take("k", 3) for _ in range(3)] == [0.0, 0.0, 0.0]
    assert 0 < b.take("k", 3) <= 20.5
    assert b.take("other", 3) == 0.0
    assert b.take("k2", 3, cost=4) == float("inf")


@pytest.mark.skipif(not REDIS, reason="set LORDE_TEST_REDIS_URL to run Redis tests")
def test_redis_bucket_is_shared_between_replicas():
    import uuid

    async def go():
        key = f"t-{uuid.uuid4().hex}"
        a, b = RedisBuckets(REDIS), RedisBuckets(REDIS)   # two Gateway replicas
        got = [await a.atake(key, 4), await b.atake(key, 4), await a.atake(key, 4, cost=2)]
        wait = await b.atake(key, 4)
        too_big = await a.atake(key + "x", 4, cost=5)
        await a.r.aclose()
        await b.r.aclose()
        return got, wait, too_big

    got, wait, too_big = asyncio.run(go())
    assert got == [0.0, 0.0, 0.0] and 0 < wait <= 15.5 and too_big == float("inf")


def test_local_buckets_forget_idle_keys(monkeypatch):
    import time as _time

    b = LocalBuckets()
    b.MAX_KEYS = 10
    for i in range(11):
        b.take(f"ip:{i}", 5)
    now = _time.monotonic()
    monkeypatch.setattr("gateway.limits.time.monotonic", lambda: now + 61)
    b.take("ip:new", 5)
    assert list(b._state) == ["ip:new"]
