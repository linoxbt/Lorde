"""Lorde Job Worker: ``python -m worker.main``.

Claims jobs with SELECT ... FOR UPDATE SKIP LOCKED (``claim_job``), heartbeats every 15 s, runs each
job in a child process (so cancellation and limits can stop it), uploads outputs to the results bucket,
records judgments (scores only; judged text stays in the org's storage), meters them into usage_events
(source 'job'), and marks the job succeeded or failed. Every minute it re-queues jobs whose worker
vanished; every six hours it purges data past each org's retention.

Fairness (audit N1): claim_job takes jobs round-robin across orgs (0007_fairness.sql). Job judgments count
against the org's daily quota (LORDE_ORG_DAILY_JUDGMENTS, gateway/quota.py): a job claimed when the quota is
used up fails at once; otherwise its rows are capped to what the quota still allows (rows x enabled gates),
the rest are reported as not evaluated in counts (``not_evaluated``, ``notice``). Today's use is the larger of
the Redis counter and the day's usage_events, so a Redis outage cannot reset it. While the job runs it holds a
reservation of min(rows in the dataset, rows the quota allows) x gates (audit 2, B9), settled when it ends.

Partial work (audit 2, B3/S2): the child appends each judgment to judgments.jsonl the moment it is made; the
worker stores them every heartbeat. However the job ends (succeeded, failed, time limit, cancelled, lease lost,
an exception) the judgments made are kept, metered (one usage_events row) and charged; only a worker shutdown
re-queues the job uncharged, because its next attempt judges and charges every row again. What a job that
stops early exposes: status ``failed`` (time limit, child error) or ``cancelled``; ``error``; counts
``judgments`` (made and charged), ``rows_judged``/``evaluated_rows``; its judgments rows (scores, decisions,
verdicts) in the database; and ``partial.jsonl`` in its results (row_idx, gate, score, decision, verdict,
lang; GET /v1/jobs/{id}/results). passed.jsonl, rejected.jsonl and report.md exist only for a completed run.

Finishing (audit N5): the job is marked succeeded only if no cancel or data deletion was requested meanwhile.
A cancel ends it cancelled with its results and judgments kept (the work was done and is charged). A data
deletion (request_org_data_deletion sets delete_requested, 0008) ends it cancelled with its uploaded result
files expired (results_expire_at = now()) for the purge, and no further judgments are stored for it.

Isolation between tenants (audit S4/S10): one job may run for at most LORDE_JOB_MAX_SECONDS and judge
at most LORDE_JOB_MAX_ROWS rows; inputs over LORDE_JOB_MAX_INPUT_BYTES are refused; the child calls Serve
with LORDE_WORKER_JUDGE_CONCURRENCY (default 1: the worker shares Serve with the Gateway, which leaves that
many of LORDE_UPSTREAM_CONCURRENCY to it; see gateway/settings.py) and gets a minimal environment with no
database URL, pepper, service key or HF token. Every status write is guarded by locked_by, so a
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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gateway.quota import GLOBAL, make_quota, org_daily_limit, utc_day  # noqa: E402
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


@dataclass
class JobRun:
    """What one attempt at a job has judged so far (read from the child's judgments.jsonl)."""

    job_dir: Path
    gates: int
    rows: int
    day: str                  # UTC day of the quota reservation; settlement and metering go to this day
    started: datetime
    offset: int = 0           # bytes of judgments.jsonl already read
    counted: int = 0          # judgments made (lines read): what is metered and charged
    stored: int = 0
    pending: list = field(default_factory=list)
    rows_seen: set = field(default_factory=set)
    store_closed: bool = False  # lease lost or data deletion requested: store nothing more
    requeued: bool = False


def n_gates(job: dict) -> int:
    return max(1, sum(1 for v in (job["config"].get("gates") or {}).values() if v is not None))


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


def parsed(j: dict) -> dict:
    """judgments.parsed when the org enabled store_content: the scores plus the row's text, flat
    (instruction; input = source/context; output = response/answer), each clipped to 4,000 chars."""
    out = {k: j.get(k) for k in ("row_idx", "gate", "score", "decision", "verdict", "lang")}
    out.update({k: v for k, v in (j.get("text") or {}).items() if k in ("instruction", "input", "output")})
    return out


class Worker:
    def __init__(self, s: Settings | None = None, worker_id: str | None = None, work_dir: str | None = None,
                 storage=None, poll_s: float = 2.0, heartbeat_s: float = HEARTBEAT_S) -> None:
        self.s = s or Settings()
        if not self.s.database_url:
            raise RuntimeError("DATABASE_URL is required")
        self.id = worker_id or os.environ.get("WORKER_ID") or f"{socket.gethostname()}-{os.getpid()}"
        self.work = Path(work_dir or os.environ.get("LORDE_WORK_DIR", "/tmp/lorde-work"))
        self.storage: Any = storage or from_settings(self.s)
        if self.storage is None:
            raise RuntimeError("storage is required (SUPABASE_URL + key, or LORDE_STORAGE_DIR)")
        self.poll_s, self.heartbeat_s = poll_s, heartbeat_s
        self.quota = make_quota(self.s.redis_url)
        self.conn: Any = None  # psycopg.AsyncConnection with dict rows
        self._last_requeue = 0.0
        self._last_purge = 0.0
        self.stopping = False
        self._stop: asyncio.Event | None = None
        self._stop_loop: asyncio.AbstractEventLoop | None = None

    async def connect(self) -> None:
        if self.conn is not None and not self.conn.closed:
            with contextlib.suppress(Exception):
                await self.conn.close()
        assert self.s.database_url
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
            await self.purge_orphans()

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

    async def purge_orphans(self, age_s: float = 86400) -> int:
        """Delete dataset objects older than a day that no datasets row points at (audit 2, S3): left by the
        direct-upload policy 0008 dropped, or by a failed purge. Errors are logged, never raised."""
        try:
            from gateway.storage import LocalStorage

            if isinstance(self.storage, LocalStorage):
                candidates = await asyncio.to_thread(self.storage.older_than, "datasets", age_s)
            else:
                candidates = [r["path"] for r in await self.q("select path from orphan_dataset_objects()")]
            if not candidates:
                return 0
            known = {r["storage_path"] for r in await self.q(
                "select storage_path from datasets where storage_path = any(%s)", (candidates,))}
            orphans = [p for p in candidates if p not in known]
            for i in range(0, len(orphans), 100):
                await self.storage.delete("datasets", orphans[i:i + 100])
            if orphans:
                log.info("deleted %s orphan dataset objects", len(orphans))
            return len(orphans)
        except Exception:  # noqa: BLE001
            log.exception("orphan dataset purge failed; will retry with the next purge")
            return 0

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
        if len(parts) < 3:  # audit 2 (B10): the Gateway refuses these now; jobs queued before fail clearly
            raise InputRejected("a whole Hub dataset repo is not supported: name one file, "
                                "hf://datasets/<owner>/<name>/<path>.jsonl (or .csv, .parquet)")
        # Audit 2 (B17): the size comes from the Hub's file metadata before anything downloads, and the download is
        # pinned to the commit that metadata was read at, so a push in between cannot swap in a bigger file.
        revision, size = await asyncio.to_thread(hf_file_info, repo, parts[2])
        if size is None:
            raise InputRejected(f"{parts[2]} is not a file in the public Hub dataset {repo}")
        if size > cap:
            raise InputRejected(f"Hub dataset file {repo}/{parts[2]} is {size // (1024 * 1024)} MB; the limit is "
                                f"{cap // (1024 * 1024)} MB")
        from huggingface_hub import hf_hub_download

        # token=False: tenants only ever reach public datasets, never what the operator's token can see
        local = Path(await asyncio.to_thread(hf_hub_download, repo, parts[2], repo_type="dataset", revision=revision,
                                             local_dir=str(job_dir), token=False))
        if local.stat().st_size > cap:
            raise InputRejected(f"dataset file is larger than {cap // (1024 * 1024)} MB")
        fmt = {".csv": "csv", ".parquet": "parquet"}.get(local.suffix, "jsonl")
        return {"input_path": str(local), "format": fmt, "hf_revision": revision}

    async def run(self, job: dict) -> None:
        jid, org = str(job["id"]), str(job["org_id"])
        job_dir = self.work / jid
        shutil.rmtree(job_dir, ignore_errors=True)
        job_dir.mkdir(parents=True)
        try:
            await self._run(job, jid, org, job_dir)
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

    # ------------------------------------------------------------------ daily quota (audit N1b)
    async def used_today(self, org: str) -> int:
        """Judgments counted today for ``org`` (or, for quota.GLOBAL, for the whole service)."""
        if org == GLOBAL:
            r = await self.q("select coalesce(sum(judgments), 0)::bigint as n from usage_events where created_at >= "
                             "date_trunc('day', now() at time zone 'utc') at time zone 'utc'")
        else:
            r = await self.q("select coalesce(sum(judgments), 0)::bigint as n from usage_events where org_id = %s "
                             "and created_at >= date_trunc('day', now() at time zone 'utc') at time zone 'utc'",
                             (org,))
        db = int(r[0]["n"]) if r else 0
        return max(db, await self.quota.used(org)) if self.s.redis_url else db

    async def quota_rows(self, job: dict) -> int | None:
        """Rows this job may judge under the org's daily quota (lower for a new org) and the service's daily
        capacity (None: no cap below LORDE_JOB_MAX_ROWS). Raises InputRejected when not even one row fits."""
        org = str(job["org_id"])
        r = await self.q("select created_at from orgs where id = %s", (org,))
        limit = org_daily_limit(self.s, r[0]["created_at"] if r else None)
        gates = n_gates(job)
        rows = None
        if limit > 0:
            rows = max(0, limit - await self.used_today(org)) // gates
            if rows < 1:
                raise InputRejected(f"the organization's daily quota of {limit} judgments is used up; nothing was "
                                    "judged. Submit the job again after 00:00 UTC")
        if (cap := self.s.global_daily_cap) > 0:
            left = max(0, cap - await self.used_today(GLOBAL)) // gates
            if left < 1:
                raise InputRejected(f"the service's daily judgment capacity of {cap} is used up; nothing was "
                                    "judged. Submit the job again after 00:00 UTC")
            rows = left if rows is None else min(rows, left)
        return rows if rows is not None and rows < self.s.job_max_rows else None

    async def _run(self, job: dict, jid: str, org: str, job_dir: Path) -> None:
        """Fetch and size the input, reserve what the job can at most use against the shared daily quota (so API
        calls made while it runs cannot spend it twice), run it, then settle to the judgments actually made.

        Audit 2 (B3/S2/B9): the reservation is min(rows in the dataset, rows the quota allows) x gates, not the
        whole allowance; judgments are stored while the job runs (``flush``); and on every way out (success,
        failure, time limit, cancel, lost lease, an exception) the judgments made are metered (one usage_events
        row) and charged against the quota of the day the reservation was made. The one exception is a worker
        shutdown (deploy): the job is re-queued and its next attempt judges, meters and charges every row again,
        so the interrupted attempt is not charged (its stored judgments are kept; re-inserts are ignored).
        """
        quota_rows = await self.quota_rows(job)  # nothing reserved yet: a used-up quota fails here
        gates = n_gates(job)
        source = await self.fetch_input(job, job_dir)
        from worker.run_job import count_rows

        rows = await asyncio.to_thread(count_rows, Path(source["input_path"]), source["format"])
        if rows > self.s.job_max_rows:
            raise InputRejected(f"dataset has more than {self.s.job_max_rows} rows (LORDE_JOB_MAX_ROWS); "
                                "split it into smaller jobs")
        run = JobRun(job_dir=job_dir, gates=gates, rows=rows, day=utc_day(), started=datetime.now(UTC))
        reserved = 0
        counters = [org] + ([GLOBAL] if self.s.global_daily_cap > 0 else [])
        if self.s.redis_url:  # shared with the Gateway (without Redis each process counts on its own)
            reserved = (rows if quota_rows is None else min(rows, quota_rows)) * gates
            for c in counters if reserved else []:
                await self.quota.add(c, reserved, day=run.day)
        t0 = time.monotonic()
        try:
            await self._judge(job, jid, org, job_dir, quota_rows, source, run)
        finally:
            charge = 0 if run.requeued else run.counted
            try:
                await self.flush(job, run)  # whatever the child wrote after the last flush
                charge = 0 if run.requeued else run.counted
                if charge:
                    await self.meter(job, charge, int((time.monotonic() - t0) * 1000), run)
                await self.q("update jobs set counts = counts || %s where id = %s",
                             (Jsonb({"judgments": run.counted, "judgments_charged": charge,
                                     "rows_judged": len(run.rows_seen)}), jid))
            except Exception:  # noqa: BLE001  (the database is gone: the quota is still settled below)
                log.exception("job %s: could not store or meter its last judgments", jid)
            if reserved or charge:
                for c in counters:
                    await self.quota.add(c, charge - reserved, day=run.day)

    # ------------------------------------------------------------------ judgments as they happen (audit 2, B3)
    async def flush(self, job: dict, run: JobRun) -> None:
        """Read the judgments the child appended since the last call and store them. Every line read counts as
        a judgment made (charged even if it cannot be stored). Rows are inserted only while this worker holds
        the job and no data deletion was requested (checked under the job's row lock, so a deletion running at
        the same time either deletes them or is seen here)."""
        path = run.job_dir / "out" / "judgments.jsonl"
        if path.exists():
            with path.open("rb") as f:
                f.seek(run.offset)
                data = f.read()
            end = data.rfind(b"\n")
            if end >= 0:
                run.offset += end + 1
                for line in data[: end + 1].splitlines():
                    if line.strip():
                        j = json.loads(line)
                        run.pending.append(j)
                        run.counted += 1
                        run.rows_seen.add(j["row_idx"])
        if not run.pending or run.store_closed:
            run.pending.clear()
            return
        jid = str(job["id"])
        sql = ("insert into judgments (job_id, row_idx, gate, lang, score, decision, verdict, parsed) "
               "values (%s, %s, %s, %s, %s, %s, %s, %s) on conflict (job_id, row_idx, gate) do nothing")
        async with self.conn.transaction():
            r = await self.q("select o.store_content from jobs j join orgs o on o.id = j.org_id where j.id = %s "
                             "and j.locked_by = %s and not j.delete_requested for update of j", (jid, self.id))
            if r:
                store = r[0]["store_content"]
                batch = [(jid, j["row_idx"], j["gate"], j.get("lang"), j["score"], j["decision"], j.get("verdict"),
                          Jsonb(parsed(j)) if store else None) for j in run.pending]
                async with self.conn.cursor() as cur:
                    await cur.executemany(sql, batch)
                run.stored += len(batch)
            else:  # the lease is gone or the org asked for its data to be deleted: store nothing more
                run.store_closed = True
        run.pending.clear()

    async def partial_results(self, job: dict, run: JobRun) -> dict:
        """For a job that stops early (time limit, cancel, failure): upload partial.jsonl, the scores judged so far
        (row, gate, score, decision, verdict, lang; no row text), unless a data deletion was requested."""
        if not run.counted or run.store_closed:
            return {}
        r = await self.q("select delete_requested from jobs where id=%s", (job["id"],))
        if not r or r[0]["delete_requested"]:
            return {}
        src, dst = run.job_dir / "out" / "judgments.jsonl", run.job_dir / "partial.jsonl"
        with src.open(encoding="utf-8") as f, dst.open("w", encoding="utf-8") as out:
            for line in f:
                if line.endswith("\n") and line.strip():
                    j = json.loads(line)
                    out.write(json.dumps({k: j.get(k) for k in ("row_idx", "gate", "score", "decision", "verdict",
                                                                 "lang")}) + "\n")
        path = f"{job['org_id']}/{job['id']}/partial.jsonl"
        await self.storage.upload("results", path, dst, "application/json")
        return {"partial": path}

    async def _judge(self, job: dict, jid: str, org: str, job_dir: Path, quota_rows: int | None, source: dict,
                     run: JobRun) -> None:
        """Run the job in the child; judgments are stored every heartbeat and counted in ``run``."""
        spec = {"id": jid, "config": job["config"], "served_model": self.s.served_model,
                "vllm_base_url": self.s.vllm_base_url, "model_revision": job.get("model_revision") or self.s.model_label,
                "judge_concurrency": self.s.worker_judge_concurrency, "max_rows": self.s.job_max_rows,
                "quota_rows": quota_rows, **source}
        (job_dir / "job.json").write_text(json.dumps(spec, default=str))
        started = {"progress": "running", **({"hf_revision": source["hf_revision"]} if "hf_revision" in source else {})}
        if not await self.q("update jobs set counts = counts || %s where id=%s and locked_by=%s returning id",
                            (Jsonb(started), jid, self.id)):
            raise JobAbort("lost the job lease before starting")

        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "worker.run_job", str(job_dir), cwd=str(Path(__file__).resolve().parent.parent),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=child_env(job_dir))
        assert proc.stdout is not None
        t0 = time.monotonic()
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
                r = await self.q("update jobs set heartbeat_at = now(), counts = counts || %s where id=%s "
                                 "and locked_by=%s returning cancel_requested",
                                 (Jsonb({"judgments": run.counted, "rows_judged": len(run.rows_seen)}), jid, self.id))
                if not r:
                    outcome = "lost"
                    break
                if r[0]["cancel_requested"]:
                    outcome = "cancelled"
                    break
                await self.flush(job, run)
        finally:  # whatever happened (including a dropped database), never leave the child running
            stop.cancel()
            if proc.returncode is None:
                await stop_process(proc)
            exited.cancel()
            output = (await log_task).decode(errors="replace")

        await self.flush(job, run)  # store what the child judged before it stopped
        if outcome == "lost":
            raise JobAbort("lost the job lease (another worker or the requeue took it); stopped the child")
        if outcome == "shutdown":
            run.requeued = True
            await self.q("update jobs set status='queued', locked_by=null, heartbeat_at=null, "
                         "attempts=greatest(attempts - 1, 0), counts = counts || %s where id=%s and locked_by=%s",
                         (Jsonb({"progress": "requeued"}), jid, self.id))
            raise JobAbort("worker shutting down; job re-queued")
        if outcome is None:
            r = await self.q("select cancel_requested from jobs where id=%s", (jid,))
            if r and r[0]["cancel_requested"]:
                outcome = "cancelled"
        partial = {"progress": "stopped", "evaluated_rows": len(run.rows_seen), "judgments": run.counted}
        if outcome == "cancelled":
            await self.finish(job, "cancelled", counts={**partial, "progress": "cancelled"},
                              results=await self.partial_results(job, run))
            return
        if outcome == "timeout":
            await self.finish(job, "failed", error=f"job exceeded the {self.s.job_max_seconds} s time limit "
                                                   "(LORDE_JOB_MAX_SECONDS); the rows judged before it are kept "
                                                   "(partial.jsonl) and charged; split the dataset into smaller jobs",
                              counts={**partial, "progress": "failed"}, results=await self.partial_results(job, run))
            return
        if proc.returncode != 0:
            tail = "\n".join(output.strip().splitlines()[-15:])
            await self.finish(job, "failed", error=f"job process exited {proc.returncode}:\n{tail}"[:4000],
                              counts={**partial, "progress": "failed"}, results=await self.partial_results(job, run))
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
        await self.finish_succeeded(job, {**summary, "progress": "done"}, results)

    async def finish_succeeded(self, job: dict, counts: dict, results: dict) -> str:
        """Mark the job succeeded (its judgments were stored while it ran), unless a cancel or a data deletion was
        requested meanwhile. A cancel ends it cancelled with its results and judgments kept (the work was done
        and is charged); a data deletion ends it cancelled with its result files expired for the purge (and its
        judgments deleted by request_org_data_deletion). Returns 'succeeded', 'cancelled' or 'lost' (this worker
        no longer holds the job)."""
        jid, org = str(job["id"]), str(job["org_id"])
        async with self.conn.transaction():
            done = await self.q(
                "update jobs set status='succeeded', error=null, counts = counts || %s, results = %s, "
                "finished_at=now(), locked_by=null where id=%s and locked_by=%s and not cancel_requested returning id",
                (Jsonb(counts), Jsonb(results), jid, self.id))
            if done:
                status, detail = "succeeded", {}
            else:
                stopped = await self.q(
                    "update jobs set status='cancelled', counts = counts || %s, results = %s, "
                    "results_expire_at = case when delete_requested then now() else results_expire_at end, "
                    "finished_at=now(), locked_by=null "
                    "where id=%s and locked_by=%s and cancel_requested returning delete_requested",
                    (Jsonb({**counts, "progress": "cancelled"}), Jsonb(results), jid, self.id))
                if not stopped:
                    log.warning("job %s: not marked succeeded, this worker no longer holds it", jid)
                    return "lost"
                status = "cancelled"
                detail = ({"reason": "data deletion requested while finishing", "results": "expired for the purge"}
                          if stopped[0]["delete_requested"] else
                          {"reason": "cancel requested while finishing", "results": "kept"})
            await self.q("insert into audit_log (org_id, actor, action, target, detail) values (%s, %s, %s, %s, %s)",
                         (org, f"worker:{self.id}", f"job.{status}", jid, Jsonb(detail)))
        log.info("job %s %s", jid, status)
        return status

    async def meter(self, job: dict, n: int, latency_ms: int, run: JobRun | None = None) -> None:
        """One usage_events row per job attempt that judged anything (source 'job'), so job judgments count like
        API calls. A job that ran past 00:00 UTC is metered on the day its quota was reserved (B17)."""
        when = run.started if run is not None and utc_day() != run.day else datetime.now(UTC)
        await self.q(
            "insert into usage_events (org_id, api_key_id, key_prefix, route, judgments, latency_ms, status, "
            "model_revision, source, created_at) select %s, %s, (select prefix from api_keys where id = %s), "
            "'job:gating', %s, %s, 200, %s, 'job', %s",
            (job["org_id"], job.get("api_key_id"), job.get("api_key_id"), n, latency_ms,
             job.get("model_revision") or self.s.model_label, when))

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


def hf_file_info(repo: str, path: str) -> tuple[str, int | None]:
    """(commit sha, bytes) of one file in a public Hub dataset, from metadata only (nothing is downloaded).
    Bytes is None when ``path`` is not a file at that commit."""
    from huggingface_hub import HfApi

    api = HfApi(token=False)
    sha = api.dataset_info(repo).sha
    if not sha:
        raise InputRejected(f"could not resolve the current revision of the Hub dataset {repo}")
    infos = api.get_paths_info(repo, [path], repo_type="dataset", revision=sha)
    sizes = [getattr(i, "size", None) for i in infos if getattr(i, "path", None) == path]
    return sha, next((int(x) for x in sizes if x is not None), None)


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
