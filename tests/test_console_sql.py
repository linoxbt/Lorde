"""Console SQL (supabase/migrations/0006_console.sql) on real Postgres: owner-only data deletion that reaches the
retention purge, per-day usage, gate pass rates, job histograms, the review queue and member emails.

Runs when LORDE_TEST_PG_URL points at a Postgres superuser connection, e.g.
LORDE_TEST_PG_URL=postgresql://postgres@127.0.0.1:54329/postgres
"""

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
URL = os.environ.get("LORDE_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set LORDE_TEST_PG_URL to run Postgres tests")
SUPA = Path(__file__).resolve().parent.parent / "supabase"
FILES = [SUPA / "tests" / "supabase_shim.sql", *sorted((SUPA / "migrations").glob("*.sql"))]


@pytest.fixture(scope="module")
def db():
    name = f"lorde_console_{uuid.uuid4().hex[:8]}"
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
        uid = c.execute("insert into auth.users (email, email_confirmed_at) values (%s, now()) returning id",
                        (email,)).fetchone()[0]  # orgs are created once the email is confirmed (0005)
        org = c.execute("select org_id from org_members where user_id = %s", (uid,)).fetchone()[0]
    return uid, org


def add_member(url, org, uid, role):
    with admin(url) as c:
        c.execute("insert into org_members (org_id, user_id, role) values (%s, %s, %s)", (org, uid, role))


def seed_org(url, org):
    """A finished job with results and judgments, a queued and a running job, a dataset and usage."""
    with admin(url) as c:
        did = uuid.uuid4()  # storage_path must be org_id/id/name (0005 datasets_storage_path_shape)
        c.execute("insert into datasets (id, org_id, storage_path, name) values (%s, %s, %s, 'd.jsonl')",
                  (did, org, f"{org}/{did}/d.jsonl"))
        done = c.execute(
            "insert into jobs (org_id, dataset_id, config, status, finished_at, results) values "
            "(%s, %s, '{}', 'succeeded', now(), %s) returning id",
            (org, did, json.dumps({"passed": f"{org}/j/passed.jsonl", "report": f"{org}/j/report.md"}))).fetchone()[0]
        queued = c.execute("insert into jobs (org_id, config) values (%s, '{}') returning id", (org,)).fetchone()[0]
        running = c.execute("insert into jobs (org_id, config, status, locked_by, heartbeat_at) values "
                            "(%s, '{}', 'running', 'w1', now()) returning id", (org,)).fetchone()[0]
        for i, (gate, score, dec) in enumerate([("reward", 0.9, True), ("reward", 0.2, False),
                                                 ("grounding", 0.75, True), ("grounding", None, None)]):
            c.execute("insert into judgments (job_id, row_idx, gate, score, decision, parsed) values "
                      "(%s, %s, %s, %s, %s, %s)", (done, i, gate, score, dec, json.dumps({"row_idx": i})))
        c.execute("insert into usage_events (org_id, judgments, prompt_tokens, completion_tokens) values "
                  "(%s, 3, 30, 3)", (org,))
    return did, done, queued, running


def test_only_the_owner_can_delete_org_data(db):
    owner, org = signup(db, "own@x.io")
    adm, _ = signup(db, "adm@x.io")
    mem, _ = signup(db, "mem@x.io")
    other_owner, _ = signup(db, "other@x.io")
    add_member(db, org, adm, "admin")
    add_member(db, org, mem, "member")
    seed_org(db, org)
    for uid in (adm, mem, other_owner):
        with admin(db) as c:
            as_user(c, uid)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute("select request_org_data_deletion(%s)", (org,))
    with admin(db) as c:
        c.execute("set role anon")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("select request_org_data_deletion(%s)", (org,))
    with admin(db) as c:
        assert c.execute("select count(*) from judgments j join jobs b on b.id = j.job_id where b.org_id=%s",
                         (org,)).fetchone()[0] == 4


def test_owner_deletion_removes_content_and_reaches_the_purge(db):
    owner, org = signup(db, "del@x.io")
    _, org_b = signup(db, "keep@x.io")
    did, done, queued, running = seed_org(db, org)
    seed_org(db, org_b)
    with admin(db) as c:
        c.execute("insert into human_labels (org_id, item_ref, kind, score, labeler) values (%s, 'judgment:1', 'reward', 1, %s)",
                  (org, owner))
        c.execute("insert into api_keys (org_id, name, prefix, key_hash) values (%s, 'k', 'lorde_sk_DELKEY01', 'h')", (org,))
    with admin(db) as c:
        as_user(c, owner)
        res = c.execute("select request_org_data_deletion(%s)", (org,)).fetchone()[0]
    assert res == {"judgments": 4, "human_labels": 1, "datasets": 1, "job_results": 1, "jobs_cancelled": 1,
                   "jobs_stopping": 1}
    with admin(db) as c:
        n = lambda sql, *a: c.execute(sql, a).fetchone()[0]  # noqa: E731
        assert n("select count(*) from judgments j join jobs b on b.id=j.job_id where b.org_id=%s", org) == 0
        assert n("select count(*) from human_labels where org_id=%s", org) == 0
        assert n("select count(*) from usage_events where org_id=%s", org) == 1      # billing data is kept
        assert n("select count(*) from api_keys where org_id=%s", org) == 1          # keys are kept
        assert n("select status from jobs where id=%s", queued) == "cancelled"
        assert n("select cancel_requested from jobs where id=%s", running) is True
        # the other org is untouched
        assert n("select count(*) from judgments j join jobs b on b.id=j.job_id where b.org_id=%s", org_b) == 4
        rows = c.execute("select kind, id, path from expired_objects() where org_id=%s", (org,)).fetchall()
        assert ("dataset", did, f"{org}/{did}/d.jsonl") in rows
        assert {r[2] for r in rows if r[0] == "result"} == {f"{org}/j/passed.jsonl", f"{org}/j/report.md"}
        assert c.execute("select count(*) from expired_objects() where org_id=%s", (org_b,)).fetchone()[0] == 0
        # what the worker does next: delete the files, then finish_purge removes rows and records it
        c.execute("select finish_purge(%s, %s)", ([did], [done]))
        assert n("select count(*) from datasets where id=%s", did) == 0
        assert n("select results from jobs where id=%s", done) == {}
        assert c.execute("select count(*) from expired_objects() where org_id=%s", (org,)).fetchone()[0] == 0
        audit = c.execute("select actor, action, detail from audit_log where org_id=%s and action like 'data.delete%%' "
                          "order by id", (org,)).fetchall()
        assert audit[0][:2] == (str(owner), "data.delete_request") and audit[0][2]["judgments"] == 4
        assert {a[1] for a in audit[1:]} == {"data.delete"}


