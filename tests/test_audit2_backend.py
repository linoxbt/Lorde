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
from pathlib import Path

import httpx
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


# ------------------------------------------------------------------ B8: MCP uses the REST request models

def gateway_app(tmp_path, **kw):
    from test_gateway import PEPPER, stub_vllm

    from gateway.keys import KeyStore

    s = Settings(key_pepper=PEPPER, keys_file=str(tmp_path / "keys.json"), usage_file=str(tmp_path / "usage.jsonl"),
                 model_revision="v1.0.0", vllm_base_url="http://vllm/v1", redis_url=None, **kw)
    key, _ = KeyStore(s.keys_file, PEPPER).create("t", "org1", ["judge"], 6000)
    seen: list = []
    return create_app(s, transport=stub_vllm("plain", seen)), {"Authorization": f"Bearer {key}"}, seen


def test_mcp_single_item_tools_enforce_the_rest_field_limits(tmp_path):
    from test_gateway import SRC, mcp_call

    app, h, seen = gateway_app(tmp_path, org_daily_judgments=10)
    big = "x" * (3 * 1024 * 1024)  # 3 MB: under the 5 MB body limit, far over the 32,000-character field limit
    with TestClient(app) as c:
        for name, args in [("lorde_judge_reward", {"instruction": big, "response": "r"}),
                           ("lorde_judge_grounding", {"source_text": big, "question": "q", "answer": "a"}),
                           ("lorde_judge_grounding", {"source_text": SRC, "question": "q", "answer": big})]:
            r = mcp_call(c, h, "tools/call", {"name": name, "arguments": args}).json()
            assert r["result"]["isError"] and "payload_too_large: " in r["result"]["content"][0]["text"]
        r = mcp_call(c, h, "tools/call", {"name": "lorde_judge_reward",
                                          "arguments": {"instruction": "", "response": "r"}}).json()
        assert "invalid_request: " in r["result"]["content"][0]["text"]
        r = mcp_call(c, h, "tools/call", {"name": "lorde_judge_reward",
                                          "arguments": {"instruction": "i", "response": "r", "threshold": 7}}).json()
        assert "invalid_request: " in r["result"]["content"][0]["text"]
        assert seen == []  # nothing reached the model server
        assert app.gw.quota.used_sync("org1") == 0  # and no quota was spent
        r = mcp_call(c, h, "tools/call", {"name": "lorde_judge_reward",
                                          "arguments": {"instruction": "Explain X", "response": "X is a thing."}}).json()
        assert not r["result"].get("isError") and len(seen) == 1


def test_default_job_row_cap_fits_the_time_limit_on_one_cpu_slot():
    s = Settings()
    assert s.job_max_rows * 2 * 30 <= s.job_max_seconds  # 2 gates x 30 s per judgment, worst case on CPU


# ------------------------------------------------------------------ B11: synchronous batches

