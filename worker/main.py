"""Lorde Job Worker: ``python -m worker.main``.

Claims jobs with SELECT ... FOR UPDATE SKIP LOCKED (``claim_job``), heartbeats every 15 s, runs each
job in a child process (so cancellation can stop it), uploads outputs to the results bucket, records
judgments (scores only; judged text stays in the org's storage), and marks the job succeeded or
failed. Every minute it re-queues jobs whose worker vanished; every six hours it purges data past
each org's retention.

Env: DATABASE_URL, VLLM_BASE_URL, VLLM_API_KEY, LORDE_SERVED_MODEL, storage (SUPABASE_URL +
SUPABASE_SERVICE_ROLE_KEY, or LORDE_STORAGE_DIR), LORDE_WORK_DIR, WORKER_ID, LORDE_MODEL_REVISION.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import socket
import sys
import time
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gateway.settings import Settings  # noqa: E402
from gateway.storage import from_settings  # noqa: E402

log = logging.getLogger("lorde.worker")
HEARTBEAT_S = 15
RESULT_FILES = ("passed.jsonl", "rejected.jsonl", "report.md", "manifest.json", "lexsi_provenance.json")


class Worker:
    def __init__(self, s: Settings | None = None, worker_id: str | None = None, work_dir: str | None = None,
                 storage=None, poll_s: float = 2.0, heartbeat_s: float = HEARTBEAT_S) -> None:
        self.s = s or Settings()
        if not self.s.database_url:
            raise RuntimeError("DATABASE_URL is required")
        self.id = worker_id or os.environ.get("WORKER_ID") or f"{socket.gethostname()}-{os.getpid()}"
        self.work = Path(work_dir or os.environ.get("LORDE_WORK_DIR", "/tmp/lorde-work"))
        self.storage = storage or from_settings(self.s)
        if self.storage is None:
            raise RuntimeError("storage is required (SUPABASE_URL + key, or LORDE_STORAGE_DIR)")
        self.poll_s, self.heartbeat_s = poll_s, heartbeat_s
        self.conn: psycopg.AsyncConnection | None = None
        self._last_requeue = 0.0
        self._last_purge = 0.0

    async def connect(self) -> None:
        self.conn = await psycopg.AsyncConnection.connect(self.s.database_url, autocommit=True, row_factory=dict_row)

    async def q(self, sql: str, args=()) -> list[dict]:
        cur = await self.conn.execute(sql, args)
        return await cur.fetchall() if cur.description else []

    # ------------------------------------------------------------------ housekeeping
    async def housekeeping(self) -> None:
        now = time.monotonic()
        if now - self._last_requeue > 60:
            self._last_requeue = now
            n = (await self.q("select requeue_stale_jobs() as n"))[0]["n"]
            if n:
                log.warning("re-queued or failed %s stale jobs", n)
        if now - self._last_purge > 6 * 3600:
            self._last_purge = now
            await self.purge()

    async def purge(self) -> None:
        rows = await self.q("select kind, id, path from expired_objects()")
        ds = [r for r in rows if r["kind"] == "dataset"]
        rs = [r for r in rows if r["kind"] == "result"]
        if ds:
            await self.storage.delete("datasets", [r["path"] for r in ds if r["path"]])
        if rs:
            await self.storage.delete("results", [r["path"] for r in rs])
        if rows:
            await self.q("select finish_purge(%s, %s)", ([r["id"] for r in ds], list({r["id"] for r in rs})))
            log.info("retention purge: %s datasets, %s job result sets", len(ds), len({r["id"] for r in rs}))

    # ------------------------------------------------------------------ one job
    async def process_one(self) -> bool:
        await self.housekeeping()
        rows = await self.q("select * from claim_job(%s, %s)", (self.id, self.s.max_active_jobs))
        if not rows:
            return False
        job = rows[0]
        log.info("claimed job %s (attempt %s)", job["id"], job["attempts"])
        try:
            await self.run(job)
        except Exception as e:  # noqa: BLE001
            log.exception("job %s failed", job["id"])
            await self.finish(job, "failed", error=f"{type(e).__name__}: {e}"[:2000])
        return True

    async def run(self, job: dict) -> None:
        jid, org = str(job["id"]), str(job["org_id"])
        job_dir = self.work / jid
        shutil.rmtree(job_dir, ignore_errors=True)
        job_dir.mkdir(parents=True)
        src = job["config"]["source"]
        spec = {"id": jid, "config": job["config"], "served_model": self.s.served_model,
                "vllm_base_url": self.s.vllm_base_url, "model_revision": job.get("model_revision") or self.s.model_label}
        if src.get("storage_path"):
            fmt = src.get("format") or "jsonl"
            local = job_dir / f"input.{fmt}"
            await self.storage.download("datasets", src["storage_path"], local)
            spec.update({"input_path": str(local), "format": fmt})
        else:
            parts = src["dataset_url"].removeprefix("hf://datasets/").split("/", 2)
            if len(parts) == 3:  # a file inside a Hub dataset repo
                from huggingface_hub import hf_hub_download

                local = Path(await asyncio.to_thread(hf_hub_download, f"{parts[0]}/{parts[1]}", parts[2],
                                                     repo_type="dataset", local_dir=str(job_dir)))
                fmt = {".csv": "csv", ".parquet": "parquet"}.get(local.suffix, "jsonl")
                spec.update({"input_path": str(local), "format": fmt})
            else:
                spec["hf_dataset"] = f"{parts[0]}/{parts[1]}"
        (job_dir / "job.json").write_text(json.dumps(spec, default=str))
        await self.q("update jobs set counts = counts || %s where id=%s", (Jsonb({"progress": "running"}), jid))

        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "worker.run_job", str(job_dir), cwd=str(Path(__file__).resolve().parent.parent),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env={**os.environ})
        log_task = asyncio.create_task(proc.stdout.read())
        cancelled = False
        while True:
            try:
                await asyncio.wait_for(proc.wait(), timeout=self.heartbeat_s)
                break
            except TimeoutError:
                r = await self.q("update jobs set heartbeat_at = now() where id=%s returning cancel_requested", (jid,))
                if r and r[0]["cancel_requested"]:
                    cancelled = True
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=10)
                    except TimeoutError:
                        proc.kill()
                        await proc.wait()
                    break
        output = (await log_task).decode(errors="replace")
        if not cancelled:
            r = await self.q("select cancel_requested from jobs where id=%s", (jid,))
            cancelled = bool(r and r[0]["cancel_requested"])
        if cancelled:
            await self.finish(job, "cancelled")
            shutil.rmtree(job_dir, ignore_errors=True)
            return
        if proc.returncode != 0:
            tail = "\n".join(output.strip().splitlines()[-15:])
            await self.finish(job, "failed", error=f"job process exited {proc.returncode}:\n{tail}"[:4000])
            return

        out = job_dir / "out"
        summary = json.loads((out / "summary.json").read_text())
        results = {}
        for name in RESULT_FILES:
            if (out / name).exists():
                path = f"{org}/{jid}/{name}"
                ctype = "text/markdown" if name.endswith(".md") else "application/json"
                await self.storage.upload("results", path, out / name, ctype)
                results[name.split(".")[0]] = path
        org_row = (await self.q("select store_content from orgs where id=%s", (org,)))[0]
        judg = [json.loads(x) for x in (out / "judgments.jsonl").read_text().splitlines() if x.strip()]
        if judg:
            async with self.conn.cursor() as cur:
                await cur.executemany(
                    "insert into judgments (job_id, row_idx, gate, score, decision, verdict, parsed) "
                    "values (%s, %s, %s, %s, %s, %s, %s)",
                    [(jid, j["row_idx"], j["gate"], j["score"], j["decision"], j.get("verdict"),
                      Jsonb(j) if org_row["store_content"] else None) for j in judg])
        await self.finish(job, "succeeded", counts={**summary, "progress": "done"}, results=results)
        shutil.rmtree(job_dir, ignore_errors=True)

    async def finish(self, job: dict, status: str, error: str | None = None, counts: dict | None = None,
                     results: dict | None = None) -> None:
        await self.q(
            "update jobs set status=%s, error=%s, counts = counts || %s, results = %s, finished_at=now(), "
            "locked_by=null where id=%s",
            (status, error, Jsonb(counts or {"progress": status}), Jsonb(results or {}), job["id"]))
        await self.q("insert into audit_log (org_id, actor, action, target, detail) values (%s, %s, %s, %s, %s)",
                     (job["org_id"], f"worker:{self.id}", f"job.{status}", str(job["id"]),
                      Jsonb({"error": error[:500]} if error else {})))
        log.info("job %s %s", job["id"], status)

    async def run_forever(self) -> None:
        await self.connect()
        log.info("worker %s polling", self.id)
        while True:
            try:
                if not await self.process_one():
                    await asyncio.sleep(self.poll_s)
            except (psycopg.OperationalError, psycopg.InterfaceError):
                log.exception("database connection lost; reconnecting")
                await asyncio.sleep(5)
                await self.connect()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='{"ts":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s"}')
    asyncio.run(Worker().run_forever())


if __name__ == "__main__":
    main()
