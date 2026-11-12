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
    """A confirmed sign-up (e.g. GitHub OAuth, or email confirmed): the onboarding trigger creates the org."""
    with admin(url) as c:
        uid = c.execute("insert into auth.users (email, email_confirmed_at) values (%s, now()) returning id",
                        (email,)).fetchone()[0]
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
    """Users write no storage objects at all, not even in their own org's folder: since 0008 (audit 2, S3) objects
    are written only through Gateway-issued uploads, which always have a datasets row. Reads stay org-scoped."""
    a, org_a = signup(db, "s@x.io")
    _, org_b = signup(db, "t@x.io")
    for path in (f"{org_a}/d1/data.jsonl", f"{org_b}/d1/x.jsonl"):
        with admin(db) as c:
            as_user(c, a)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute("insert into storage.objects (bucket_id, name) values ('datasets', %s)", (path,))
    with admin(db) as c:
        c.execute("insert into storage.objects (bucket_id, name) values ('datasets', %s), ('datasets', %s)",
                  (f"{org_a}/d2/mine.jsonl", f"{org_b}/d2/theirs.jsonl"))
        as_user(c, a)
        assert [r[0] for r in c.execute("select name from storage.objects where name like '%%/d2/%%'")] == [
            f"{org_a}/d2/mine.jsonl"]


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
        did, dpath = c.execute(
            "insert into datasets (id, org_id, name, storage_path, expires_at) select g, %s, 'x.jsonl', "
            "%s || '/' || g || '/x.jsonl', now() - interval '1 day' from gen_random_uuid() g "
            "returning id, storage_path", (org, str(org))).fetchone()
        jid = c.execute("insert into jobs (org_id, config, status, finished_at, results) values (%s, '{}', 'succeeded', "
                        "now() - interval '90 days', '{\"passed\": \"results/x/passed.jsonl\"}') returning id",
                        (org,)).fetchone()[0]
        rows = c.execute("select kind, id, path from expired_objects() where org_id=%s", (org,)).fetchall()
        assert ("dataset", did, dpath) in rows and ("result", jid, "results/x/passed.jsonl") in rows
        c.execute("select finish_purge(%s, %s)", ([did], [jid]))
        assert c.execute("select count(*) from datasets where id=%s", (did,)).fetchone()[0] == 0
        assert c.execute("select count(*) from audit_log where org_id=%s and action='data.delete'",
                         (org,)).fetchone()[0] == 2


def test_key_changes_are_audited_by_trigger(db):
    a, org = signup(db, "audit@x.io")
    with admin(db) as c:
        as_user(c, a)
        c.execute("insert into api_keys (org_id, name, prefix, key_hash) values (%s, 'k', 'lorde_sk_AUDIT001', 'h')", (org,))
        c.execute("update api_keys set revoked_at = now() where prefix = 'lorde_sk_AUDIT001'")
    with admin(db) as c:
        rows = c.execute("select actor, action from audit_log where target = 'lorde_sk_AUDIT001' order by id").fetchall()
    assert rows == [(str(a), "key.create"), (str(a), "key.revoke")]


# ------------------------------------------------------------------ 0005_hardening regressions

def test_org_created_only_once_email_is_confirmed(db):
    """S8: an unconfirmed sign-up creates no org; confirming creates exactly one; later updates add none."""
    with admin(db) as c:
        uid = c.execute("insert into auth.users (email) values ('bot@x.io') returning id").fetchone()[0]
        assert c.execute("select count(*) from org_members where user_id=%s", (uid,)).fetchone()[0] == 0
        c.execute("update auth.users set email_confirmed_at = now() where id=%s", (uid,))
        rows = c.execute("select o.name, m.role from org_members m join orgs o on o.id = m.org_id "
                         "where m.user_id=%s", (uid,)).fetchall()
        assert rows == [("bot's org", "owner")]
        c.execute("update auth.users set email_confirmed_at = now() + interval '1 s' where id=%s", (uid,))
        c.execute("update auth.users set email_confirmed_at = null where id=%s", (uid,))
        c.execute("update auth.users set email_confirmed_at = now() where id=%s", (uid,))
        c.execute("update auth.users set email = 'bot2@x.io' where id=%s", (uid,))
        assert c.execute("select count(*) from org_members where user_id=%s", (uid,)).fetchone()[0] == 1


