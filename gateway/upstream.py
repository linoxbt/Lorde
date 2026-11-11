"""Client for Lorde Serve (vLLM or llama.cpp): timeout, one retry on connection errors, circuit breaker, and
admission (gateway/slots.py): per-org and global in-flight slots with a bounded, timed wait. Only real upstream
failures (connection errors, timeouts, 5xx) count towards the breaker; a request refused for want of a slot
(429 "model busy") never reached Serve and does not."""

from __future__ import annotations

import time
from typing import Any

import httpx

from .errors import ModelUnavailable
from .settings import Settings
from .slots import LocalSlots, UpstreamSlots


class CircuitBreaker:
    """Opens after N consecutive failures inside a window; stays open for one window."""

    def __init__(self, failures: int, window_s: float) -> None:
        self.failures, self.window_s = failures, window_s
        self._count, self._first, self._open_until = 0, 0.0, 0.0

    def check(self) -> None:
        now = time.monotonic()
        if now < self._open_until:
            raise ModelUnavailable("Lorde Serve is unavailable; retry shortly",
                                   headers={"Retry-After": str(int(self._open_until - now) + 1)})

    def ok(self) -> None:
        self._count = 0

    def fail(self) -> None:
        now = time.monotonic()
        if self._count == 0 or now - self._first > self.window_s:
            self._count, self._first = 0, now
        self._count += 1
        if self._count >= self.failures:
            self._open_until = now + self.window_s
            self._count = 0

    @property
    def is_open(self) -> bool:
        return time.monotonic() < self._open_until


class Upstream:
    def __init__(self, s: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.s = s
        self.client = httpx.AsyncClient(
            base_url=s.vllm_base_url.rstrip("/") + "/", timeout=s.upstream_timeout_s, transport=transport,
            headers={"Authorization": f"Bearer {s.vllm_api_key}"},
            limits=httpx.Limits(max_connections=s.api_upstream_slots + 2))
        self.breaker = CircuitBreaker(s.breaker_failures, s.breaker_window_s)
        self.slots: UpstreamSlots = LocalSlots(s.api_upstream_slots, s.org_upstream_concurrency,
                                               s.upstream_max_waiters, s.upstream_queue_timeout_s)

    async def chat(self, body: dict[str, Any], org_id: str | None = None) -> dict[str, Any]:
        """POST /chat/completions for ``org_id``; returns the JSON body. Raises ModelUnavailable on failure and
        429 rate_limited (slots.Busy) when no slot frees up in time."""
        self.breaker.check()
        async with self.slots.acquire(org_id):
            self.breaker.check()  # it may have opened while this call waited
            for attempt in range(2):
                try:
                    r = await self.client.post("chat/completions", json=body)
                except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError) as e:
                    if attempt == 0:
                        continue
                    self.breaker.fail()
                    raise ModelUnavailable(f"upstream connection failed: {type(e).__name__}",
                                           headers={"Retry-After": "5"}) from e
                except httpx.TimeoutException as e:
                    self.breaker.fail()
                    raise ModelUnavailable("upstream timeout", headers={"Retry-After": "5"}) from e
                if r.status_code >= 500:
                    self.breaker.fail()
                    raise ModelUnavailable(f"upstream error {r.status_code}", headers={"Retry-After": "5"})
                self.breaker.ok()
                if r.status_code >= 400:
                    from .errors import InvalidRequest

                    raise InvalidRequest(f"upstream rejected the request: {r.text[:300]}")
                return r.json()
        raise AssertionError("unreachable")

    async def ready(self) -> bool:
        try:
            r = await self.client.get("models", timeout=5)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        await self.client.aclose()
