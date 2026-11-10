"""Supabase migrations on real Postgres: onboarding, RLS, the SKIP LOCKED queue, requeue and retention.

Runs when LORDE_TEST_PG_URL points at a Postgres superuser connection (CI uses a service container), e.g.
LORDE_TEST_PG_URL=postgresql://postgres@127.0.0.1:54329/postgres
"""

import json
import os
import threading
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
URL = os.environ.get("LORDE_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set LORDE_TEST_PG_URL to run Postgres tests")
SUPA = Path(__file__).resolve().parent.parent / "supabase"
FILES = [SUPA / "tests" / "supabase_shim.sql", *sorted((SUPA / "migrations").glob("*.sql"))]


@pytest.fixture(scope="module")
def db():
    name = f"lorde_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(f"create database {name}")
    url = URL.rsplit("/", 1)[0] + "/" + name
    with psycopg.connect(url, autocommit=True) as c:
        for f in FILES:
            c.execute(f.read_text())
    yield url
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(f"drop database {name} with (force)")


def admin(url):
    return psycopg.connect(url, autocommit=True)


def as_user(conn, uid):
    conn.execute("set role authenticated")
    conn.execute("select set_config('request.jwt.claims', %s, false)", (json.dumps({"sub": str(uid)}),))


def signup(url, email):
    with admin(url) as c:
        uid = c.execute("insert into auth.users (email) values (%s) returning id", (email,)).fetchone()[0]
        org = c.execute("select org_id from org_members where user_id = %s", (uid,)).fetchone()[0]
    return uid, org


def test_signup_creates_org_with_owner(db):
    uid, org = signup(db, "ada@example.com")
    with admin(db) as c:
        assert c.execute("select role from org_members where user_id=%s", (uid,)).fetchone()[0] == "owner"
        assert c.execute("select name from orgs where id=%s", (org,)).fetchone()[0] == "ada's org"


def test_rls_isolates_orgs_and_hides_key_hash(db):
    a, org_a = signup(db, "a@x.io")
    b, org_b = signup(db, "b@x.io")
    m, _ = signup(db, "m@x.io")
    with admin(db) as c:
        c.execute("insert into org_members (org_id, user_id, role) values (%s, %s, 'member')", (org_a, m))
        c.execute("insert into api_keys (org_id, name, prefix, key_hash) values (%s, 'k', 'lorde_sk_AAAAAAAA', 'h')",
                  (org_a,))
        c.execute("insert into jobs (org_id, config) values (%s, '{}'), (%s, '{}')", (org_a, org_b))
    with admin(db) as c:
        as_user(c, a)
        assert [r[0] for r in c.execute("select id from orgs")] == [org_a]
        assert c.execute("select count(*) from jobs").fetchone()[0] == 1
        assert c.execute("select prefix from api_keys").fetchone()[0] == "lorde_sk_AAAAAAAA"
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("select key_hash from api_keys")
    with admin(db) as c:
        as_user(c, m)  # plain member: reads keys, cannot create or revoke them, cannot write jobs
        assert c.execute("select count(*) from api_keys").fetchone()[0] == 1
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("insert into api_keys (org_id, name, prefix, key_hash) values (%s, 'x', 'lorde_sk_BBBBBBBB', 'h')",
                      (org_a,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("insert into jobs (org_id, config) values (%s, '{}')", (org_a,))
        assert c.execute("update api_keys set revoked_at = now() returning id").fetchall() == []
    with admin(db) as c:
        as_user(c, b)  # admin of another org sees nothing of org A
        assert c.execute("select count(*) from api_keys").fetchone()[0] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("insert into api_keys (org_id, name, prefix, key_hash) values (%s, 'x', 'lorde_sk_CCCCCCCC', 'h')",
                      (org_a,))
    with admin(db) as c:
        as_user(c, a)  # owner creates a key in their own org
        c.execute("insert into api_keys (org_id, name, prefix, key_hash) values (%s, 'mine', 'lorde_sk_DDDDDDDD', 'h')",
                  (org_a,))


def test_storage_paths_are_scoped_to_org(db):
    a, org_a = signup(db, "s@x.io")
    _, org_b = signup(db, "t@x.io")
    with admin(db) as c:
        as_user(c, a)
        c.execute("insert into storage.objects (bucket_id, name) values ('datasets', %s)", (f"{org_a}/d1/data.jsonl",))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("insert into storage.objects (bucket_id, name) values ('datasets', %s)", (f"{org_b}/d1/x.jsonl",))


@pytest.mark.parametrize("round_", range(5))
def test_claim_job_skip_locked_and_org_cap(db, round_):
    orgs = [signup(db, f"q{round_}{i}@x.io")[1] for i in range(3)]
    with admin(db) as c:
        for org in orgs:
            for _ in range(6):
                c.execute("insert into jobs (org_id, config) values (%s, '{}')", (org,))
    claimed, lock, barrier = [], threading.Lock(), threading.Barrier(8)

    def worker(n):
        with admin(db) as c:
            barrier.wait()
            for _ in range(12):  # a worker polls; an empty claim just means "try again"
                row = c.execute("select id, org_id from claim_job(%s)", (f"w{n}",)).fetchone()
                if row:
                    with lock:
                        claimed.append(row)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    ids = [r[0] for r in claimed]
    assert len(ids) == len(set(ids)), "a job was claimed twice"
    for org in orgs:  # per-org cap of 3 running, and every org got its share
        assert sum(1 for r in claimed if r[1] == org) == 3


def test_requeue_stale_then_fail(db):
    _, org = signup(db, "h@x.io")
    with admin(db) as c:
        jid = c.execute("insert into jobs (org_id, config, status, attempts, heartbeat_at) values "
                        "(%s, '{}', 'running', 1, now() - interval '5 minutes') returning id", (org,)).fetchone()[0]
        assert c.execute("select requeue_stale_jobs()").fetchone()[0] >= 1
        assert c.execute("select status from jobs where id=%s", (jid,)).fetchone()[0] == "queued"
        c.execute("update jobs set status='running', attempts=3, heartbeat_at=now() - interval '5 minutes' where id=%s",
                  (jid,))
        c.execute("select requeue_stale_jobs()")
        status, err = c.execute("select status, error from jobs where id=%s", (jid,)).fetchone()
        assert status == "failed" and "heartbeat" in err


def test_retention_purge_is_audited(db):
    _, org = signup(db, "p@x.io")
    with admin(db) as c:
        did = c.execute("insert into datasets (org_id, storage_path, expires_at) values "
                        "(%s, 'p/x.jsonl', now() - interval '1 day') returning id", (org,)).fetchone()[0]
        jid = c.execute("insert into jobs (org_id, config, status, finished_at, results) values (%s, '{}', 'succeeded', "
                        "now() - interval '90 days', '{\"passed\": \"results/x/passed.jsonl\"}') returning id",
                        (org,)).fetchone()[0]
        rows = c.execute("select kind, id, path from expired_objects() where org_id=%s", (org,)).fetchall()
        assert ("dataset", did, "p/x.jsonl") in rows and ("result", jid, "results/x/passed.jsonl") in rows
        c.execute("select finish_purge(%s, %s)", ([did], [jid]))
        assert c.execute("select count(*) from datasets where id=%s", (did,)).fetchone()[0] == 0
        assert c.execute("select count(*) from audit_log where org_id=%s and action='data.delete'",
                         (org,)).fetchone()[0] == 2