def test_batches_are_capped_and_stop_when_the_client_disconnects(tmp_path):
    """B11: at most 8 items per synchronous batch; once the client has gone, items not yet started are not sent
    to the model and their quota is refunded."""
    from test_gateway_fairness import REWARD, held_upstream, hold, make, until

    assert Settings().max_batch == 8

    async def go():
        release, entered = asyncio.Event(), []
        app, h = make(tmp_path, orgs=("orgD",), transport=held_upstream(release, entered), org_daily_judgments=100)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/v1/judge/batch", headers=h["orgD"], json={"kind": "reward", "items": [REWARD] * 9})
            assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"

        body = json.dumps({"kind": "reward", "items": [hold(), REWARD, REWARD, REWARD]}).encode()
        gone, sent = asyncio.Event(), []
        delivered = False

        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            await gone.wait()
            return {"type": "http.disconnect"}

        async def send(msg):
            sent.append(msg)

        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
                 "scheme": "http", "path": "/v1/judge/batch", "raw_path": b"/v1/judge/batch", "query_string": b"",
                 "root_path": "", "client": ("127.0.0.1", 1), "server": ("t", 80),
                 "headers": [(b"host", b"t"), (b"content-type", b"application/json"),
                             (b"content-length", str(len(body)).encode()),
                             (b"authorization", h["orgD"]["Authorization"].encode())]}
        call = asyncio.create_task(app(scope, receive, send))
        await until(lambda: len(entered) == 1)  # the first item is with the model
        gone.set()                              # the client hangs up
        await asyncio.sleep(0.05)
        release.set()
        await asyncio.wait_for(call, 10)
        assert len(entered) == 1  # the three items after the disconnect never reached the model
        assert app.gw.quota.used_sync("orgD") == 1  # 4 spent up front, 3 refunded
        res = json.loads(b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body"))
        assert [x["error"]["code"] for x in res["results"][1:]] == ["client_disconnected"] * 3
        assert res["summary"]["errors"] == 3

    asyncio.run(go())


def test_sdk_splits_large_batches_into_server_sized_requests(tmp_path):
    from lorde import Lorde
    from test_gateway import PEPPER, stub_vllm

    from gateway.keys import KeyStore

    s = Settings(key_pepper=PEPPER, keys_file=str(tmp_path / "keys.json"), usage_file=str(tmp_path / "u.jsonl"),
                 model_revision="v1.0.0", vllm_base_url="http://vllm/v1", redis_url=None)
    key, _ = KeyStore(s.keys_file, PEPPER).create("t", "org1", ["judge"], 6000)
    with TestClient(create_app(s, transport=stub_vllm())) as c:
        out = Lorde(api_key=key, base_url="http://testserver", http_client=c).judge.batch(
            "reward", [{"instruction": f"Explain {i}", "response": "X is a thing that is long enough to pass."}
                       for i in range(19)])
    assert [r.index for r in out.results] == list(range(19)) and out.summary["total"] == 19
    assert len(out.request_id.split(",")) == 3


# ------------------------------------------------------------------ S1: Sybil starvation of the one slot

def test_global_daily_cap_is_shared_by_every_org_and_refunds_the_org(tmp_path):
    from test_gateway_fairness import REWARD, make

    app, h = make(tmp_path, orgs=("o1", "o2", "o3"), global_daily_cap=3, org_daily_judgments=100)
    with TestClient(app) as c:
        for org in ("o1", "o2", "o3"):
            assert c.post("/v1/judge/reward", headers=h[org], json=REWARD).status_code == 200
        for org in ("o1", "o2"):
            r = c.post("/v1/judge/reward", headers=h[org], json=REWARD)
            e = r.json()["error"]
            assert r.status_code == 429 and e["code"] == "capacity_exceeded" and e["limit"] == 3
            assert "00:00 UTC" in e["message"] and int(r.headers["retry-after"]) >= 1
        r = c.post("/v1/judge/batch", headers=h["o3"], json={"kind": "reward", "items": [REWARD] * 2})
        assert r.status_code == 429 and r.json()["error"]["code"] == "capacity_exceeded"
    assert app.gw.quota.used_sync("o1") == 1 and app.gw.quota.used_sync("o3") == 1  # refused: org not charged


@pytest.mark.skipif(not REDIS, reason="set LORDE_TEST_REDIS_URL")
def test_global_cap_is_atomic_in_redis():
    from gateway.quota import GLOBAL, RedisQuota

    async def go():
        q = RedisQuota(REDIS)
        day = "t" + uuid.uuid4().hex[:8]  # a private "day" so the test's counter starts at 0
        try:
            got = await asyncio.gather(*(q.take(GLOBAL, 1, 5, day=day) for _ in range(30)))
            return sum(1 for ok, _ in got if ok), await q.r.get(q.key(GLOBAL, day))
        finally:
            await q.close()
    ok, stored = asyncio.run(go())
    assert ok == 5 and int(stored) == 5


def test_new_orgs_get_a_lower_daily_quota():
    from datetime import UTC, datetime, timedelta

    from gateway.quota import org_daily_limit

    s = Settings(org_daily_judgments=500, new_org_hours=24, new_org_daily_judgments=50)
    now = datetime.now(UTC)
    assert org_daily_limit(s, now - timedelta(hours=1)) == 50
    assert org_daily_limit(s, now - timedelta(hours=25)) == 500
    assert org_daily_limit(s, None) == 500  # unknown age (file key store): the normal quota
    assert org_daily_limit(Settings(org_daily_judgments=0, new_org_daily_judgments=50), now) == 50
    assert org_daily_limit(Settings(org_daily_judgments=500, new_org_daily_judgments=0), now) == 500


@pg
def test_new_org_quota_applies_through_the_gateway(env):
    with db(env) as c:
        used = c.execute("select coalesce(sum(judgments), 0) from usage_events where org_id=%s and created_at >= "
                         "date_trunc('day', now() at time zone 'utc') at time zone 'utc'", (env["org"],)).fetchone()[0]
        c.execute("update orgs set created_at = now() - interval '1 hour' where id=%s", (env["org"],))
    s = Settings(**{**env["s"].__dict__, "org_daily_judgments": 100_000, "new_org_daily_judgments": used + 1})
    body = {"instruction": "Explain X", "response": "X is a thing."}
    try:
        with TestClient(create_app(s)) as c:
            assert c.post("/v1/judge/reward", headers=env["keys"]["judge"], json=body).status_code == 200
            r = c.post("/v1/judge/reward", headers=env["keys"]["judge"], json=body)
            assert r.status_code == 429 and r.json()["error"]["code"] == "quota_exceeded"
            assert "lower quota" in r.json()["error"]["message"] and r.json()["error"]["limit"] == used + 1
        with db(env) as c:
            c.execute("update orgs set created_at = now() - interval '2 days' where id=%s", (env["org"],))
        with TestClient(create_app(s)) as c:  # a day old: the normal quota (a fresh key cache reads the age)
            assert c.post("/v1/judge/reward", headers=env["keys"]["judge"], json=body).status_code == 200
    finally:
        with db(env) as c:
            c.execute("update orgs set created_at = now() - interval '2 days' where id=%s", (env["org"],))


@pg
def test_jobs_respect_the_global_capacity(env):
    clear_jobs(env)
    with db(env) as c:
        used_all = c.execute("select coalesce(sum(judgments), 0) from usage_events where created_at >= "
                             "date_trunc('day', now() at time zone 'utc') at time zone 'utc'").fetchone()[0]
    jid = submit(env, ROWS)  # accepted under no global cap, then run with the capacity used up
    s = Settings(**{**env["s"].__dict__, "global_daily_cap": used_all})
    assert run_one(Worker(s, worker_id="cap-g", work_dir=str(env["tmp"] / "cap-g"), poll_s=0.1)) is True
    status, error, *_ = job_state(env, jid)
    assert status == "failed" and "daily judgment capacity" in error
    with TestClient(create_app(s)) as c:
        up = upload(c, env["keys"]["all"], ROWS[:1])
        r = c.post("/v1/jobs", headers=env["keys"]["all"], json={"upload_id": up})
        assert r.status_code == 429 and r.json()["error"]["code"] == "capacity_exceeded"


def test_one_waiter_per_org_and_round_robin_hand_over():
    from gateway.slots import Busy, LocalSlots

    async def go():
        slots = LocalSlots(1, 2, 8, 5, org_max_waiters=1)
        order: list[str] = []
        hold = asyncio.Event()

        async def use(org, tag, ev=None):
            async with slots.acquire(org):
                order.append(tag)
                if ev is not None:
                    await ev.wait()
                await asyncio.sleep(0.01)

        holder = asyncio.create_task(use("x", "x1", hold))
        await asyncio.sleep(0.01)
        tasks = [asyncio.create_task(use("a", "a1")), asyncio.create_task(use("a", "a2"))]
        await asyncio.sleep(0.01)
        tasks.append(asyncio.create_task(use("b", "b1")))
        await asyncio.sleep(0.01)
        # org A has two requests in the global queue (2 org slots) and one more waiting for its own slot ...
        tasks.append(asyncio.create_task(use("a", "a3")))
        await asyncio.sleep(0.01)
        with pytest.raises(Busy, match="from this organization are waiting"):  # ... so a fourth is refused
            async with slots.acquire("a"):
                pass
        hold.set()
        await asyncio.gather(holder, *tasks)
        # round-robin by org: B's request is served before A's second, though A queued it first
        assert order == ["x1", "a1", "b1", "a2", "a3"]
        assert slots.global_slot.used == 0 and not slots.global_slot.waiters and slots.orgs == {}

    asyncio.run(go())


def test_busy_refusals_past_the_free_budget_cost_one_judgment(tmp_path):
    from test_gateway_fairness import REWARD, held_upstream, hold, make, until

    async def go():
        release, entered = asyncio.Event(), []
        app, h = make(tmp_path, orgs=("orgA", "orgB"), transport=held_upstream(release, entered),
                      upstream_concurrency=1, upstream_queue_timeout_s=0.1, org_free_busy_per_day=2,
                      org_daily_judgments=100)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            a1 = asyncio.create_task(c.post("/v1/judge/reward", headers=h["orgA"], json=hold()))
            await until(lambda: len(entered) == 1)
            charged = []
            for _ in range(4):
                r = await c.post("/v1/judge/reward", headers=h["orgB"], json=REWARD)
                assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
                charged.append(r.json()["error"].get("charged", 0))
            assert charged == [0, 0, 1, 1]  # two refusals free, then each costs one judgment
            assert app.gw.quota.used_sync("orgB") == 2
            release.set()
            assert (await a1).status_code == 200
        usage = app.gw.usage.queue  # not flushed: no lifespan under ASGITransport
        assert sum(u["judgments"] for u in usage if u["org_id"] == "orgB") == 2  # metered as charged

    asyncio.run(go())


# ------------------------------------------------------------------ S3: storage abuse

def dataset_usage(env, s):
    from gateway.db import JobStore, make_pool
    from gateway.jobs import UPLOAD_URL_TTL_S

    async def go():
        pool = make_pool(env["db"], max_size=1)
        await pool.open()
        try:
            return await JobStore(pool).dataset_usage(str(env["org"]), s.max_upload_bytes, UPLOAD_URL_TTL_S)
        finally:
            await pool.close()
    return asyncio.run(go())


@pg
def test_uploads_are_capped_per_org_by_count_and_bytes(env):
    h = env["keys"]["all"]
    n, used = dataset_usage(env, env["s"])
    s = Settings(**{**env["s"].__dict__, "org_max_datasets": n + 1, "org_max_dataset_bytes": 1 << 40})
    with TestClient(create_app(s)) as c:
        up = upload(c, h, ROWS[:1])  # one more fits; once uploaded it counts at its real size
        r = c.post("/v1/uploads", headers=h, json={"filename": "more.jsonl"})
        assert r.status_code == 429 and r.json()["error"]["code"] == "storage_quota_exceeded"
        assert r.json()["error"]["max_datasets"] == n + 1
    n2, used2 = dataset_usage(env, s)
    assert n2 == n + 1 and used2 - used < 10_000  # the completed upload counts at its size, not 200 MB
    # bytes: a pending upload counts at the upload limit, so only one fits in limit = used + 200 MB
    s = Settings(**{**env["s"].__dict__, "org_max_dataset_bytes": used2 + s.max_upload_bytes})
    with TestClient(create_app(s)) as c:
        assert c.post("/v1/uploads", headers=h, json={"filename": "a.jsonl"}).status_code == 200
        r = c.post("/v1/uploads", headers=h, json={"filename": "b.jsonl"})
        assert r.status_code == 429 and r.json()["error"]["code"] == "storage_quota_exceeded"
    with db(env) as c:  # expired datasets free their share
        c.execute("update datasets set expires_at = now() - interval '1 minute' where org_id=%s", (env["org"],))
    with TestClient(create_app(s)) as c:
        assert c.post("/v1/uploads", headers=h, json={"filename": "c.jsonl"}).status_code == 200
    with db(env) as c:
        assert up and c.execute("select uploaded_at is not null from datasets where id=%s", (up,)).fetchone()[0]


@pg
def test_orphan_dataset_objects_are_purged(env, tmp_path):
    from test_jobs import work

    w = work(env)
    root = env["tmp"] / "storage" / "datasets"
    orphan = root / str(env["org"]) / str(uuid.uuid4()) / "orphan.jsonl"
    fresh = root / str(env["org"]) / str(uuid.uuid4()) / "fresh.jsonl"
    for f in (orphan, fresh):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}\n")
    with TestClient(create_app(env["s"])) as c:
        kept_id = upload(c, env["keys"]["all"], ROWS[:1])
    with db(env) as c:
        kept = root / c.execute("select storage_path from datasets where id=%s", (kept_id,)).fetchone()[0]
    old = time.time() - 2 * 86400
    for f in (orphan, kept):
        os.utime(f, (old, old))

    async def go():
        await w.connect()
        try:
            return await w.purge_orphans()
        finally:
            await w.conn.close()
    assert asyncio.run(go()) >= 1
    assert not orphan.exists() and kept.exists() and fresh.exists()  # a day old and unknown: deleted

    with db(env) as c:  # Supabase Storage: the same rule, from storage.objects
        did = uuid.uuid4()
        kept_path = c.execute("select storage_path from datasets where id=%s", (kept_id,)).fetchone()[0]
        c.execute("insert into storage.objects (bucket_id, name, created_at) values ('datasets', %s, "
                  "now() - interval '2 days'), ('datasets', %s, now() - interval '2 days'), ('datasets', %s, now())",
                  (f"{env['org']}/{did}/gone.jsonl", kept_path, f"{env['org']}/{did}/new.jsonl"))
        paths = {r[0] for r in c.execute("select path from orphan_dataset_objects()")}
        assert f"{env['org']}/{did}/gone.jsonl" in paths
        assert f"{env['org']}/{did}/new.jsonl" not in paths and kept_path not in paths
        fn = "public.orphan_dataset_objects(int)"
        assert not c.execute("select has_function_privilege('authenticated', %s, 'execute')", (fn,)).fetchone()[0]
        assert c.execute("select file_size_limit from storage.buckets where id='datasets'").fetchone()[0] == 200 << 20


