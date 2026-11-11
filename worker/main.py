"""Lorde Job Worker: ``python -m worker.main``.

Claims jobs with SELECT ... FOR UPDATE SKIP LOCKED (``claim_job``), heartbeats every 15 s, runs each
job in a child process (so cancellation and limits can stop it), uploads outputs to the results bucket,
records judgments (scores only; judged text stays in the org's storage), meters them into usage_events
(source 'job'), and marks the job succeeded or failed. Every minute it re-queues jobs whose worker
vanished; every six hours it purges data past each org's retention.

Isolation between tenants (audit S4/S10): one job may run for at most LORDE_JOB_MAX_SECONDS and judge
at most LORDE_JOB_MAX_ROWS rows; inputs over LORDE_JOB_MAX_INPUT_BYTES are refused; the child calls Serve
with LORDE_WORKER_JUDGE_CONCURRENCY (default LORDE_UPSTREAM_CONCURRENCY) and gets a minimal environment
with no database URL, pepper, service key or HF token. Every status write is guarded by locked_by, so a
worker that lost its lease cannot overwrite the job. SIGTERM stops claiming, ends the running job's child
and puts the job back in the queue without counting the attempt.

Env: DATABASE_URL, VLLM_BASE_URL, VLLM_API_KEY, LORDE_SERVED_MODEL, storage (SUPABASE_URL +
SUPABASE_SERVICE_ROLE_KEY, or LORDE_STORAGE_DIR), LORDE_WORK_DIR, WORKER_ID, LORDE_MODEL_REVISION.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
import socket
import sys
import time
from pathlib import Path, PurePosixPath

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gateway.settings import Settings  # noqa: E402
from gateway.storage import from_settings  # noqa: E402

log = logging.getLogger("lorde.worker")
HEARTBEAT_S = 15
RESULT_FILES = ("passed.jsonl", "rejected.jsonl", "report.md", "manifest.json", "lexsi_provenance.json")
# The only variables the job child sees (S10): no DATABASE_URL, LORDE_KEY_PEPPER, SUPABASE_* or HF_TOKEN.
CHILD_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "PYTHONPATH", "PYTHONHASHSEED",
             "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE", "VIRTUAL_ENV", "VLLM_API_KEY", "LITELLM_LOG",
             "HF_HOME", "HF_HUB_CACHE", "HF_DATASETS_CACHE", "HF_HUB_OFFLINE", "HF_DATASETS_OFFLINE",
             "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
             "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "OMP_NUM_THREADS")


class JobAbort(Exception):
    """A job must stop for a reason that is not the job's own failure (lease lost, shutdown)."""


class InputRejected(Exception):
    """The job's input is unusable (wrong path, too large); the job fails with this message."""


def child_env(work_dir: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in CHILD_ENV}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env.setdefault("HOME", str(work_dir))
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    return env


