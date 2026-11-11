# ruff: noqa: F811  (the module-scoped `env` fixture is imported from test_jobs and requested by name)
"""Worker robustness and tenant isolation on real Postgres (audit S1, S4, S10, B8, M11), plus the
Gateway's job-side features that need a database (Idempotency-Key, model registry, key CLI, MCP errors).

Needs LORDE_TEST_PG_URL (see test_supabase_sql.py).
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
from test_jobs import ROWS, clear_jobs, env, run_one, upload, work  # noqa: E402,F401

from gateway.app import create_app  # noqa: E402
from gateway.settings import Settings  # noqa: E402
from worker.main import Worker, child_env, dataset_path_ok  # noqa: E402

URL = os.environ.get("LORDE_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set LORDE_TEST_PG_URL to run Postgres tests")


def db(env):
    return psycopg.connect(env["db"], autocommit=True)


def job_row(env, jid):
    with db(env) as c:
        return c.execute("select status, error, attempts, locked_by, counts from jobs where id=%s", (jid,)).fetchone()


def submit(env, rows, s=None, **body):
    with TestClient(create_app(s or env["s"])) as c:
        h = env["keys"]["all"]
        up = upload(c, h, rows)
        r = c.post("/v1/jobs", headers=h, json={"upload_id": up, **body})
        assert r.status_code == 201, r.text
        return r.json()["id"]


def test_dataset_path_check():
    org, ds = str(uuid.uuid4()), str(uuid.uuid4())
    assert dataset_path_ok(f"{org}/{ds}/data.jsonl", org, ds)
    for bad in [f"{uuid.uuid4()}/{ds}/data.jsonl", f"{org}/{ds}/../x/data.jsonl", f"{org}/{ds}/a/b.jsonl",
                f"{org}/{ds}/", "../../etc/passwd", f"{org}/{uuid.uuid4()}/data.jsonl"]:
        assert not dataset_path_ok(bad, org, ds), bad
    assert not dataset_path_ok(f"{org}/{ds}/d.jsonl", org, None)


def test_child_gets_a_minimal_environment(monkeypatch, tmp_path):
    for k in ("DATABASE_URL", "LORDE_KEY_PEPPER", "SUPABASE_SERVICE_ROLE_KEY", "HF_TOKEN", "REDIS_URL",
              "LORDE_METRICS_TOKEN", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(k, "secret-" + k)
    monkeypatch.setenv("VLLM_API_KEY", "serve-key")
    e = child_env(tmp_path)
    assert e["VLLM_API_KEY"] == "serve-key" and "PATH" in e
    assert not [v for v in e.values() if v.startswith("secret-")]


def test_forged_dataset_path_fails_the_job_not_the_worker(env):
    clear_jobs(env)
    with db(env) as c:  # a job row written before 0005 (or by hand) that points at another org's file
        other = c.execute("insert into orgs (name) values ('victim') returning id").fetchone()[0]
        jid = c.execute("insert into jobs (org_id, config) values (%s, %s) returning id",
                        (env["org"], json.dumps({"gates": {"grounding": 0.7, "reward": 0.7},
                                                 "options": {"pii": False, "dedup": "exact"},
                                                 "source": {"storage_path": f"{other}/x/data.jsonl",
                                                            "format": "jsonl"}}))).fetchone()[0]
    assert run_one(work(env)) is True
    status, error, *_ = job_row(env, jid)
    assert status == "failed" and "not inside its own organization" in error


def test_purge_survives_bad_rows(env, monkeypatch):
    clear_jobs(env)
    with db(env) as c:
        # rows that predate the storage_path constraint: simulate by lifting it for this insert only
        c.execute("alter table datasets drop constraint datasets_storage_path_shape")
        bad = c.execute("insert into datasets (org_id, name, storage_path, expires_at) values "
                        "(%s, 'x', '../../escape', now() - interval '1 day') returning id", (env["org"],)).fetchone()[0]
        c.execute("alter table datasets add constraint datasets_storage_path_shape check (name is not null and "
                  "storage_path = org_id::text || '/' || id::text || '/' || name) not valid")
    with TestClient(create_app(env["s"])) as c:
        good = upload(c, env["keys"]["all"], ROWS[:1])
    with db(env) as c:
        path = c.execute("update datasets set expires_at = now() - interval '1 day' where id=%s returning storage_path",
                         (good,)).fetchone()[0]
    f = env["tmp"] / "storage" / "datasets" / path
    assert f.exists()
    w = work(env)

    async def go():
        await w.connect()
        try:
            await w.purge()
        finally:
            await w.conn.close()
    asyncio.run(go())
    assert not f.exists()
    with db(env) as c:
        assert c.execute("select count(*) from datasets where id = any(%s)", ([bad, uuid.UUID(good)],)).fetchone()[0] == 0
    # a storage error on one object is logged and retried later, never raised
    with TestClient(create_app(env["s"])) as c:
        again = upload(c, env["keys"]["all"], ROWS[:1])
    with db(env) as c:
        c.execute("update datasets set expires_at = now() - interval '1 day' where id=%s", (again,))

    async def broken(bucket, paths):
        raise RuntimeError("storage down")

    monkeypatch.setattr(w.storage, "delete", broken)
    asyncio.run(go())
    with db(env) as c:
        assert c.execute("select count(*) from datasets where id=%s", (again,)).fetchone()[0] == 1


def test_run_forever_survives_errors_and_stops_on_request(env):
    w = work(env)
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("one bad job")
        if len(calls) == 2:
            raise psycopg.OperationalError("connection dropped")
        w.stop()
        return False

    w.process_one = flaky
    asyncio.run(asyncio.wait_for(w.run_forever(), 30))
    assert len(calls) == 3 and w.conn.closed


def test_row_cap_lang_metering_and_idempotent_judgments(env):
    clear_jobs(env)
    rows = [{**r, "lang": lang} for r, lang in zip(ROWS, ["fr", "hi", "en"], strict=True)]
    s = Settings(**{**env["s"].__dict__, "job_max_rows": 2})
    jid = submit(env, rows, s)
    assert run_one(Worker(s, worker_id="cap", work_dir=str(env["tmp"] / "cap"), poll_s=0.1)) is True
    status, error, *_ = job_row(env, jid)
    assert status == "failed" and "more than 2 rows" in error

    jid = submit(env, rows)
    assert run_one(work(env)) is True
    assert job_row(env, jid)[0] == "succeeded"
    with db(env) as c:
        langs = dict(c.execute("select row_idx, lang from judgments where job_id=%s and gate='grounding'",
                               (jid,)).fetchall())
        assert langs == {0: "fr", 1: "hi", 2: "en"}
        n = c.execute("select count(*) from judgments where job_id=%s", (jid,)).fetchone()[0]
        u = c.execute("select judgments, source, route, key_prefix, latency_ms from usage_events "
                      "where source='job' order by id desc limit 1").fetchone()
        assert u[0] == n and u[1] == "job" and u[2] == "job:gating" and u[3].startswith("lorde_sk_") and u[4] >= 0


def test_time_limit_and_lost_lease(env):
    clear_jobs(env)
    s = Settings(**{**env["s"].__dict__, "vllm_base_url": env["slow_url"], "job_max_seconds": 1})
    jid = submit(env, ROWS * 4, s)
    w = Worker(s, worker_id="limit", work_dir=str(env["tmp"] / "limit"), heartbeat_s=0.3)
    t0 = time.monotonic()
    assert run_one(w) is True
    status, error, *_ = job_row(env, jid)
    assert status == "failed" and "time limit" in error and time.monotonic() - t0 < 30

    # a worker that lost its lease stops its child and does not overwrite the job
    clear_jobs(env)
    s = Settings(**{**env["s"].__dict__, "vllm_base_url": env["slow_url"]})
    jid = submit(env, ROWS * 4, s)
    w = Worker(s, worker_id="loser", work_dir=str(env["tmp"] / "lost"), heartbeat_s=0.3)
    t = threading.Thread(target=run_one, args=(w,))
    t.start()
    for _ in range(100):
        if job_row(env, jid)[0] == "running":
            break
        time.sleep(0.1)
    with db(env) as c:
        c.execute("update jobs set locked_by='thief' where id=%s", (jid,))
    t.join(60)
    assert not t.is_alive()
    status, _, _, locked_by, _ = job_row(env, jid)
    assert status == "running" and locked_by == "thief"
    with db(env) as c:
        c.execute("update jobs set status='cancelled', locked_by=null where id=%s", (jid,))


def test_shutdown_requeues_the_running_job_without_counting_the_attempt(env):
    clear_jobs(env)
    s = Settings(**{**env["s"].__dict__, "vllm_base_url": env["slow_url"]})
    jid = submit(env, ROWS * 4, s)
    w = Worker(s, worker_id="deploy", work_dir=str(env["tmp"] / "deploy"), heartbeat_s=5)
    t = threading.Thread(target=run_one, args=(w,))
    t.start()
    for _ in range(100):
        if job_row(env, jid)[0] == "running":
            break
        time.sleep(0.1)
    time.sleep(1.0)
    w._stop_loop.call_soon_threadsafe(w.stop)  # what the SIGTERM handler does
    t.join(30)
    assert not t.is_alive()
    status, _, attempts, locked_by, counts = job_row(env, jid)
    assert status == "queued" and attempts == 0 and locked_by is None and counts["progress"] == "requeued"
    assert not (env["tmp"] / "deploy" / jid).exists()
    with db(env) as c:
        c.execute("update jobs set status='cancelled' where id=%s", (jid,))


def test_idempotency_key_and_mcp_not_found(env):
    clear_jobs(env)
    from test_gateway import mcp_call

    h = env["keys"]["all"]
    with TestClient(create_app(env["s"])) as c:
        up = upload(c, h, ROWS[:1])
        hk = {**h, "Idempotency-Key": "job-" + uuid.uuid4().hex}
        a = c.post("/v1/jobs", headers=hk, json={"upload_id": up})
        b = c.post("/v1/jobs", headers=hk, json={"upload_id": up})
        assert a.status_code == 201 and b.status_code == 200 and a.json()["id"] == b.json()["id"]
        assert c.post("/v1/jobs", headers={**h, "Idempotency-Key": "bad key!"}, json={"upload_id": up}).status_code == 400
        for jid in ("not-a-uuid", str(uuid.uuid4())):
            r = mcp_call(c, h, "tools/call", {"name": "lorde_job_status", "arguments": {"job_id": jid}}).json()
            assert r["result"]["isError"] and "not_found: " in r["result"]["content"][0]["text"]
    with db(env) as c:
        assert c.execute("select count(*) from audit_log where action='job.create' and target=%s",
                         (a.json()["id"],)).fetchone()[0] == 1


def test_model_registry_promotion(env):
    from gateway.models_cli import promote

    h = env["keys"]["all"]
    promote(env["db"], "Oxlino/lorde-tiny-aya", "v1.0.0-rc1")
    out = promote(env["db"], "Oxlino/lorde-tiny-aya", "v1.0.1", name="Lorde 1.0.1")
    assert out["retired"] == ["Oxlino/lorde-tiny-aya@v1.0.0-rc1"]
    with db(env) as c:
        states = dict(c.execute("select revision, state from model_versions").fetchall())
        assert states == {"v1.0.0-rc1": "retired", "v1.0.1": "active"}
    s = Settings(**{**env["s"].__dict__})
    with TestClient(create_app(s)) as c:
        assert c.get("/v1/models", headers=h).json()["data"][0]["revision"] == "lorde-tiny-aya@v1.0.1"
        info = c.get("/v1/models/lorde", headers=h).json()
        assert info["revision"] == "v1.0.1" and "api_version" in info
    with db(env) as c:
        c.execute("update model_versions set state='retired'")


def test_keys_cli_uses_postgres(env, monkeypatch, capsys):
    from gateway import keys

    monkeypatch.setenv("DATABASE_URL", env["db"])
    monkeypatch.setenv("LORDE_KEY_PEPPER", env["s"].key_pepper)
    keys.main(["create", "--name", "cli", "--org", str(env["org"]), "--scopes", "judge,jobs:read"])
    out = capsys.readouterr().out
    key = out.split()[0]
    with db(env) as c:
        assert c.execute("select scopes from api_keys where prefix=%s", (key[:17],)).fetchone()[0] == ["judge",
                                                                                                      "jobs:read"]
    keys.main(["revoke", key[:17]])
    assert "revoked" in capsys.readouterr().out
    with TestClient(create_app(env["s"])) as c:
        assert c.get("/v1/models", headers={"Authorization": f"Bearer {key}"}).status_code == 403
    with pytest.raises(SystemExit):
        keys.main(["create", "--name", "x", "--org", str(env["org"]), "--rpm", "100000"])