def test_users_cannot_write_datasets_and_paths_are_checked(db):
    """S1: only the Gateway writes datasets rows, and only as {org_id}/{id}/{name}."""
    a, org = signup(db, "ds@x.io")
    _, other = signup(db, "victim@x.io")
    with admin(db) as c:
        as_user(c, a)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("insert into datasets (org_id, name, storage_path) values (%s, 'x.jsonl', %s)",
                      (org, f"{other}/d/x.jsonl"))
    with admin(db) as c:  # service role (the Gateway): a forged or traversing path is refused too
        for name, path in [("x.jsonl", f"{other}/{uuid.uuid4()}/x.jsonl"), ("x.jsonl", "../../etc/passwd"),
                           ("../x", None), (None, f"{org}/a/b")]:
            did = uuid.uuid4()
            with pytest.raises(psycopg.errors.CheckViolation):
                c.execute("insert into datasets (id, org_id, name, storage_path) values (%s, %s, %s, %s)",
                          (did, org, name, path or f"{org}/{did}/{name}"))
        did = uuid.uuid4()
        c.execute("insert into datasets (id, org_id, name, storage_path) values (%s, %s, 'ok.jsonl', %s)",
                  (did, org, f"{org}/{did}/ok.jsonl"))
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("update datasets set storage_path = %s where id = %s", (f"{other}/{did}/ok.jsonl", did))


def test_api_key_limits_and_one_way_revocation(db):
    """S2: rpm cap, known scopes, created_by from the session, immutable fields, no un-revoke."""
    a, org = signup(db, "keys@x.io")
    b, _ = signup(db, "someone@x.io")
    with admin(db) as c:
        as_user(c, a)
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("insert into api_keys (org_id, name, prefix, key_hash, rpm) values "
                      "(%s, 'k', 'lorde_sk_RPMCAP01', 'h', 2147483647)", (org,))
    with admin(db) as c:
        as_user(c, a)
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("insert into api_keys (org_id, name, prefix, key_hash, scopes) values "
                      "(%s, 'k', 'lorde_sk_SCOPES01', 'h', '{judge,admin}')", (org,))
    with admin(db) as c:
        as_user(c, a)
        c.execute("insert into api_keys (org_id, name, prefix, key_hash, rpm, created_by) values "
                  "(%s, 'k', 'lorde_sk_HARDEN01', 'h', 6000, %s)", (org, b))
        assert c.execute("select created_by from api_keys where prefix='lorde_sk_HARDEN01'").fetchone()[0] == a
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("update api_keys set rpm = 100000 where prefix = 'lorde_sk_HARDEN01'")
    with admin(db) as c:
        as_user(c, a)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("update api_keys set scopes = '{judge}' where prefix = 'lorde_sk_HARDEN01'")
    with admin(db) as c:
        as_user(c, a)
        c.execute("update api_keys set name = 'renamed' where prefix = 'lorde_sk_HARDEN01'")
        c.execute("update api_keys set revoked_at = now() where prefix = 'lorde_sk_HARDEN01'")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("update api_keys set revoked_at = null where prefix = 'lorde_sk_HARDEN01'")
    with admin(db) as c:  # one-way for the service role too
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("update api_keys set revoked_at = null where prefix = 'lorde_sk_HARDEN01'")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("update api_keys set revoked_at = now() + interval '1 day' where prefix = 'lorde_sk_HARDEN01'")
        assert c.execute("select revoked_at is not null from api_keys where prefix = 'lorde_sk_HARDEN01'").fetchone()[0]


