# ruff: noqa: F811  (the module-scoped `env` fixture is imported from test_jobs and requested by name)
"""Regressions for the second audit's backend, database and security findings (12 Nov 2026).

The Postgres tests need LORDE_TEST_PG_URL (see test_supabase_sql.py).
"""

import asyncio
import json
import os
import threading
import time
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")
from fastapi.testclient import TestClient  # noqa: E402
from test_jobs import ROWS, clear_jobs, env, run_one, upload  # noqa: E402,F401

from gateway.app import create_app  # noqa: E402
from gateway.settings import Settings  # noqa: E402
from worker.main import Worker  # noqa: E402

URL = os.environ.get("LORDE_TEST_PG_URL")
REDIS = os.environ.get("LORDE_TEST_REDIS_URL")
pg = pytest.mark.skipif(not URL, reason="set LORDE_TEST_PG_URL to run Postgres tests")


def db(env):
    return psycopg.connect(env["db"], autocommit=True)


# ------------------------------------------------------------------ B4, B17: job creation races

@pg
def test_concurrent_job_posts_respect_the_org_cap_and_idempotency(env):
    """B4: 20 concurrent POST /v1/jobs create exactly max_active_jobs; B17: 10 concurrent requests with one
    Idempotency-Key create one job, and only one answer is 201 (the rest 200 with the same job)."""
    clear_jobs(env)
    h = env["keys"]["all"]
    with TestClient(create_app(env["s"])) as c:
        up = upload(c, h, ROWS[:1])
        codes: list = []
        lock = threading.Lock()

        def burst(headers, n):
            barrier = threading.Barrier(n)
            codes.clear()

            def post():
                barrier.wait()
                r = c.post("/v1/jobs", headers=headers, json={"upload_id": up})
                with lock:
                    codes.append((r.status_code, r.json()))

            ts = [threading.Thread(target=post) for _ in range(n)]
            [t.start() for t in ts]
            [t.join(60) for t in ts]

        burst(h, 20)
        created = [b for code, b in codes if code == 201]
        assert len(created) == env["s"].max_active_jobs, sorted(x[0] for x in codes)
        assert all(code == 429 and b["error"]["code"] == "rate_limited" for code, b in codes if code != 201)
        with db(env) as d:
            assert d.execute("select count(*) from jobs where org_id=%s and status in ('queued','running')",
                             (env["org"],)).fetchone()[0] == env["s"].max_active_jobs

        clear_jobs(env)
        burst({**h, "Idempotency-Key": "race-" + uuid.uuid4().hex}, 10)
        assert sorted(code for code, _ in codes) == [200] * 9 + [201]
        assert len({b["id"] for _, b in codes}) == 1
    clear_jobs(env)


# ------------------------------------------------------------------ B3/S2/B9: partial work is kept and charged

def submit(env, rows, s=None):
    with TestClient(create_app(s or env["s"])) as c:
        up = upload(c, env["keys"]["all"], rows)
        r = c.post("/v1/jobs", headers=env["keys"]["all"], json={"upload_id": up})
        assert r.status_code == 201, r.text
        return r.json()["id"]


def quota_used(org) -> int:
    from gateway.quota import RedisQuota

    async def go():
        q = RedisQuota(REDIS)
        try:
            return await q.used(str(org))
        finally:
            await q.close()
    return asyncio.run(go())


def job_state(env, jid):
    with db(env) as c:
        status, error, counts, results = c.execute("select status, error, counts, results from jobs where id=%s",
                                                   (jid,)).fetchone()
        n = c.execute("select count(*) from judgments where job_id=%s", (jid,)).fetchone()[0]
        metered = c.execute("select coalesce(sum(judgments), 0) from usage_events where source='job' and "
                            "created_at > now() - interval '5 minutes' and org_id=%s", (env["org"],)).fetchone()[0]
    return status, error, counts, results, n, metered


def slow_settings(env, **kw):
    return Settings(**{**env["s"].__dict__, "vllm_base_url": env["slow_url"], "redis_url": REDIS,
                       "org_daily_judgments": 100_000, **kw})


def run_in_thread(w):
    t = threading.Thread(target=run_one, args=(w,))
    t.start()
    return t


def wait_for(cond, timeout=30.0):
    t0 = time.monotonic()
    while not cond():
        assert time.monotonic() - t0 < timeout, "condition not reached"
        time.sleep(0.05)


