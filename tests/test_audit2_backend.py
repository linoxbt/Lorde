# ruff: noqa: F811  (the module-scoped `env` fixture is imported from test_jobs and requested by name)
"""Regressions for the second audit's backend, database and security findings (12 Nov 2026).

The Postgres tests need LORDE_TEST_PG_URL (see test_supabase_sql.py).
"""

import os
import threading
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")
from fastapi.testclient import TestClient  # noqa: E402
from test_jobs import ROWS, clear_jobs, env, upload  # noqa: E402,F401

from gateway.app import create_app  # noqa: E402

URL = os.environ.get("LORDE_TEST_PG_URL")
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