def test_org_member_roles_and_last_owner(db):
    """S3: admins cannot mint owners/admins or touch the owner; the last owner cannot leave or be demoted."""
    owner, org = signup(db, "boss@x.io")
    adm, _ = signup(db, "adm@x.io")
    stranger, _ = signup(db, "stranger@x.io")
    other, _ = signup(db, "other@x.io")
    with admin(db) as c:
        c.execute("insert into org_members (org_id, user_id, role) values (%s, %s, 'admin')", (org, adm))
    for sql, args in [("insert into org_members (org_id, user_id, role) values (%s, %s, 'owner')", (org, stranger)),
                      ("insert into org_members (org_id, user_id, role) values (%s, %s, 'admin')", (org, stranger))]:
        with admin(db) as c:
            as_user(c, adm)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql, args)
    with admin(db) as c:
        as_user(c, adm)
        assert c.execute("update org_members set role = 'member' where user_id = %s returning 1",
                         (owner,)).fetchall() == []
        assert c.execute("delete from org_members where user_id = %s returning 1", (owner,)).fetchall() == []
        # since 0008 (audit 2, S5) members join by accepting an invitation, not by a direct insert
        invite = c.execute("select create_invite(%s, 'stranger@x.io', 'member')", (org,)).fetchone()[0]
    with admin(db) as c:
        as_user(c, stranger)
        c.execute("select accept_invite(%s)", (invite,))
    with admin(db) as c:
        as_user(c, adm)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # cannot promote the member either
            c.execute("update org_members set role = 'owner' where user_id = %s and org_id = %s", (stranger, org))
    with admin(db) as c:
        as_user(c, owner)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("update org_members set role = 'admin' where user_id = %s and org_id = %s", (owner, org))
    with admin(db) as c:
        as_user(c, owner)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("delete from org_members where user_id = %s and org_id = %s", (owner, org))
    with admin(db) as c:
        as_user(c, owner)  # with a second owner, the first may step down
        c.execute("update org_members set role = 'owner' where user_id = %s and org_id = %s", (adm, org))
        c.execute("delete from org_members where user_id = %s and org_id = %s", (owner, org))
    with admin(db) as c:
        as_user(c, other)  # outsiders see and change nothing
        assert c.execute("delete from org_members where org_id = %s returning 1", (org,)).fetchall() == []
    with admin(db) as c:  # deleting the user or the org still cascades
        c.execute("delete from auth.users where id = %s", (adm,))
        assert c.execute("select count(*) from org_members where org_id=%s and role='owner'", (org,)).fetchone()[0] == 0
        c.execute("delete from orgs where id = %s", (org,))
        assert c.execute("select count(*) from org_members where org_id=%s", (org,)).fetchone()[0] == 0


def test_requeue_cancels_cancel_requested_and_judgments_are_unique(db):
    """B8: a stale job whose cancel was requested ends cancelled; judgments have one row per row and gate."""
    _, org = signup(db, "b8@x.io")
    with admin(db) as c:
        jid = c.execute("insert into jobs (org_id, config, status, attempts, heartbeat_at, cancel_requested) values "
                        "(%s, '{}', 'running', 1, now() - interval '5 minutes', true) returning id", (org,)).fetchone()[0]
        c.execute("select requeue_stale_jobs()")
        status, finished = c.execute("select status, finished_at from jobs where id=%s", (jid,)).fetchone()
        assert status == "cancelled" and finished is not None
        c.execute("insert into judgments (job_id, row_idx, gate, score) values (%s, 0, 'reward', 0.5)", (jid,))
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute("insert into judgments (job_id, row_idx, gate, score) values (%s, 0, 'reward', 0.6)", (jid,))
        c.execute("insert into judgments (job_id, row_idx, gate, score) values (%s, 0, 'reward', 0.6) "
                  "on conflict (job_id, row_idx, gate) do nothing", (jid,))
        c.execute("insert into judgments (job_id, row_idx, gate, score) values (%s, 0, 'grounding', 0.6)", (jid,))
        assert c.execute("select count(*) from judgments where job_id=%s", (jid,)).fetchone()[0] == 2


def test_function_privileges(db):
    """S13: anon cannot call the SECURITY DEFINER helpers; queue functions are service-only."""
    with admin(db) as c:
        def can(role, fn):
            return c.execute("select has_function_privilege(%s, %s, 'execute')", (role, fn)).fetchone()[0]
        for fn in ("public.is_member(uuid)", "public.is_admin(uuid)", "public.is_owner(uuid)"):
            assert not can("anon", fn) and can("authenticated", fn)
        for fn in ("public.handle_new_user()", "public.audit_api_keys()", "public.api_keys_guard()",
                   "public.claim_job(text, int)", "public.requeue_stale_jobs(interval, int)",
                   "public.expired_objects()", "public.finish_purge(uuid[], uuid[])"):
            assert not can("anon", fn) and not can("authenticated", fn), fn
        cfg = dict(c.execute("select p.proname, array_to_string(p.proconfig, ',') from pg_proc p "
                             "join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'public'").fetchall())
        for fn in ("claim_job", "requeue_stale_jobs", "expired_objects", "finish_purge", "is_member", "is_owner",
                   "expired_objects_by_retention", "review_queue"):
            assert "search_path=public" in (cfg.get(fn) or ""), fn


# ------------------------------------------------------------------ 0007_fairness