@pg
@pytest.mark.skipif(not REDIS, reason="set LORDE_TEST_REDIS_URL")
def test_time_limit_keeps_meters_and_charges_the_judgments_made(env):
    """B3/S2: a job stopped by the time limit used to discard everything, refund its reservation and meter
    nothing. Now its judgments are stored, metered, charged, and offered as partial.jsonl. B9: while it runs the
    job reserves rows x gates of its own dataset (12 x 2), not the org's whole remaining quota."""
    clear_jobs(env)
    with db(env) as c:
        c.execute("delete from usage_events where org_id=%s and source='job'", (env["org"],))
    s = slow_settings(env, job_max_seconds=4)
    jid = submit(env, ROWS * 4, s)  # 12 rows, every judge call takes 1 s
    before = quota_used(env["org"])
    w = Worker(s, worker_id="partial", work_dir=str(env["tmp"] / "partial"), heartbeat_s=0.3)
    t = run_in_thread(w)
    wait_for(lambda: job_state(env, jid)[0] == "running")
    wait_for(lambda: quota_used(env["org"]) > before)
    assert quota_used(env["org"]) - before == 12 * 2  # the reservation: this dataset's rows x gates
    wait_for(lambda: job_state(env, jid)[4] >= 1, 20)  # judgments are stored while the job runs
    t.join(60)
    assert not t.is_alive()
    status, error, counts, results, n, metered = job_state(env, jid)
    assert status == "failed" and "time limit" in error and "kept" in error
    assert 1 <= n < 24 and metered == n and counts["judgments"] == counts["judgments_charged"] == n
    assert quota_used(env["org"]) - before == n  # reservation settled to the judgments made
    assert set(results) == {"partial"}
    with TestClient(create_app(env["s"])) as c:
        r = c.get(f"/v1/jobs/{jid}/results", headers=env["keys"]["all"])
        assert r.status_code == 200 and set(r.json()["files"]) == {"partial"}
        lines = [json.loads(x) for x in c.get(r.json()["files"]["partial"]).text.splitlines()]
    assert len(lines) == n and all(set(x) == {"row_idx", "gate", "score", "decision", "verdict", "lang"}
                                   for x in lines)


@pg
@pytest.mark.skipif(not REDIS, reason="set LORDE_TEST_REDIS_URL")
def test_cancel_keeps_and_charges_partial_work_and_shutdown_charges_nothing(env):
    clear_jobs(env)
    s = slow_settings(env)
    jid = submit(env, ROWS * 4, s)
    before = quota_used(env["org"])
    w = Worker(s, worker_id="cancel", work_dir=str(env["tmp"] / "cancel"), heartbeat_s=0.3)
    t = run_in_thread(w)
    wait_for(lambda: job_state(env, jid)[4] >= 2, 30)
    with TestClient(create_app(env["s"])) as c:
        assert c.post(f"/v1/jobs/{jid}/cancel", headers=env["keys"]["all"]).json()["cancel_requested"] is True
    t.join(60)
    status, _, counts, results, n, _ = job_state(env, jid)
    assert status == "cancelled" and n >= 2 and counts["judgments_charged"] == n and "partial" in results
    assert quota_used(env["org"]) - before == n

    # a deploy (SIGTERM) re-queues the job: its next attempt judges every row again, so this one is not charged
    clear_jobs(env)
    jid = submit(env, ROWS * 4, s)
    before = quota_used(env["org"])
    with db(env) as c:
        metered_before = c.execute("select count(*) from usage_events where source='job'").fetchone()[0]
    w = Worker(s, worker_id="deploy2", work_dir=str(env["tmp"] / "deploy2"), heartbeat_s=0.3)
    t = run_in_thread(w)
    wait_for(lambda: job_state(env, jid)[4] >= 1, 30)
    w._stop_loop.call_soon_threadsafe(w.stop)
    t.join(60)
    status, _, counts, _, n, _ = job_state(env, jid)
    assert status == "queued" and counts["progress"] == "requeued" and n >= 1  # stored judgments are kept
    assert quota_used(env["org"]) == before
    with db(env) as c:
        assert c.execute("select count(*) from usage_events where source='job'").fetchone()[0] == metered_before
    clear_jobs(env)


@pg
def test_rows_over_the_job_cap_fail_before_judging(env):
    clear_jobs(env)
    s = Settings(**{**env["s"].__dict__, "job_max_rows": 2})
    jid = submit(env, ROWS, s)
    assert run_one(Worker(s, worker_id="cap2", work_dir=str(env["tmp"] / "cap2"), poll_s=0.1)) is True
    status, error, _, _, n, _ = job_state(env, jid)
    assert status == "failed" and "more than 2 rows" in error and n == 0


@pg
def test_0008_applies_on_the_production_order_and_is_rerunnable():
    """Production applied 0006 before 0005, then 0007; 0008 must apply on that, twice, and match a fresh install."""
    from test_supabase_sql import SUPA

    mig = {f.name[:4]: f for f in (SUPA / "migrations").glob("*.sql")}
    name = f"lorde_prod8_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(f"create database {name}")
    url = URL.rsplit("/", 1)[0] + "/" + name
    try:
        with psycopg.connect(url, autocommit=True) as c:
            for f in [SUPA / "tests" / "supabase_shim.sql"] + [mig[n] for n in ("0001", "0002", "0003", "0004",
                                                                                "0006", "0005", "0007", "0008")]:
                with c.transaction():
                    c.execute(f.read_text())
            with c.transaction():
                c.execute(mig["0008"].read_text())  # re-runnable
            assert c.execute("select count(*) from information_schema.columns where table_name='jobs' "
                             "and column_name='delete_requested'").fetchone()[0] == 1
    finally:
        with psycopg.connect(URL, autocommit=True) as c:
            c.execute(f"drop database {name} with (force)")


# ------------------------------------------------------------------ B10, B17: Hub datasets

