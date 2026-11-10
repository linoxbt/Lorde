"""Postgres (Supabase) backends: API keys, usage events and the jobs table.

The Gateway and Worker connect with the database's service credentials (DATABASE_URL, Supabase's
direct or pooled connection string). RLS protects user-facing access through Supabase; these
service paths are the only writers of jobs, judgments and usage_events (spec Section 13.2).
"""

from __future__ import annotations

import json
import time
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from .keys import CACHE_TTL_S, ApiKey, KeyStore, hash_key, prefix_of


def make_pool(url: str, min_size: int = 1, max_size: int = 10) -> AsyncConnectionPool:
    return AsyncConnectionPool(url, min_size=min_size, max_size=max_size, open=False,
                               kwargs={"autocommit": True, "row_factory": dict_row})


class PgKeyStore(KeyStore):
    """api_keys table; same cache and constant-time check as the file store."""

    def __init__(self, pool: AsyncConnectionPool, pepper: str) -> None:
        self.pool, self.pepper = pool, pepper
        self._cache: dict[str, tuple[float, ApiKey | None]] = {}
        self._touched: dict[str, float] = {}

    @staticmethod
    def _rec(r: dict) -> ApiKey:
        return ApiKey(prefix=r["prefix"], key_hash=r["key_hash"], name=r["name"], org_id=str(r["org_id"]),
                      scopes=list(r["scopes"]), rpm=r["rpm"], created_at=str(r["created_at"]),
                      revoked_at=str(r["revoked_at"]) if r["revoked_at"] else None, id=str(r["id"]))

    async def averify(self, key: str) -> ApiKey | None:
        import hmac

        prefix = prefix_of(key)
        if not prefix:
            return None
        now = time.monotonic()
        hit = self._cache.get(prefix)
        if hit and now - hit[0] < CACHE_TTL_S:
            rec = hit[1]
        else:
            async with self.pool.connection() as c:
                r = await (await c.execute("select * from api_keys where prefix = %s", (prefix,))).fetchone()
            rec = self._rec(r) if r else None
            self._cache[prefix] = (now, rec)
        if rec is None or rec.revoked_at or not hmac.compare_digest(rec.key_hash, hash_key(key, self.pepper)):
            return None
        if now - self._touched.get(prefix, 0) > 60:  # last_used_at, at most once a minute per key
            self._touched[prefix] = now
            async with self.pool.connection() as c:
                await c.execute("update api_keys set last_used_at = now() where prefix = %s", (prefix,))
        return rec

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

    async def arevoke(self, prefix: str, actor: str = "cli") -> bool:
        async with self.pool.connection() as c:
            await c.execute("select set_config('lorde.actor', %s, false)", (actor,))
            r = await (await c.execute(
                "update api_keys set revoked_at = now() where prefix = %s and revoked_at is null returning org_id",
                (prefix,))).fetchone()
        self._cache.pop(prefix, None)
        return bool(r)


class PgUsageSink:
    """Batched inserts into usage_events every second; no request content."""

    COLS = ("org_id", "api_key_id", "key_prefix", "route", "judgments", "prompt_tokens", "completion_tokens",
            "latency_ms", "status", "model_revision")

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool
        self.queue: list[dict[str, Any]] = []
        self._task = None

    def record(self, **e: Any) -> None:
        self.queue.append(e)

    async def aflush(self) -> None:
        if not self.queue:
            return
        batch, self.queue = self.queue, []
        sql = f"insert into usage_events ({', '.join(self.COLS)}) values ({', '.join(['%s'] * len(self.COLS))})"
        async with self.pool.connection() as c, c.cursor() as cur:
            await cur.executemany(sql, [tuple(e.get(k) for k in self.COLS) for e in batch])

    def start(self) -> None:
        import asyncio

        async def loop():
            while True:
                await asyncio.sleep(1.0)
                try:
                    await self.aflush()
                except Exception:  # noqa: BLE001  (never lose the loop; retry next tick)
                    import logging

                    logging.getLogger("lorde.usage").exception("usage flush failed")

        self._task = asyncio.create_task(loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self.aflush()

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
            r = await (await c.execute(
                "insert into datasets (org_id, name, storage_path, format, bytes, expires_at) "
                "values (%s, %s, '', %s, %s, now() + make_interval(days => %s)) returning *",
                (org_id, name, fmt, size, ret))).fetchone()
            path = f"{org_id}/{r['id']}/{name}"
            await c.execute("update datasets set storage_path=%s where id=%s", (path, r["id"]))
            r["storage_path"] = path
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

    async def create_job(self, org_id: str, dataset_id: str | None, api_key_id: str | None, config: dict,
                         model_revision: str, actor: str) -> dict:
        async with self.pool.connection() as c:
            r = await (await c.execute(
                "insert into jobs (org_id, dataset_id, api_key_id, config, model_revision) values (%s,%s,%s,%s,%s) "
                "returning *", (org_id, dataset_id, api_key_id, Jsonb(config), model_revision))).fetchone()
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
