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
    from gateway.models_cli import installed_fingerprints, promote

    h = env["keys"]["all"]
    # audit 2 (M5): promotion needs a promotable Lorde Bench result and the model's stored template fingerprints
    ok = {"promotable": True, "acceptance": [{"criterion": "κ", "value": 0.8, "met": True, "blocking": True}]}
    fps = installed_fingerprints()
    promote(env["db"], "Oxlino/lorde-tiny-aya", "v1.0.0-rc1", bench=ok, fingerprints=fps)
    out = promote(env["db"], "Oxlino/lorde-tiny-aya", "v1.0.1", name="Lorde 1.0.1", bench=ok, fingerprints=fps)
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


def test_store_content_keeps_row_text_only_when_enabled(env):
    clear_jobs(env)
    with db(env) as c:
        c.execute("update orgs set store_content = true where id=%s", (env["org"],))
    try:
        jid = submit(env, ROWS[:2])
        assert run_one(work(env)) is True
    finally:
        with db(env) as c:
            c.execute("update orgs set store_content = false where id=%s", (env["org"],))
    with db(env) as c:
        p = c.execute("select parsed from judgments where job_id=%s and gate='grounding' and row_idx=1",
                      (jid,)).fetchone()[0]
    assert p["instruction"] == ROWS[1]["instruction"] and p["input"] == ROWS[1]["input"]
    assert p["output"] == ROWS[1]["output"] and p["gate"] == "grounding" and "score" in p and "text" not in p


# ------------------------------------------------------------------ audit N5: no resurrection at finish
# Audit 2 (B3) changed what a cancel keeps: judgments are stored while the job runs and a cancelled job keeps
# (and is charged for) what it judged. Only a data deletion still leaves no judgments and expires the files.

def request_deletion(env):
    """request_org_data_deletion() as the org's owner (what the Console's "Delete data" calls)."""
    with db(env) as c:
        owner = c.execute("select user_id from org_members where org_id=%s and role='owner' limit 1",
                          (env["org"],)).fetchone()[0]
        c.execute("set role authenticated")
        c.execute("select set_config('request.jwt.claims', %s, false)", (json.dumps({"sub": str(owner)}),))
        c.execute("select request_org_data_deletion(%s)", (env["org"],))


def test_data_deletion_during_upload_ends_cancelled_without_judgments_and_expires_results(env, monkeypatch):
    clear_jobs(env)
    jid = submit(env, ROWS)
    w = work(env)
    real_upload = w.storage.upload

    async def upload_then_delete(bucket, path, local, ctype):
        await real_upload(bucket, path, local, ctype)
        request_deletion(env)  # the owner deletes the org's data while the results upload

    monkeypatch.setattr(w.storage, "upload", upload_then_delete)
    assert run_one(w) is True
    with db(env) as c:
        status, results, expire, locked = c.execute(
            "select status, results, results_expire_at, locked_by from jobs where id=%s", (jid,)).fetchone()
        assert status == "cancelled" and locked is None and results and expire is not None
        assert c.execute("select count(*) from judgments where job_id=%s", (jid,)).fetchone()[0] == 0
        expired = {r[0] for r in c.execute("select path from expired_objects() where id=%s", (jid,))}
        assert expired == set(results.values())  # the purge deletes the uploaded files
        actions = [r[0] for r in c.execute("select action from audit_log where target=%s order by id", (str(jid),))]
        assert actions[-1] == "job.cancelled"
        charged = c.execute("select judgments from usage_events where source='job' order by id desc limit 1"
                            ).fetchone()[0]
        assert charged >= 3  # the model did the work: metered even though the data is deleted
    with TestClient(create_app(env["s"])) as c:
        assert c.get(f"/v1/jobs/{jid}/results", headers=env["keys"]["all"]).status_code == 409


def test_cancel_during_upload_ends_cancelled_and_keeps_its_work(env, monkeypatch):
    clear_jobs(env)
    jid = submit(env, ROWS)
    w = work(env)
    real_upload = w.storage.upload

    async def upload_then_cancel(bucket, path, local, ctype):
        await real_upload(bucket, path, local, ctype)
        with db(env) as c:  # what POST /v1/jobs/{id}/cancel does while the results upload
            c.execute("update jobs set cancel_requested = true where id=%s", (jid,))

    monkeypatch.setattr(w.storage, "upload", upload_then_cancel)
    assert run_one(w) is True
    with db(env) as c:
        status, results, expire, counts = c.execute(
            "select status, results, results_expire_at, counts from jobs where id=%s", (jid,)).fetchone()
        assert status == "cancelled" and results and expire is None
        n = c.execute("select count(*) from judgments where job_id=%s", (jid,)).fetchone()[0]
        assert n >= 3 and counts["judgments"] == counts["judgments_charged"] == n
    with TestClient(create_app(env["s"])) as c:
        r = c.get(f"/v1/jobs/{jid}/results", headers=env["keys"]["all"])
        assert r.status_code == 200 and "passed" in r.json()["files"]


