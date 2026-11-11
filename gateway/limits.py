"""Per-key token buckets: Redis-backed (shared by every replica) when REDIS_URL is set, else in-process.

If Redis is unreachable the Gateway fails open to an in-process bucket per replica (same limits, but not
shared across replicas) and logs a warning at most every 30 s, so a Redis outage never takes judging down.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict

log = logging.getLogger("lorde.limits")


class TokenBuckets:
    MAX_KEYS = 100_000

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: OrderedDict[str, tuple[float, float]] = OrderedDict()  # key -> (tokens, last_ts), LRU order

    def take(self, key: str, per_minute: int, cost: int = 1) -> float:
        """Spend ``cost`` tokens; return 0 if allowed, else seconds until enough tokens refill."""
        rate = per_minute / 60.0
        cap = float(per_minute)
        now = time.monotonic()
        with self._lock:
            tokens, last = self._state.get(key, (cap, now))
            self._state[key] = (tokens, last)
            self._state.move_to_end(key)
            while len(self._state) > self.MAX_KEYS:  # hard cap, O(1): forget the least recently used bucket
                self._state.popitem(last=False)
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

    async def ping(self) -> bool:
        return True


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
    def __init__(self, url: str, timeout_s: float = 1.0) -> None:
        import redis.asyncio as redis

        self.r = redis.from_url(url, socket_timeout=timeout_s, socket_connect_timeout=timeout_s)
        self.script = self.r.register_script(_LUA)
        self.fallback = LocalBuckets()
        self._warned = 0.0

    async def atake(self, key: str, per_minute: int, cost: int = 1) -> float:
        try:
            wait = float(await self.script(keys=[f"lorde:rl:{key}"],
                                           args=[per_minute, per_minute / 60.0, cost, time.time()]))
        except Exception:  # noqa: BLE001  (redis.RedisError, OSError, timeouts: fail open, locally limited)
            now = time.monotonic()
            if now - self._warned > 30:
                self._warned = now
                log.warning("Redis rate limiter unreachable; using in-process buckets", exc_info=True)
            return self.fallback.take(key, per_minute, cost)
        return float("inf") if wait < 0 else wait

    async def ping(self) -> bool:
        try:
            return bool(await self.r.ping())
        except Exception:  # noqa: BLE001
            return False


def make_buckets(redis_url: str | None):
    return RedisBuckets(redis_url) if redis_url else LocalBuckets()
