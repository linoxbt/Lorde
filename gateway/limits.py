"""Per-key token buckets. In-process (one replica); Redis-backed buckets replace this when scaling out."""

from __future__ import annotations

import threading
import time


class TokenBuckets:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_ts)

    def take(self, key: str, per_minute: int, cost: int = 1) -> float:
        """Spend ``cost`` tokens; return 0 if allowed, else seconds until enough tokens refill."""
        rate = per_minute / 60.0
        cap = float(per_minute)
        now = time.monotonic()
        with self._lock:
            tokens, last = self._state.get(key, (cap, now))
            tokens = min(cap, tokens + (now - last) * rate)
            if cost > cap:
                return float("inf")
            if tokens >= cost:
                self._state[key] = (tokens - cost, now)
                return 0.0
            self._state[key] = (tokens, now)
            return (cost - tokens) / rate


class LocalBuckets(TokenBuckets):
    async def atake(self, key: str, per_minute: int, cost: int = 1) -> float:
        return self.take(key, per_minute, cost)


# Atomic token bucket in Redis: shared by every Gateway replica.
_LUA = """
local cap = tonumber(ARGV[1]) local rate = tonumber(ARGV[2]) local cost = tonumber(ARGV[3])
local now = tonumber(ARGV[4])
local b = redis.call('HMGET', KEYS[1], 't', 'ts')
local tokens = tonumber(b[1]) or cap local ts = tonumber(b[2]) or now
tokens = math.min(cap, tokens + (now - ts) * rate)
local wait = 0
if cost > cap then wait = -1 elseif tokens >= cost then tokens = tokens - cost else wait = (cost - tokens) / rate end
redis.call('HSET', KEYS[1], 't', tokens, 'ts', now)
redis.call('EXPIRE', KEYS[1], math.ceil(cap / rate) + 60)
return tostring(wait)
"""


class RedisBuckets:
    def __init__(self, url: str) -> None:
        import redis.asyncio as redis

        self.r = redis.from_url(url)
        self.script = self.r.register_script(_LUA)

    async def atake(self, key: str, per_minute: int, cost: int = 1) -> float:
        wait = float(await self.script(keys=[f"lorde:rl:{key}"],
                                       args=[per_minute, per_minute / 60.0, cost, time.time()]))
        return float("inf") if wait < 0 else wait


def make_buckets(redis_url: str | None):
    return RedisBuckets(redis_url) if redis_url else LocalBuckets()