@pg
def test_whole_hub_repos_are_refused_at_creation_and_in_the_worker(env):
    """B10: the API image has no `datasets` library, so a repo-only dataset_url can never run: 422 up front."""
    from test_gateway import mcp_call

    from gateway.jobs import HF_FILE

    clear_jobs(env)
    h = env["keys"]["all"]
    with TestClient(create_app(env["s"])) as c:
        for url in ("hf://datasets/Oxlino/lorde-data", "hf://datasets/Oxlino/lorde-data/splits"):
            r = c.post("/v1/jobs", headers=h, json={"dataset_url": url})
            assert r.status_code == 422 and r.json()["error"]["code"] == "unsupported_dataset", r.text
        r = mcp_call(c, h, "tools/call", {"name": "lorde_create_gating_job",
                                          "arguments": {"dataset_url": "hf://datasets/a/b"}}).json()
        assert r["result"]["isError"] and "unsupported_dataset: " in r["result"]["content"][0]["text"]
        r = c.post("/v1/jobs", headers=h, json={"dataset_url": "hf://datasets/a/b/data/train.jsonl"})
        assert r.status_code == 201
    assert HF_FILE.match("hf://datasets/a/b/x.parquet") and not HF_FILE.match("hf://datasets/a/b/x.txt")
    clear_jobs(env)
    with db(env) as c:  # queued before the Gateway refused them: the worker fails it with the same advice
        jid = c.execute("insert into jobs (org_id, config) values (%s, %s) returning id",
                        (env["org"], json.dumps({"gates": {"grounding": 0.7, "reward": None}, "options": {},
                                                 "source": {"dataset_url": "hf://datasets/a/b"}}))).fetchone()[0]
    from test_jobs import work

    assert run_one(work(env)) is True
    status, error, *_ = job_state(env, jid)
    assert status == "failed" and "whole Hub dataset repo is not supported" in error


def test_hub_file_size_is_checked_before_download_and_the_revision_is_pinned(tmp_path, monkeypatch):
    """B17: the size comes from Hub metadata at a resolved commit, before any download; the download is pinned to
    that commit."""
    import huggingface_hub

    from worker.main import InputRejected

    calls: dict = {"download": []}

    class FakeApi:
        def __init__(self, token=None):
            assert token is False

        def dataset_info(self, repo, **kw):
            return type("Info", (), {"sha": "c0ffee"})()

        def get_paths_info(self, repo, paths, repo_type=None, revision=None):
            calls["revision"] = revision
            return [type("F", (), {"path": paths[0], "size": calls["size"]})()]

    def fake_download(repo, filename, repo_type=None, revision=None, local_dir=None, token=None):
        calls["download"].append(revision)
        out = tmp_path / "dl.jsonl"
        out.write_text('{"instruction": "q", "output": "a"}\n')
        return str(out)

    monkeypatch.setattr(huggingface_hub, "HfApi", FakeApi)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_download)
    w = Worker(Settings(database_url="postgresql://unused/x", storage_dir=str(tmp_path / "st"), key_pepper="p" * 40),
               work_dir=str(tmp_path / "w"))
    job = {"org_id": uuid.uuid4(), "config": {"source": {"dataset_url": "hf://datasets/o/n/d.jsonl"}}}
    calls["size"] = w.s.job_max_input_bytes + 1
    with pytest.raises(InputRejected, match="MB; the limit is"):
        asyncio.run(w.fetch_input(job, tmp_path))
    assert calls["download"] == [] and calls["revision"] == "c0ffee"  # refused before downloading anything
    calls["size"] = 10
    out = asyncio.run(w.fetch_input(job, tmp_path))
    assert calls["download"] == ["c0ffee"] and out["hf_revision"] == "c0ffee" and out["format"] == "jsonl"


def test_upstream_timeout_covers_queueing_behind_job_judgments_in_serve(tmp_path):
    """B9: with one Serve slot shared by API calls and the job worker, an API call may wait in Serve for a job
    judgment before its own runs; its HTTP timeout covers both, so it is not timed out (and charged) for that."""
    one = Settings(upstream_concurrency=1, upstream_timeout_s=30)
    assert one.api_upstream_slots == 1 and one.upstream_read_timeout_s == 60
    assert Settings(upstream_concurrency=3, upstream_timeout_s=30).upstream_read_timeout_s == 30  # 2 API + 1 job
    assert Settings(upstream_concurrency=1, upstream_timeout_s=30,
                    worker_shares_upstream=False).upstream_read_timeout_s == 30
    from gateway.upstream import Upstream

    up = Upstream(Settings(upstream_concurrency=1, upstream_timeout_s=7))
    assert up.client.timeout.read == 14
    from gateway.errors import ModelUnavailable, refundable

    timeout = ModelUnavailable("upstream timeout")
    timeout.charged = True  # a reply that really took longer than the whole budget is still charged
    assert not refundable(timeout)


def test_default_job_row_cap_fits_the_time_limit_on_one_cpu_slot():
    s = Settings()
    assert s.job_max_rows * 2 * 30 <= s.job_max_seconds  # 2 gates x 30 s per judgment, worst case on CPU