def test_finish_failure_keeps_the_stored_judgments_and_charges_them(env, monkeypatch):
    """The finishing transaction fails: the job is failed, not succeeded; the judgments stored while it ran
    stay, and exactly those are metered (they used to be dropped, and nothing was charged)."""
    clear_jobs(env)
    jid = submit(env, ROWS)
    w = work(env)
    real_q = w.q

    async def failing_audit(sql, args=()):
        if sql.startswith("insert into audit_log") and "job.succeeded" in args:
            raise RuntimeError("database hiccup after the judgments were written")
        return await real_q(sql, args)

    monkeypatch.setattr(w, "q", failing_audit)
    assert run_one(w) is True
    with db(env) as c:
        assert c.execute("select status from jobs where id=%s", (jid,)).fetchone()[0] == "failed"
        n = c.execute("select count(*) from judgments where job_id=%s", (jid,)).fetchone()[0]
        metered = c.execute("select judgments from usage_events where source='job' order by id desc limit 1"
                            ).fetchone()[0]
        assert n >= 3 and metered == n


# ------------------------------------------------------------------ audit N1b: the daily quota and jobs

def used_today(env) -> int:
    with db(env) as c:
        return c.execute("select coalesce(sum(judgments), 0) from usage_events where org_id=%s and created_at >= "
                         "date_trunc('day', now() at time zone 'utc') at time zone 'utc'", (env["org"],)).fetchone()[0]


def test_daily_quota_caps_job_rows_and_refuses_jobs(env):
    clear_jobs(env)
    used = used_today(env)
    # two gates per row: 4 judgments left allow 2 of the 3 rows
    s = Settings(**{**env["s"].__dict__, "org_daily_judgments": used + 4})
    jid = submit(env, ROWS, s)
    assert run_one(Worker(s, worker_id="quota", work_dir=str(env["tmp"] / "quota"), poll_s=0.1)) is True
    status, error, _, _, counts = job_row(env, jid)
    assert status == "succeeded", error
    assert counts["rows_in"] == 3 and counts["quota_rows"] == 2 and counts["not_evaluated"] == 1
    assert "not evaluated" in counts["notice"] and counts["evaluated"] <= 2
    with db(env) as c:
        idx = {r[0] for r in c.execute("select row_idx from judgments where job_id=%s", (jid,))}
        assert idx and idx <= {0, 1}
    assert used_today(env) <= used + 4

    # the quota is now used up: POST /v1/jobs is refused, and a job already queued fails without judging
    s = Settings(**{**env["s"].__dict__, "org_daily_judgments": used_today(env)})
    with TestClient(create_app(s)) as c:
        h = env["keys"]["all"]
        up = upload(c, h, ROWS[:1])
        r = c.post("/v1/jobs", headers=h, json={"upload_id": up})
        assert r.status_code == 429 and r.json()["error"]["code"] == "quota_exceeded"
        assert 0 < int(r.headers["retry-after"]) <= 86401
    jid = submit(env, ROWS[:1])  # accepted under the default quota, then run under the used-up one
    assert run_one(Worker(s, worker_id="quota2", work_dir=str(env["tmp"] / "quota2"), poll_s=0.1)) is True
    status, error, *_ = job_row(env, jid)
    assert status == "failed" and "daily quota" in error
    with db(env) as c:
        assert c.execute("select count(*) from judgments where job_id=%s", (jid,)).fetchone()[0] == 0


def test_api_quota_starts_from_todays_metered_usage(env):
    """A fresh Gateway process (in-process counter) still counts what usage_events metered today, jobs included."""
    with db(env) as c:  # e.g. a job metered earlier today
        c.execute("insert into usage_events (org_id, route, judgments, source) values (%s, 'job:gating', 5, 'job')",
                  (env["org"],))
    used = used_today(env)
    s = Settings(**{**env["s"].__dict__, "org_daily_judgments": used + 1})
    with TestClient(create_app(s)) as c:
        h = env["keys"]["judge"]
        body = {"instruction": "Explain X", "response": "X is a thing."}
        assert c.post("/v1/judge/reward", headers=h, json=body).status_code == 200
        r = c.post("/v1/judge/reward", headers=h, json=body)
        assert r.status_code == 429 and r.json()["error"]["code"] == "quota_exceeded"


def test_stamp_ids_keeps_only_the_quota_rows(tmp_path):
    import csv

    import pyarrow as pa
    import pyarrow.parquet as pq

    from worker.run_job import stamp_ids

    rows = [{"instruction": f"q{i}", "output": "a", "lang": "fr"} for i in range(5)]
    src = tmp_path / "in.jsonl"
    src.write_text("\n".join(json.dumps(r) for r in rows))
    langs: dict = {}
    dst, n = stamp_ids(src, "jsonl", tmp_path, 10, langs, keep=2)
    assert n == 5 and [json.loads(x)["id"] for x in dst.read_text().splitlines()] == ["row-0", "row-1"]
    assert set(langs) == {0, 1}
    with (tmp_path / "in.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    dst, n = stamp_ids(tmp_path / "in.csv", "csv", tmp_path, 10, keep=3)
    assert n == 5 and len(dst.read_text().splitlines()) == 4  # header + 3
    pq.write_table(pa.Table.from_pylist(rows), tmp_path / "in.parquet")
    dst, n = stamp_ids(tmp_path / "in.parquet", "parquet", tmp_path, 10, keep=4)
    assert n == 5 and pq.read_table(dst).column("id").to_pylist() == ["row-0", "row-1", "row-2", "row-3"]
