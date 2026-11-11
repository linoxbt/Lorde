"""Postgres (Supabase) backends: API keys, usage events and the jobs table.

The Gateway and Worker connect with the database's service credentials (DATABASE_URL, Supabase's
direct or pooled connection string). RLS protects user-facing access through Supabase; these
service paths are the only writers of jobs, judgments and usage_events (spec Section 13.2).
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
import uuid
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from .keys import ApiKey, KeyCache, KeyStore, hash_key, prefix_of


def dataset_path(org_id: str, dataset_id: str, name: str) -> str:
    """Storage path of an uploaded dataset; the worker refuses any other shape."""
    return f"{org_id}/{dataset_id}/{name}"


def make_pool(url: str, min_size: int = 1, max_size: int = 10) -> AsyncConnectionPool:
    return AsyncConnectionPool(url, min_size=min_size, max_size=max_size, open=False,
                               kwargs={"autocommit": True, "row_factory": dict_row})


class PgKeyStore(KeyStore):
    """api_keys table; same bounded cache and constant-time check as the file store."""

    def __init__(self, pool: AsyncConnectionPool, pepper: str, cache_size: int = 10_000) -> None:
        self.pool, self.pepper = pool, pepper
        self._cache = KeyCache(cache_size)
        self._touched: dict[str, float] = {}  # only valid keys land here, so it stays small

    @staticmethod
    def _rec(r: dict) -> ApiKey:
        return ApiKey(prefix=r["prefix"], key_hash=r["key_hash"], name=r["name"], org_id=str(r["org_id"]),
                      scopes=list(r["scopes"]), rpm=r["rpm"], created_at=str(r["created_at"]),
                      revoked_at=str(r["revoked_at"]) if r["revoked_at"] else None, id=str(r["id"]))

    async def alookup(self, key: str) -> ApiKey | None:
        prefix = prefix_of(key)  # malformed keys never reach the database
        if not prefix:
            return None
        found, rec = self._cache.get(prefix)
        if not found:
            async with self.pool.connection() as c:
                r = await (await c.execute("select * from api_keys where prefix = %s", (prefix,))).fetchone()
            rec = self._rec(r) if r else None
            self._cache.put(prefix, rec)
        if rec is None or not hmac.compare_digest(rec.key_hash, hash_key(key, self.pepper)):
            return None
        now = time.monotonic()
        if not rec.revoked_at and now - self._touched.get(prefix, 0) > 60:  # last_used_at, once a minute
            self._touched[prefix] = now
            async with self.pool.connection() as c:
                await c.execute("update api_keys set last_used_at = now() where prefix = %s", (prefix,))
        return rec

    async def averify(self, key: str) -> ApiKey | None:
        rec = await self.alookup(key)
        return None if rec is None or rec.revoked_at else rec

    async def acreate(self, name: str, org_id: str, scopes: list[str] | None = None, rpm: int = 600,
                      actor: str = "cli") -> tuple[str, ApiKey]:
        from .keys import KEY_PREFIX, _b62

        key = KEY_PREFIX + _b62()
        async with self.pool.connection() as c:
            await c.execute("select set_config('lorde.actor', %s, false)", (actor,))  # read by the audit trigger
            r = await (await c.execute(
                "insert into api_keys (org_id, name, prefix, key_hash, scopes, rpm) values (%s,%s,%s,%s,%s,%s) "
                "returning *", (org_id, name, prefix_of(key), hash_key(key, self.pepper), scopes or ["judge"], rpm),
            )).fetchone()
        return key, self._rec(r)

    async def alist(self, org_id: str | None = None) -> list[ApiKey]:
        async with self.pool.connection() as c:
            rows = await (await c.execute(
                "select * from api_keys where %s::uuid is null or org_id = %s::uuid order by created_at",
                (org_id, org_id))).fetchall()
        return [self._rec(r) for r in rows]

    async def arevoke(self, prefix: str, actor: str = "cli") -> bool:
        async with self.pool.connection() as c:
            await c.execute("select set_config('lorde.actor', %s, false)", (actor,))
            r = await (await c.execute(
                "update api_keys set revoked_at = now() where prefix = %s and revoked_at is null returning org_id",
                (prefix,))).fetchone()
        self._cache.pop(prefix, None)
        return bool(r)


class PgUsageSink:
    """Batched inserts into usage_events every second; no request content.

    A batch that fails to insert goes back to the queue and is retried next tick; at most
    ``max_queue`` events are held, the oldest dropped first (and counted in the log).
    """

    COLS = ("org_id", "api_key_id", "key_prefix", "route", "judgments", "prompt_tokens", "completion_tokens",
            "latency_ms", "status", "model_revision", "source")

    def __init__(self, pool: AsyncConnectionPool, max_queue: int = 10_000) -> None:
        self.pool = pool
        self.max_queue = max_queue
        self.queue: list[dict[str, Any]] = []
        self.dropped = 0
        self._task: asyncio.Task | None = None

    def record(self, **e: Any) -> None:
        e.setdefault("source", "mcp" if str(e.get("route", "")).startswith("mcp:") else "api")
        self.queue.append(e)
        self._trim()

    def _trim(self) -> None:
        over = len(self.queue) - self.max_queue
        if over > 0:
            del self.queue[:over]
            self.dropped += over

    async def aflush(self) -> None:
        if not self.queue:
            return
        batch, self.queue = self.queue, []
        sql = f"insert into usage_events ({', '.join(self.COLS)}) values ({', '.join(['%s'] * len(self.COLS))})"
        try:
            async with self.pool.connection() as c, c.cursor() as cur:
                await cur.executemany(sql, [tuple(e.get(k) for k in self.COLS) for e in batch])
        except Exception:
            self.queue = batch + self.queue  # keep order; retried next tick
            self._trim()
            raise

    def start(self) -> None:
        async def loop():
            while True:
                await asyncio.sleep(1.0)
                try:
                    await self.aflush()
                except Exception:  # noqa: BLE001  (never lose the loop; retry next tick)
                    logging.getLogger("lorde.usage").warning(
                        "usage flush failed; %s events queued, %s dropped so far", len(self.queue), self.dropped,
                        exc_info=True)

        self._task = asyncio.create_task(loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        try:
            await self.aflush()
        except Exception:  # noqa: BLE001
            logging.getLogger("lorde.usage").warning("final usage flush failed; %s events lost", len(self.queue))

    def flush(self) -> None:  # parity with UsageSink
        pass


class JobStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def create_dataset(self, org_id: str, name: str, fmt: str, size: int | None, retention_days: int | None,
                             actor: str) -> dict:
        async with self.pool.connection() as c:
            ret = retention_days or (await (await c.execute("select retention_days from orgs where id=%s",
                                                            (org_id,))).fetchone() or {"retention_days": 30})[
                "retention_days"]
            # The path shape is enforced by the datasets_storage_path_shape constraint (0005_hardening.sql).
            did = uuid.uuid4()
            r = await (await c.execute(
                "insert into datasets (id, org_id, name, storage_path, format, bytes, expires_at) "
                "values (%s, %s, %s, %s, %s, %s, now() + make_interval(days => %s)) returning *",
                (did, org_id, name, dataset_path(org_id, str(did), name), fmt, size, ret))).fetchone()
        return r

    async def get_dataset(self, org_id: str, dataset_id: str) -> dict | None:
        async with self.pool.connection() as c:
            return await (await c.execute("select * from datasets where id=%s and org_id=%s",
                                          (dataset_id, org_id))).fetchone()

    async def active_jobs(self, org_id: str) -> int:
        async with self.pool.connection() as c:
            r = await (await c.execute("select count(*) n from jobs where org_id=%s and status in ('queued','running')",
                                       (org_id,))).fetchone()
        return r["n"]

    async def job_by_idempotency_key(self, org_id: str, key: str) -> dict | None:
        async with self.pool.connection() as c:
            return await (await c.execute("select * from jobs where org_id=%s and idempotency_key=%s",
                                          (org_id, key))).fetchone()

    async def create_job(self, org_id: str, dataset_id: str | None, api_key_id: str | None, config: dict,
                         model_revision: str, actor: str, idempotency_key: str | None = None) -> dict:
        """Insert a job; with an idempotency key, a concurrent duplicate returns the existing job."""
        async with self.pool.connection() as c:
            r = await (await c.execute(
                "insert into jobs (org_id, dataset_id, api_key_id, config, model_revision, idempotency_key) "
                "values (%s,%s,%s,%s,%s,%s) on conflict (org_id, idempotency_key) where idempotency_key is not null "
                "do nothing returning *",
                (org_id, dataset_id, api_key_id, Jsonb(config), model_revision, idempotency_key))).fetchone()
            if r is None:
                return await (await c.execute("select * from jobs where org_id=%s and idempotency_key=%s",
                                              (org_id, idempotency_key))).fetchone()
            await c.execute("insert into audit_log (org_id, actor, action, target, detail) values "
                            "(%s, %s, 'job.create', %s, %s)", (org_id, actor, str(r["id"]), Jsonb(config)))
        return r

    async def get_job(self, org_id: str, job_id: str) -> dict | None:
        async with self.pool.connection() as c:
            return await (await c.execute("select * from jobs where id=%s and org_id=%s", (job_id, org_id))).fetchone()

    async def cancel_job(self, org_id: str, job_id: str, actor: str) -> dict | None:
        """Queued jobs are cancelled at once; running jobs get cancel_requested and the worker stops them."""
        async with self.pool.connection() as c:
            r = await (await c.execute(
                "update jobs set status = case when status='queued' then 'cancelled'::job_status else status end, "
                "cancel_requested = true, finished_at = case when status='queued' then now() else finished_at end "
                "where id=%s and org_id=%s and status in ('queued','running') returning *",
                (job_id, org_id))).fetchone()
            if r:
                await c.execute("insert into audit_log (org_id, actor, action, target) values (%s,%s,'job.cancel',%s)",
                                (org_id, actor, job_id))
        return r


def job_view(j: dict) -> dict[str, Any]:
    counts = j.get("counts") or {}
    return {"id": str(j["id"]), "status": j["status"], "dataset_id": str(j["dataset_id"]) if j["dataset_id"] else None,
            "config": j["config"], "model": j.get("model_revision"), "counts": counts,
            "progress": counts.get("progress"), "error": j.get("error"), "attempts": j.get("attempts"),
            "cancel_requested": j.get("cancel_requested"),
            "created_at": j["created_at"].isoformat() if j.get("created_at") else None,
            "started_at": j["started_at"].isoformat() if j.get("started_at") else None,
            "finished_at": j["finished_at"].isoformat() if j.get("finished_at") else None}


__all__ = ["JobStore", "PgKeyStore", "PgUsageSink", "job_view", "json", "make_pool"]