# ------------------------------------------------------------------ S4, S7, S10: the HTTP edge

START_API = Path(__file__).resolve().parent.parent / "infra" / "start-api.sh"


def test_unrouted_bodies_are_not_buffered(tmp_path):
    """S4: an anonymous POST to a path outside the keyed API is answered without its body being read."""
    app, _, _ = gateway_app(tmp_path)

    async def go():
        reads = 0

        async def receive():
            nonlocal reads
            reads += 1
            return {"type": "http.request", "body": b"x" * 65536, "more_body": True}  # an endless body

        sent = []

        async def send(msg):
            sent.append(msg)

        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
                 "scheme": "http", "path": "/anything", "raw_path": b"/anything", "query_string": b"",
                 "root_path": "", "client": ("203.0.113.9", 1), "server": ("t", 80), "headers": [(b"host", b"t")]}
        await asyncio.wait_for(app(scope, receive, send), 5)
        return reads, next(m["status"] for m in sent if m["type"] == "http.response.start")

    reads, status = asyncio.run(go())
    assert status == 404 and reads == 0


def test_start_script_trusts_only_the_proxy_hop_and_disables_uvicorns_access_log():
    import re

    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    text = START_API.read_text()
    assert "--forwarded-allow-ips='*'" not in text and "--no-access-log" in text
    assert "--limit-concurrency" in text and "--timeout-keep-alive" in text
    hops = re.search(r'PROXY_HOPS="\$\{FORWARDED_ALLOW_IPS:-([^}]+)\}"', text).group(1)

    async def peer_seen(client, xff):
        seen = {}

        async def app(scope, receive, send):
            seen["client"] = scope["client"][0]

        scope = {"type": "http", "client": (client, 1), "headers": [(b"x-forwarded-for", xff.encode())]}
        await ProxyHeadersMiddleware(app, trusted_hosts=hops)(scope, None, None)
        return seen["client"]

    # Railway's edge (a CGNAT address) appends the real client; a client-written hop to its left is ignored
    assert asyncio.run(peer_seen("100.64.0.2", "6.6.6.6, 203.0.113.5")) == "203.0.113.5"
    assert asyncio.run(peer_seen("100.64.0.2", "203.0.113.5")) == "203.0.113.5"
    # a request that does not come from a proxy keeps its own address whatever it claims
    assert asyncio.run(peer_seen("198.51.100.7", "6.6.6.6")) == "198.51.100.7"