def test_claim_job_round_robin_across_orgs(db):
    """N1c: org B's first job runs before org A's second, though A queued both first."""
    orgs = [signup(db, f"rr{i}@x.io")[1] for i in range(3)]
    a, b, c_org = orgs
    with admin(db) as c:
        c.execute("update jobs set status = 'cancelled' where status = 'queued'")  # other tests' leftovers
        a1 = c.execute("insert into jobs (org_id, config) values (%s, '{}') returning id", (a,)).fetchone()[0]
        a2 = c.execute("insert into jobs (org_id, config) values (%s, '{}') returning id", (a,)).fetchone()[0]
        a3 = c.execute("insert into jobs (org_id, config) values (%s, '{}') returning id", (a,)).fetchone()[0]
        b1 = c.execute("insert into jobs (org_id, config) values (%s, '{}') returning id", (b,)).fetchone()[0]
        # org C last started a job yesterday: it is due before A, which started one a moment ago
        c.execute("insert into jobs (org_id, config, status, started_at, finished_at) values "
                  "(%s, '{}', 'succeeded', now() - interval '1 day', now() - interval '1 day')", (c_org,))
        c1 = c.execute("insert into jobs (org_id, config) values (%s, '{}') returning id", (c_org,)).fetchone()[0]

        def claim():
            r = c.execute("select id from claim_job('rr')").fetchone()
            return r and r[0]

        assert claim() == a1  # A and B never started a job (they come first): the older job wins
        assert claim() == b1  # B has never started one; A just did
        assert claim() == c1  # C's latest start (yesterday) is older than A's
        assert claim() == a2
        assert claim() == a3
        assert claim() is None
        assert c.execute("select count(*) from jobs where org_id=%s and status='running'", (a,)).fetchone()[0] == 3


def test_0007_validates_0005_constraints(db):
    with admin(db) as c:
        rows = dict(c.execute("select conname, convalidated from pg_constraint where conname in "
                              "('api_keys_prefix_shape', 'api_keys_scopes_known', 'datasets_storage_path_shape')"))
    assert rows == {"api_keys_prefix_shape": True, "api_keys_scopes_known": True, "datasets_storage_path_shape": True}


def apply(c, path):
    with c.transaction():  # as gateway/migrate.py does: one transaction per file
        c.execute(path.read_text())


def test_migrations_in_production_order():
    """Production applied 0006 before 0005; 0007 must apply cleanly on top of that (and be re-runnable)."""
    mig = {f.name[:4]: f for f in (SUPA / "migrations").glob("*.sql")}
    name = f"lorde_prod_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(f"create database {name}")
    url = URL.rsplit("/", 1)[0] + "/" + name
    try:
        notices: list[str] = []
        with psycopg.connect(url, autocommit=True) as c:
            c.add_notice_handler(lambda d: notices.append(d.message_primary))
            apply(c, SUPA / "tests" / "supabase_shim.sql")
            for n in ("0001", "0002", "0003", "0004", "0006"):
                apply(c, mig[n])
            org = c.execute("insert into orgs (name) values ('legacy') returning id").fetchone()[0]
            c.execute("insert into api_keys (org_id, name, prefix, key_hash) values (%s, 'old', 'lorde_sk_legacy', 'h')",
                      (org,))  # a key row from before 0005's prefix shape
            apply(c, mig["0005"])

            def cfg(fn):
                return c.execute("select array_to_string(proconfig, ',') from pg_proc where proname=%s",
                                 (fn,)).fetchone()[0] or ""

            assert "search_path" not in cfg("expired_objects_by_retention")  # the N6 gap this order leaves
            apply(c, mig["0007"])
            for fn in ("expired_objects_by_retention", "expired_objects", "claim_job", "review_queue"):
                assert "search_path=public" in cfg(fn), fn
            assert any("api_keys_prefix_shape left NOT VALID" in m for m in notices), notices
            valid = dict(c.execute("select conname, convalidated from pg_constraint where conname in "
                                   "('api_keys_prefix_shape', 'api_keys_scopes_known')"))
            assert valid == {"api_keys_prefix_shape": False, "api_keys_scopes_known": True}
            c.execute("delete from api_keys where prefix = 'lorde_sk_legacy'")
            apply(c, mig["0007"])  # re-runnable; with the row gone the constraint is validated
            assert c.execute("select convalidated from pg_constraint where conname = 'api_keys_prefix_shape'"
                             ).fetchone()[0] is True
            # the queue works on the production-order schema
            c.execute("insert into jobs (org_id, config) values (%s, '{}')", (org,))
            assert c.execute("select count(*) from claim_job('w')").fetchone()[0] == 1
            for fn in ("public.claim_job(text, int)", "public.expired_objects_by_retention()"):
                assert not c.execute("select has_function_privilege('authenticated', %s, 'execute')",
                                     (fn,)).fetchone()[0]
            assert c.execute("select has_function_privilege('authenticated', 'public.review_queue(uuid, int)', "
                             "'execute')").fetchone()[0]
    finally:
        with psycopg.connect(URL, autocommit=True) as c:
            c.execute(f"drop database {name} with (force)")