def test_retention_purge_still_works_through_the_wrapper(db):
    _, org = signup(db, "ret@x.io")
    with admin(db) as c:
        did = uuid.uuid4()
        c.execute("insert into datasets (id, org_id, storage_path, name, expires_at) values "
                  "(%s, %s, %s, 'x.jsonl', now() - interval '1 day')", (did, org, f"{org}/{did}/x.jsonl"))
        rows = c.execute("select kind, id from expired_objects() where org_id=%s", (org,)).fetchall()
        assert rows == [("dataset", did)]
    with admin(db) as c:
        as_user(c, signup(db, "ret2@x.io")[0])
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("select * from expired_objects()")


def test_usage_daily_fills_days_and_isolates_orgs(db):
    a, org = signup(db, "use@x.io")
    b, _ = signup(db, "use2@x.io")
    today = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    with admin(db) as c:
        for d, j in [(0, 5), (0, 2), (3, 7), (20, 100)]:
            c.execute("insert into usage_events (org_id, judgments, prompt_tokens, completion_tokens, created_at) "
                      "values (%s, %s, 10, 1, %s)", (org, j, today - timedelta(days=d)))
    with admin(db) as c:
        as_user(c, a)
        rows = c.execute("select day, judgments, calls, prompt_tokens from usage_daily(%s, 14)", (org,)).fetchall()
    assert len(rows) == 14 and rows[-1][0] == today.date() and rows[0][0] == (today - timedelta(days=13)).date()
    by_day = {r[0]: r[1:] for r in rows}
    assert by_day[today.date()] == (7, 2, 20)
    assert by_day[(today - timedelta(days=3)).date()] == (7, 1, 10)
    assert sum(r[1] for r in rows) == 14                       # day 20 is outside the window
    with admin(db) as c:
        as_user(c, b)  # not a member: every day is zero
        assert c.execute("select coalesce(sum(judgments), 0) from usage_daily(%s, 14)", (org,)).fetchone()[0] == 0


def test_pass_rates_histogram_review_queue_and_members(db):
    a, org = signup(db, "rev@x.io")
    b, org_b = signup(db, "rev2@x.io")
    _, done, _, _ = seed_org(db, org)
    with admin(db) as c:
        as_user(c, a)
        rates = {r[0]: r[1:] for r in c.execute("select gate, judged, passed from gate_pass_rates(%s, 30)", (org,))}
        assert rates == {"reward": (2, 1), "grounding": (1, 1)}
        hist = c.execute("select gate, lang, bin, passed, rejected, undecided from job_judgment_stats(%s) "
                         "order by gate, bin nulls last", (done,)).fetchall()
        assert ("reward", "?", 9, 1, 0, 0) in hist and ("reward", "?", 2, 0, 1, 0) in hist
        assert ("grounding", "?", None, 0, 0, 1) in hist
        queue = c.execute("select id, gate, score from review_queue(%s, 50)", (org,)).fetchall()
        assert len(queue) == 4
        first = queue[0][0]
        c.execute("insert into human_labels (org_id, item_ref, kind, score, labeler) values (%s, %s, 'reward', 0.3, %s)",
                  (org, f"judgment:{first}", a))
        c.execute("insert into human_labels (org_id, item_ref, kind, score, labeler) values (%s, %s, 'reward', 0.8, %s)",
                  (org, f"judgment:{first}", a))
        assert first not in [r[0] for r in c.execute("select id from review_queue(%s, 50)", (org,))]
        labels = c.execute("select judgment_id, human_score, lorde_score from my_review_labels(%s)", (org,)).fetchall()
        assert labels == [(first, pytest.approx(0.8), pytest.approx(queue[0][2]))]   # the latest label wins
        members = c.execute("select email, role from org_members_with_email(%s)", (org,)).fetchall()
        assert members == [("rev@x.io", "owner")]
    with admin(db) as c:
        as_user(c, b)  # another org sees nothing
        assert c.execute("select count(*) from gate_pass_rates(%s, 30)", (org,)).fetchone()[0] == 0
        assert c.execute("select count(*) from job_judgment_stats(%s)", (done,)).fetchone()[0] == 0
        assert c.execute("select count(*) from review_queue(%s, 50)", (org,)).fetchone()[0] == 0
        assert c.execute("select count(*) from org_members_with_email(%s)", (org,)).fetchone()[0] == 0
    with admin(db) as c:
        c.execute("set role anon")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("select * from usage_daily(%s, 14)", (org,))