def test_gateway_client_ip_is_the_right_most_untrusted_hop_and_the_relay_still_works(tmp_path):
    from test_gateway_fairness import make

    app, _ = make(tmp_path, orgs=(), trusted_proxies=["10.0.0.0/8"], relay_secret="r" * 32)
    gw = app.gw
    spoofed = {"x-forwarded-for": "6.6.6.6, 203.0.113.5, 10.1.2.3"}
    assert gw.client_ip({"client": ("10.9.9.9", 1)}, spoofed) == "203.0.113.5"
    assert gw.client_ip({"client": ("10.9.9.9", 1)}, {"x-forwarded-for": "10.1.1.1, 10.2.2.2"}) == "10.1.1.1"
    assert gw.client_ip({"client": ("198.51.100.7", 1)}, spoofed) == "198.51.100.7"
    relay = {"x-lorde-relay-secret": "r" * 32, "x-lorde-client-ip": "192.0.2.44", **spoofed}
    assert gw.client_ip({"client": ("54.1.2.3", 1)}, relay) == "192.0.2.44"
    assert gw.client_ip({"client": ("10.9.9.9", 1)}, {**relay, "x-lorde-relay-secret": "nope"}) == "203.0.113.5"


def test_access_log_masks_storage_tokens(tmp_path, caplog):
    import logging

    app, _, _ = gateway_app(tmp_path, storage_dir=str(tmp_path / "st"))
    token = app.gw.storage.token("put", "datasets", "o/d/x.jsonl")
    with caplog.at_level(logging.INFO, logger="lorde.access"), TestClient(app) as c:
        assert c.put(f"/v1/_storage/{token}", content=b"{}\n").status_code == 200
        assert c.get(f"/v1/_storage/{token}").status_code == 404  # a put token cannot read
    lines = [r for r in caplog.records if r.name == "lorde.access"]
    assert len(lines) == 2 and all(token not in r.getMessage() and token not in json.dumps(r.fields, default=str)
                                   for r in lines)
    assert {r.fields["route"] for r in lines} == {"/v1/_storage/{token}"}


