"""Admission in front of Lorde Serve: a global slot pool plus per-organization slots (tenant fairness, audit N1).

A judgment holds two slots while it calls Serve: one of its org's ``LORDE_ORG_UPSTREAM_CONCURRENCY`` slots
(default 1), then one of the Gateway's global slots (``Settings.api_upstream_slots``). The org slot is taken
first, so an org can never have more than its share waiting for, or holding, a global slot: one tenant's flood
queues behind itself while another tenant's request reaches the model after at most the calls already in
flight plus one per other waiting org.

Waiting is bounded twice. The time spent waiting for both slots is at most ``LORDE_UPSTREAM_QUEUE_TIMEOUT_S``
(default 30 s), and at most ``LORDE_UPSTREAM_MAX_WAITERS`` (default 32) requests may wait for a global slot
(and as many for any one org's slot): past either bound the request is refused at once with 429
``rate_limited`` "model busy, retry later" and a Retry-After. A refused or timed-out wait never reached Serve,
so it is not an upstream failure and the circuit breaker does not count it.

``LocalSlots`` is in-process, which is exact for the single Gateway replica this deployment runs. Several
replicas would need a shared implementation with the same ``acquire(org_id)`` interface, e.g. Redis sorted
sets of leases (ZADD with an expiry score, ZREMRANGEBYSCORE to drop dead holders, ZCARD against the limit).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from collections.abc import AsyncIterator
from typing import Protocol

from .errors import LordeError


class Busy(LordeError):
    """429 rate_limited: the model is saturated (queue full or wait timed out). Not an upstream failure."""

    status = 429
    code = "rate_limited"


class _Slot:
    """A FIFO counting semaphore that is not bound to an event loop when created."""

    def __init__(self, n: int) -> None:
        self.n = max(1, n)
        self.used = 0
        self.waiters: deque[asyncio.Future[None]] = deque()

    @property
    def waiting(self) -> int:
        return sum(1 for f in self.waiters if not f.done())

    @property
    def free(self) -> bool:
        return self.used < self.n and not self.waiters

    @property
    def idle(self) -> bool:
        return self.used == 0 and not self.waiters

    async def acquire(self, timeout_s: float) -> None:
        """Take a slot within ``timeout_s`` or raise TimeoutError (the wait is then abandoned cleanly)."""
        if self.free:
            self.used += 1
            return
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.waiters.append(fut)
        try:
            async with asyncio.timeout(max(timeout_s, 0.0)):
                await fut
        except BaseException:
            if fut.done() and not fut.cancelled():
                self.release()  # the slot was handed over just as we gave up: pass it on
            else:
                fut.cancel()
                with contextlib.suppress(ValueError):
                    self.waiters.remove(fut)
            raise

    def release(self) -> None:
        while self.waiters:
            fut = self.waiters.popleft()
            if not fut.done():
                fut.set_result(None)  # hand the slot straight to the next waiter (used stays the same)
                return
        self.used -= 1


class UpstreamSlots(Protocol):
    def acquire(self, org_id: str | None) -> contextlib.AbstractAsyncContextManager[None]: ...


class LocalSlots:
    def __init__(self, global_slots: int, org_slots: int, max_waiters: int, timeout_s: float) -> None:
        self.global_slot = _Slot(global_slots)
        self.org_n, self.max_waiters, self.timeout_s = max(1, org_slots), max(0, max_waiters), timeout_s
        self.orgs: dict[str, _Slot] = {}
        self.retry_after = str(max(1, int(timeout_s // 2)))

    def busy(self, why: str) -> Busy:
        return Busy(f"model busy, retry later ({why})", headers={"Retry-After": self.retry_after})

    def in_flight(self, org_id: str) -> int:
        slot = self.orgs.get(org_id)
        return slot.used if slot else 0

    @contextlib.asynccontextmanager
    async def acquire(self, org_id: str | None) -> AsyncIterator[None]:
        deadline = time.monotonic() + self.timeout_s
        org = None
        if org_id is not None:
            org = self.orgs.get(org_id)
            if org is None:
                org = self.orgs[org_id] = _Slot(self.org_n)
            if not org.free and org.waiting >= self.max_waiters:
                raise self.busy("too many requests from this organization are waiting")
            try:
                await org.acquire(deadline - time.monotonic())
            except TimeoutError:
                raise self.busy("timed out waiting for this organization's turn") from None
            finally:
                if org.idle:
                    self.orgs.pop(org_id, None)
        try:
            if not self.global_slot.free and self.global_slot.waiting >= self.max_waiters:
                raise self.busy("the queue is full")
            try:
                await self.global_slot.acquire(deadline - time.monotonic())
            except TimeoutError:
                raise self.busy("timed out in the queue") from None
            try:
                yield
            finally:
                self.global_slot.release()
        finally:
            if org is not None:
                org.release()
                if org.idle and self.orgs.get(org_id or "") is org:
                    self.orgs.pop(org_id or "", None)
