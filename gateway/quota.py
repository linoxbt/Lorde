"""Per-organization daily judgment quota (audit N1b): ``LORDE_ORG_DAILY_JUDGMENTS`` per UTC day, 0 = unlimited.

One counter per org per UTC day covers every judgment the org causes: REST and MCP judge calls, the chat
proxy, and gating jobs (the worker adds each finished job's judgments, and caps a job's rows to what is left
when it claims it). With REDIS_URL the counter is shared (``lorde:quota:<org>:<YYYYMMDD>``, expiring after two
days) by the Gateway and the worker; without Redis, or while Redis is unreachable, each process falls back to
an in-process counter (fail open, like the rate limiter) and logs a warning at most every 30 s. The worker also
reads the day's metered usage from the database, so a Redis outage cannot hand a job a fresh quota.

A refused request is 429 ``quota_exceeded`` with Retry-After set to the seconds left until 00:00 UTC.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime, timedelta

log = logging.getLogger("lorde.quota")


def utc_day(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y%m%d")


def seconds_to_midnight(now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((midnight - now).total_seconds()) + 1)


class LocalQuota:
    """In-process counters (one replica, or the fallback while Redis is down)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._used: dict[tuple[str, str], int] = {}

    def _prune(self, day: str) -> None:
        for k in [k for k in self._used if k[1] != day]:
            del self._used[k]

    def take_sync(self, org: str, n: int, limit: int) -> tuple[bool, int]:
        day = utc_day()
        with self._lock:
            if len(self._used) > 10_000:
                self._prune(day)
            used = self._used.get((org, day), 0)
            if limit > 0 and used + n > limit:
                return False, used
            self._used[(org, day)] = used + n
            return True, used + n

    def add_sync(self, org: str, n: int, day: str | None = None) -> int:
        day = day or utc_day()
        with self._lock:
            v = max(0, self._used.get((org, day), 0) + n)
            self._used[(org, day)] = v
            return v

    def used_sync(self, org: str) -> int:
        with self._lock:
            return self._used.get((org, utc_day()), 0)

    def raise_to_sync(self, org: str, floor: int) -> int:
        day = utc_day()
        with self._lock:
            v = max(self._used.get((org, day), 0), floor)
            self._used[(org, day)] = v
            return v

    async def raise_to(self, org: str, floor: int) -> int:
        """Atomically raise today's counter to at least ``floor`` (never lowers it)."""
        return self.raise_to_sync(org, floor)

    async def take(self, org: str, n: int, limit: int) -> tuple[bool, int]:
        return self.take_sync(org, n, limit)

    async def add(self, org: str, n: int, day: str | None = None) -> int:
        """Add ``n`` (negative: refund) to the counter of ``day`` (default today, UTC). A settlement or refund
        passes the day of its reservation, so one that lands after midnight corrects the right day."""
        return self.add_sync(org, n, day)

    async def used(self, org: str) -> int:
        return self.used_sync(org)

    async def close(self) -> None:
        pass


# Check and spend atomically: refused requests spend nothing.
_TAKE = """
local used = tonumber(redis.call('GET', KEYS[1]) or '0')
local n = tonumber(ARGV[1]) local limit = tonumber(ARGV[2])
if limit > 0 and used + n > limit then return {0, used} end
used = redis.call('INCRBY', KEYS[1], n)
redis.call('EXPIRE', KEYS[1], 172800)
return {1, used}
"""
_RAISE = """
local v = tonumber(redis.call('GET', KEYS[1]) or '0') local floor = tonumber(ARGV[1])
if floor > v then redis.call('SET', KEYS[1], floor) v = floor end
redis.call('EXPIRE', KEYS[1], 172800)
return v
"""
_ADD = """
local v = redis.call('INCRBY', KEYS[1], ARGV[1])
if v < 0 then redis.call('SET', KEYS[1], 0) v = 0 end
redis.call('EXPIRE', KEYS[1], 172800)
return v
"""


class RedisQuota:
    def __init__(self, url: str, timeout_s: float = 1.0) -> None:
        import redis.asyncio as redis

        self.r = redis.from_url(url, socket_timeout=timeout_s, socket_connect_timeout=timeout_s)
        self._take = self.r.register_script(_TAKE)
        self._add = self.r.register_script(_ADD)
        self._raise = self.r.register_script(_RAISE)
        self.fallback = LocalQuota()
        self._warned = 0.0
        self.degraded = False  # the last call fell back to the in-process counter

    @staticmethod
    def key(org: str, day: str | None = None) -> str:
        return f"lorde:quota:{org}:{day or utc_day()}"

    def _fail(self) -> None:
        self.degraded = True
        now = time.monotonic()
        if now - self._warned > 30:
            self._warned = now
            log.warning("Redis quota counter unreachable; using an in-process counter", exc_info=True)

    async def take(self, org: str, n: int, limit: int) -> tuple[bool, int]:
        try:
            ok, used = await self._take(keys=[self.key(org)], args=[n, limit])
        except Exception:  # noqa: BLE001  (fail open, counted locally)
            self._fail()
            return self.fallback.take_sync(org, n, limit)
        self.degraded = False
        return bool(int(ok)), int(used)

    async def add(self, org: str, n: int, day: str | None = None) -> int:
        try:
            v = int(await self._add(keys=[self.key(org, day)], args=[n]))
        except Exception:  # noqa: BLE001
            self._fail()
            return self.fallback.add_sync(org, n, day)
        self.degraded = False
        return v

    async def raise_to(self, org: str, floor: int) -> int:
        """Atomically raise today's counter to at least ``floor`` (never lowers it): safe under concurrency."""
        try:
            v = int(await self._raise(keys=[self.key(org)], args=[floor]))
        except Exception:  # noqa: BLE001
            self._fail()
            return self.fallback.raise_to_sync(org, floor)
        self.degraded = False
        return v

    async def used(self, org: str) -> int:
        try:
            v = await self.r.get(self.key(org))
        except Exception:  # noqa: BLE001
            self._fail()
            return self.fallback.used_sync(org)
        self.degraded = False
        return int(v or 0)

    async def close(self) -> None:
        try:
            await self.r.aclose()
        except Exception:  # noqa: BLE001
            pass


def make_quota(redis_url: str | None) -> LocalQuota | RedisQuota:
    return RedisQuota(redis_url) if redis_url else LocalQuota()