# ------------------------------------------------------------------ S5: membership needs consent

@pytest.fixture(scope="module")
def sqldb():
    from test_supabase_sql import FILES

    name = f"lorde_a2_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(f"create database {name}")
    url = URL.rsplit("/", 1)[0] + "/" + name
    with psycopg.connect(url, autocommit=True) as c:
        for f in FILES:
            c.execute(f.read_text())
    yield url
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(f"drop database {name} with (force)")


@pg
def test_members_join_only_by_accepting_an_invitation(sqldb):
    from test_supabase_sql import admin, as_user, signup

    owner, org = signup(sqldb, "own@inv.io")
    adm, _ = signup(sqldb, "adm@inv.io")
    victim, victim_org = signup(sqldb, "victim@inv.io")
    other, _ = signup(sqldb, "other@inv.io")
    with admin(sqldb) as c:
        c.execute("insert into org_members (org_id, user_id, role) values (%s, %s, 'admin')", (org, adm))
        unconfirmed = c.execute("insert into auth.users (email) values ('late@inv.io') returning id").fetchone()[0]

    def as_(uid):
        c = admin(sqldb)
        as_user(c, uid)
        return c

    for uid in (owner, adm):  # no direct inserts any more, not even of a plain member
        with as_(uid) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("insert into org_members (org_id, user_id, role) values (%s, %s, 'member')", (org, victim))
    with as_(adm) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):  # admins invite members only
        c.execute("select create_invite(%s, 'victim@inv.io', 'admin')", (org,))
    with as_(other) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):  # outsiders cannot invite
        c.execute("select create_invite(%s, 'victim@inv.io')", (org,))
    with as_(owner) as c:
        inv = c.execute("select create_invite(%s, ' Victim@INV.io ', 'admin')", (org,)).fetchone()[0]
        late = c.execute("select create_invite(%s, 'late@inv.io')", (org,)).fetchone()[0]
        assert c.execute("select email, role from org_invites where id=%s", (inv,)).fetchone() == ("victim@inv.io",
                                                                                                  "admin")
    with as_(other) as c:  # someone else cannot see or take the invitation
        assert c.execute("select count(*) from my_invites()").fetchone()[0] == 0
        assert c.execute("select count(*) from org_invites").fetchone()[0] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("select accept_invite(%s)", (inv,))
    with as_(unconfirmed) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):  # email not confirmed yet
        c.execute("select accept_invite(%s)", (late,))
    with admin(sqldb) as c:
        assert c.execute("select count(*) from org_members where org_id=%s and user_id=%s",
                         (org, victim)).fetchone()[0] == 0  # nothing happened without the victim's consent
    with as_(victim) as c:
        assert [r[0] for r in c.execute("select org_id from my_invites()")] == [org]
        assert c.execute("select accept_invite(%s)", (inv,)).fetchone()[0] == org
    with as_(victim) as c, pytest.raises(psycopg.errors.InvalidParameterValue):  # used once
        c.execute("select accept_invite(%s)", (inv,))
    with admin(sqldb) as c:
        roles = dict(c.execute("select org_id, role from org_members where user_id=%s", (victim,)).fetchall())
        assert roles == {victim_org: "owner", org: "admin"}  # their own org is untouched
        actions = [r[0] for r in c.execute("select action from audit_log where org_id=%s and action like 'member.%%' "
                                           "order by id", (org,))]
        assert actions == ["member.invite", "member.invite", "member.join"]
        c.execute("update org_invites set expires_at = now() - interval '1 second' where id=%s", (late,))
        c.execute("update auth.users set email_confirmed_at = now() where id=%s", (unconfirmed,))
    with as_(unconfirmed) as c, pytest.raises(psycopg.errors.InvalidParameterValue):  # expired
        c.execute("select accept_invite(%s)", (late,))
    with admin(sqldb) as c:
        for fn in ("public.create_invite(uuid, text, org_role)", "public.my_invites()", "public.accept_invite(uuid)"):
            assert not c.execute("select has_function_privilege('anon', %s, 'execute')", (fn,)).fetchone()[0]
            assert c.execute("select has_function_privilege('authenticated', %s, 'execute')", (fn,)).fetchone()[0]
        cfg = dict(c.execute("select proname, array_to_string(proconfig, ',') from pg_proc where proname in "
                             "('create_invite', 'my_invites', 'accept_invite')").fetchall())
        assert all("search_path=public" in v for v in cfg.values()) and len(cfg) == 3