def dataset_path_ok(path: str, org_id: str, dataset_id: str | None) -> bool:
    """Uploads live at {org_id}/{dataset_id}/{name} (gateway.db.dataset_path); nothing else is read."""
    if not dataset_id or not path.startswith(f"{org_id}/{dataset_id}/"):
        return False
    parts = PurePosixPath(path).parts
    return len(parts) == 3 and all(p not in ("", ".", "..") for p in parts) and "\\" not in path


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
        self.stopping = False
        self._stop: asyncio.Event | None = None
        self._stop_loop: asyncio.AbstractEventLoop | None = None

    async def connect(self) -> None:
        if self.conn is not None and not self.conn.closed:
            with contextlib.suppress(Exception):
                await self.conn.close()
        self.conn = await psycopg.AsyncConnection.connect(self.s.database_url, autocommit=True, row_factory=dict_row)

    async def q(self, sql: str, args=()) -> list[dict]:
        cur = await self.conn.execute(sql, args)
        return await cur.fetchall() if cur.description else []

    def stop(self) -> None:
        """Graceful shutdown (SIGTERM): claim nothing new; the running job is handed back to the queue."""
        if not self.stopping:
            log.warning("worker %s stopping: no new claims; the running job will be re-queued", self.id)
        self.stopping = True
        if self._stop is not None:
            self._stop.set()

    def _stop_event(self) -> asyncio.Event:
        loop = asyncio.get_running_loop()
        if self._stop is None or self._stop_loop is not loop:
            self._stop, self._stop_loop = asyncio.Event(), loop
            if self.stopping:
                self._stop.set()
        return self._stop

    async def _sleep(self, seconds: float) -> None:
        """Sleep, but wake at once when the worker is told to stop."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop_event().wait(), timeout=seconds)

    # ------------------------------------------------------------------ housekeeping
    async def housekeeping(self) -> None:
        now = time.monotonic()
        if now - self._last_requeue > 60:
            self._last_requeue = now
            n = (await self.q("select requeue_stale_jobs() as n"))[0]["n"]
            if n:
                log.warning("re-queued, cancelled or failed %s stale jobs", n)
        if now - self._last_purge > 6 * 3600:
            self._last_purge = now
            await self.purge()

    async def purge(self) -> None:
        """Delete data past retention. Each object is handled on its own: a bad row is logged and skipped,
        never allowed to stop the purge or the worker (audit S1)."""
        rows = await self.q("select kind, id, org_id, path from expired_objects()")
        done_ds: list = []
        failed_jobs: set = set()
        all_jobs: set = set()
        for r in rows:
            path, org, oid = r["path"] or "", str(r["org_id"]), str(r["id"])
            bucket = "datasets" if r["kind"] == "dataset" else "results"
            if r["kind"] == "result":
                all_jobs.add(r["id"])
            try:
                ok = (dataset_path_ok(path, org, oid) if bucket == "datasets"
                      else path.startswith(f"{org}/{oid}/") and ".." not in PurePosixPath(path).parts)
                if not ok:
                    log.error("retention: %s %s has a path outside its org/id prefix; row removed, object kept",
                              r["kind"], oid)
                elif path:
                    await self.storage.delete(bucket, [path])
                if bucket == "datasets":
                    done_ds.append(r["id"])
            except Exception:  # noqa: BLE001
                log.exception("retention: could not delete %s %s; will retry next purge", r["kind"], oid)
                if bucket == "results":
                    failed_jobs.add(r["id"])
        done_jobs = list(all_jobs - failed_jobs)
        if done_ds or done_jobs:
            await self.q("select finish_purge(%s, %s)", (done_ds, done_jobs))
            log.info("retention purge: %s datasets, %s job result sets", len(done_ds), len(done_jobs))

    # ------------------------------------------------------------------ one job
    async def process_one(self) -> bool:
        await self.housekeeping()
        if self.stopping:
            return False
        rows = await self.q("select * from claim_job(%s, %s)", (self.id, self.s.max_active_jobs))
        if not rows:
            return False
        job = rows[0]
        log.info("claimed job %s (attempt %s)", job["id"], job["attempts"])
        try:
            await self.run(job)
        except JobAbort as e:
            log.warning("job %s: %s", job["id"], e)
        except InputRejected as e:
            await self.finish(job, "failed", error=str(e)[:2000])
        except (psycopg.OperationalError, psycopg.InterfaceError):
            raise  # the connection is gone: run_forever reconnects; the stale job is re-queued by heartbeat
        except Exception as e:  # noqa: BLE001
            log.exception("job %s failed", job["id"])
            await self.finish(job, "failed", error=f"{type(e).__name__}: {e}"[:2000])
        return True

    async def fetch_input(self, job: dict, job_dir: Path) -> dict:
        org = str(job["org_id"])
        src = job["config"]["source"]
        cap = self.s.job_max_input_bytes
        if src.get("storage_path"):
            path = src["storage_path"]
            if not dataset_path_ok(path, org, str(job["dataset_id"]) if job.get("dataset_id") else None):
                raise InputRejected("the job's dataset path is not inside its own organization's upload")
            fmt = src.get("format") or "jsonl"
            if fmt not in ("jsonl", "csv", "parquet"):
                raise InputRejected(f"unsupported format {fmt!r}")
            local = job_dir / f"input.{fmt}"
            from gateway.storage import TooLarge

            try:
                await self.storage.download("datasets", path, local, max_bytes=cap)
            except TooLarge as e:
                raise InputRejected(f"dataset is larger than {cap // (1024 * 1024)} MB") from e
            return {"input_path": str(local), "format": fmt}
        parts = src["dataset_url"].removeprefix("hf://datasets/").split("/", 2)
        repo = f"{parts[0]}/{parts[1]}"
        size = await asyncio.to_thread(hf_size, repo, parts[2] if len(parts) == 3 else None)
        if size > cap:
            raise InputRejected(f"Hub dataset {repo} is {size // (1024 * 1024)} MB; the limit is "
                                f"{cap // (1024 * 1024)} MB")
        if len(parts) == 3:  # a file inside a Hub dataset repo
            from huggingface_hub import hf_hub_download

            # token=False: tenants only ever reach public datasets, never what the operator's token can see
            local = Path(await asyncio.to_thread(hf_hub_download, repo, parts[2], repo_type="dataset",
                                                 local_dir=str(job_dir), token=False))
            if local.stat().st_size > cap:
                raise InputRejected(f"dataset file is larger than {cap // (1024 * 1024)} MB")
            fmt = {".csv": "csv", ".parquet": "parquet"}.get(local.suffix, "jsonl")
            return {"input_path": str(local), "format": fmt}
        return {"hf_dataset": repo}

    async def run(self, job: dict) -> None:
        jid, org = str(job["id"]), str(job["org_id"])
        job_dir = self.work / jid
        shutil.rmtree(job_dir, ignore_errors=True)
        job_dir.mkdir(parents=True)
        try:
            await self._run(job, jid, org, job_dir)
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

    async def _run(self, job: dict, jid: str, org: str, job_dir: Path) -> None:
        spec = {"id": jid, "config": job["config"], "served_model": self.s.served_model,
                "vllm_base_url": self.s.vllm_base_url, "model_revision": job.get("model_revision") or self.s.model_label,
                "judge_concurrency": self.s.worker_judge_concurrency, "max_rows": self.s.job_max_rows}
        spec.update(await self.fetch_input(job, job_dir))
        (job_dir / "job.json").write_text(json.dumps(spec, default=str))
        if not await self.q("update jobs set counts = counts || %s where id=%s and locked_by=%s returning id",
                            (Jsonb({"progress": "running"}), jid, self.id)):
            raise JobAbort("lost the job lease before starting")

        t0 = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "worker.run_job", str(job_dir), cwd=str(Path(__file__).resolve().parent.parent),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=child_env(job_dir))
        log_task = asyncio.create_task(proc.stdout.read())
        exited = asyncio.create_task(proc.wait())
        stop = asyncio.create_task(self._stop_event().wait())
        outcome = None  # None (exited) | 'cancelled' | 'timeout' | 'shutdown' | 'lost'
        try:
            while True:
                await asyncio.wait({exited, stop}, timeout=self.heartbeat_s, return_when=asyncio.FIRST_COMPLETED)
                if exited.done():
                    break
                if self.stopping:
                    outcome = "shutdown"
                    break
                if time.monotonic() - t0 > self.s.job_max_seconds:
                    outcome = "timeout"
                    break
                r = await self.q("update jobs set heartbeat_at = now() where id=%s and locked_by=%s "
                                 "returning cancel_requested", (jid, self.id))
                if not r:
                    outcome = "lost"
                    break
                if r[0]["cancel_requested"]:
                    outcome = "cancelled"
                    break
        finally:  # whatever happened (including a dropped database), never leave the child running
            stop.cancel()
            if proc.returncode is None:
                await stop_process(proc)
            exited.cancel()
            output = (await log_task).decode(errors="replace")

        if outcome == "lost":
            raise JobAbort("lost the job lease (another worker or the requeue took it); stopped the child")
        if outcome == "shutdown":
            await self.q("update jobs set status='queued', locked_by=null, heartbeat_at=null, "
                         "attempts=greatest(attempts - 1, 0), counts = counts || %s where id=%s and locked_by=%s",
                         (Jsonb({"progress": "requeued"}), jid, self.id))
            raise JobAbort("worker shutting down; job re-queued")
        if outcome is None:
            r = await self.q("select cancel_requested from jobs where id=%s", (jid,))
            if r and r[0]["cancel_requested"]:
                outcome = "cancelled"
        if outcome == "cancelled":
            await self.finish(job, "cancelled")
            return
        if outcome == "timeout":
            await self.finish(job, "failed", error=f"job exceeded the {self.s.job_max_seconds} s time limit "
                                                   "(LORDE_JOB_MAX_SECONDS); split the dataset into smaller jobs")
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
        n_judg = 0
        batch: list[tuple] = []
        sql = ("insert into judgments (job_id, row_idx, gate, lang, score, decision, verdict, parsed) "
               "values (%s, %s, %s, %s, %s, %s, %s, %s) on conflict (job_id, row_idx, gate) do nothing")
        with (out / "judgments.jsonl").open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                j = json.loads(line)
                batch.append((jid, j["row_idx"], j["gate"], j.get("lang"), j["score"], j["decision"],
                              j.get("verdict"), Jsonb(j) if org_row["store_content"] else None))
                n_judg += 1
                if len(batch) >= 1000:
                    async with self.conn.cursor() as cur:
                        await cur.executemany(sql, batch)
                    batch = []
        if batch:
            async with self.conn.cursor() as cur:
                await cur.executemany(sql, batch)
        if await self.finish(job, "succeeded", counts={**summary, "progress": "done"}, results=results):
            await self.meter(job, n_judg, int((time.monotonic() - t0) * 1000))

    async def meter(self, job: dict, n: int, latency_ms: int) -> None:
        """One usage_events row per finished job (source 'job'), so job judgments count like API calls."""
        await self.q(
            "insert into usage_events (org_id, api_key_id, key_prefix, route, judgments, latency_ms, status, "
            "model_revision, source) select %s, %s, (select prefix from api_keys where id = %s), "
            "'job:gating', %s, %s, 200, %s, 'job'",
            (job["org_id"], job.get("api_key_id"), job.get("api_key_id"), n, latency_ms,
             job.get("model_revision") or self.s.model_label))

    async def finish(self, job: dict, status: str, error: str | None = None, counts: dict | None = None,
                     results: dict | None = None) -> bool:
        r = await self.q(
            "update jobs set status=%s, error=%s, counts = counts || %s, results = %s, finished_at=now(), "
            "locked_by=null where id=%s and locked_by=%s returning id",
            (status, error, Jsonb(counts or {"progress": status}), Jsonb(results or {}), job["id"], self.id))
        if not r:
            log.warning("job %s: not marked %s, this worker no longer holds it", job["id"], status)
            return False
        await self.q("insert into audit_log (org_id, actor, action, target, detail) values (%s, %s, %s, %s, %s)",
                     (job["org_id"], f"worker:{self.id}", f"job.{status}", str(job["id"]),
                      Jsonb({"error": error[:500]} if error else {})))
        log.info("job %s %s", job["id"], status)
        return True

    async def run_forever(self) -> None:
        """Poll until stopped. No single job, row or outage may end the loop: errors are logged and retried
        with backoff, and a broken database connection is re-opened."""
        backoff = 1.0
        while not self.stopping:
            try:
                if self.conn is None or self.conn.closed:
                    await self.connect()
                    log.info("worker %s polling", self.id)
                if not await self.process_one():
                    await self._sleep(self.poll_s)
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                db = isinstance(e, psycopg.OperationalError | psycopg.InterfaceError)
                log.exception("worker loop error (%s); retrying in %.0f s",
                              "database connection lost" if db else type(e).__name__, backoff)
                if db and self.conn is not None:
                    with contextlib.suppress(Exception):
                        await self.conn.close()
                await self._sleep(backoff)
                backoff = min(backoff * 2, 60.0)
        if self.conn is not None:
            with contextlib.suppress(Exception):
                await self.conn.close()


async def stop_process(proc: asyncio.subprocess.Process, grace_s: float = 5.0) -> None:
    with contextlib.suppress(ProcessLookupError):
        proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_s)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()


def hf_size(repo: str, path: str | None) -> int:
    """Bytes of a public Hub dataset file, or of every file in the repo (checked before anything downloads)."""
    from huggingface_hub import HfApi

    api = HfApi(token=False)
    if path:
        infos = api.get_paths_info(repo, [path], repo_type="dataset")
        return sum(getattr(i, "size", 0) or 0 for i in infos)
    info = api.dataset_info(repo, files_metadata=True)
    return sum((f.size or 0) for f in (info.siblings or []))


def main() -> None:
    from gateway.logs import setup

    setup()
    w = Worker()

    async def go():
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, w.stop)
        await w.run_forever()
        log.info("worker %s stopped", w.id)

    asyncio.run(go())


if __name__ == "__main__":
    main()