# ------------------------------------------------------------------ S9: the job child cannot read its parent

PARENT = r'''
import importlib.util, subprocess, sys
spec = importlib.util.spec_from_file_location("procsec", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
if sys.argv[2] == "1":
    assert m.make_undumpable()
child = "import os\ntry:\n    print('READ' if b'pepper-123' in open(f'/proc/{os.getppid()}/environ', 'rb').read() else 'NO')\n" \
        "except PermissionError:\n    print('DENIED')"
print(subprocess.run([sys.executable, "-I", "-c", child], capture_output=True, text=True, env={}).stdout.strip())
'''


@pytest.mark.skipif(not (hasattr(os, "geteuid") and os.geteuid() == 0 and os.path.exists("/proc/self/environ")),
                    reason="needs Linux and root (to run the check as an unprivileged uid)")
def test_job_child_cannot_read_an_undumpable_parents_environment():
    """S9: the worker and Gateway mark themselves non-dumpable, so a process of the same uid (the job child)
    cannot read their /proc/<pid>/environ. Checked as uid 65534 with the system Python (no venv access needed)."""
    import shutil
    import subprocess

    py = shutil.which("python3", path="/usr/local/bin:/usr/bin:/bin")
    procsec = Path(__file__).resolve().parent.parent / "gateway" / "procsec.py"

    def run(flag):
        return subprocess.run([py, "-I", "-c", PARENT, str(procsec), flag], capture_output=True, text=True,
                              user=65534, group=65534, cwd="/", env={"SECRET_X": "pepper-123"}, timeout=30)

    control, hardened = run("0"), run("1")
    assert control.stdout.strip() == "READ", control.stderr  # same uid, dumpable: the secret leaks
    assert hardened.stdout.strip() == "DENIED", hardened.stderr


def test_entrypoints_mark_themselves_undumpable():
    import inspect

    import gateway.app
    import worker.main

    assert "make_undumpable()" in inspect.getsource(worker.main.main)
    assert "make_undumpable()" in inspect.getsource(gateway.app.__getattr__)