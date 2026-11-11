"""Jobs end to end on real Postgres: upload -> job -> worker (CuratorKIT, Lorde judge) -> results.

Needs LORDE_TEST_PG_URL (see test_supabase_sql.py). The judge is a stub vLLM that wraps its JSON in
prose unless a schema is requested, so a clean run proves the worker asks for structured outputs.
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
from stub_openai import serve  # noqa: E402
from test_supabase_sql import FILES  # noqa: E402

from gateway.app import create_app  # noqa: E402
from gateway.keys import KEY_PREFIX, _b62, hash_key, prefix_of  # noqa: E402
from gateway.settings import Settings  # noqa: E402
from worker.main import Worker  # noqa: E402

URL = os.environ.get("LORDE_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set LORDE_TEST_PG_URL to run Postgres tests")
PEPPER = "j" * 40
SRC = ("The Niger River is the main river of West Africa, about 4,180 km long. Its drainage basin covers "
       "2,117,700 square kilometres and it flows through Mali, Niger and Nigeria.")
ROWS = [{"instruction": "How long is the Niger River?", "input": SRC,
         "output": "The Niger River is the main river of West Africa, about 4,180 km long."},
        {"instruction": "How long is the Niger River?", "input": SRC, "output": "It is about 6,650 km long."},
        {"instruction": "Which countries does it flow through?", "input": SRC,
         "output": "The Niger River is the main river of West Africa and flows through Mali, Niger and Nigeria."}]


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    name = f"lorde_jobs_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(f"create database {name}")
    db = URL.rsplit("/", 1)[0] + "/" + name
    with psycopg.connect(db, autocommit=True) as c:
        for f in FILES:
            c.execute(f.read_text())
        uid = c.execute("insert into auth.users (email, email_confirmed_at) values ('ops@x.io', now()) returning id").fetchone()[0]
        org = c.execute("select org_id from org_members where user_id=%s", (uid,)).fetchone()[0]
        keys = {}
        for label, scopes in {"all": ["judge", "jobs:read", "jobs:write", "usage:read"], "judge": ["judge"]}.items():
            k = KEY_PREFIX + _b62()
            c.execute("insert into api_keys (org_id, name, prefix, key_hash, scopes) values (%s,%s,%s,%s,%s)",
                      (org, label, prefix_of(k), hash_key(k, PEPPER), scopes))
            keys[label] = {"Authorization": f"Bearer {k}"}
    tmp = tmp_path_factory.mktemp("jobs")
    vllm, vurl = serve("chatty")
    slow, surl = serve("slow")
    s = Settings(key_pepper=PEPPER, database_url=db, storage_dir=str(tmp / "storage"), vllm_base_url=vurl,
                 model_revision="v1.0.0")
    yield {"db": db, "org": org, "keys": keys, "s": s, "tmp": tmp, "slow_url": surl}
    vllm.shutdown()
    slow.shutdown()
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(f"drop database {name} with (force)")


def upload(c, h, rows, filename="data.jsonl"):
    r = c.post("/v1/uploads", headers=h, json={"filename": filename})
    assert r.status_code == 200, r.text
    u = r.json()
    body = "\n".join(json.dumps(x) for x in rows).encode()
    assert c.put(u["upload_url"], content=body).json()["bytes"] == len(body)
    return u["upload_id"]


def work(env, **kw) -> Worker:
    w = Worker(env["s"], worker_id="test-worker", work_dir=str(env["tmp"] / "work"), poll_s=0.1, **kw)
    return w


def run_one(w: Worker) -> bool:
    async def go():
        await w.connect()
        try:
            return await w.process_one()
        finally:
            await w.conn.close()
    return asyncio.run(go())


def clear_jobs(env):
    with psycopg.connect(env["db"], autocommit=True) as c:
        c.execute("update jobs set status='cancelled' where status in ('queued','running')")


def test_upload_job_worker_results(env):
    h = env["keys"]["all"]
    with TestClient(create_app(env["s"])) as c:
        up = upload(c, h, ROWS)
        r = c.post("/v1/jobs", headers=h, json={"upload_id": up, "gates": {"grounding": 0.7, "reward": 0.7}})
        assert r.status_code == 201 and r.json()["status"] == "queued"
        jid = r.json()["id"]
        assert c.get(f"/v1/jobs/{jid}/results", headers=h).status_code == 409   # not finished yet

        assert run_one(work(env)) is True

        j = c.get(f"/v1/jobs/{jid}", headers=h).json()
        assert j["status"] == "succeeded", j
        assert j["counts"]["judge_errors"] == 0          # structured outputs per gate, despite a chatty model
        assert j["counts"]["passed"] == 2 and j["counts"]["rejected"] == 1 and j["counts"]["rows_in"] == 3
        res = c.get(f"/v1/jobs/{jid}/results", headers=h).json()
        assert set(res["files"]) >= {"passed", "rejected", "report", "lexsi_provenance"}
        passed = [json.loads(x) for x in c.get(res["files"]["passed"]).text.splitlines()]
        rejected = [json.loads(x) for x in c.get(res["files"]["rejected"]).text.splitlines()]
        assert sorted(p["row"] for p in passed) == [0, 2] and rejected[0]["row"] == 1
        assert rejected[0]["rejection_reason"].startswith("hallucination") and rejected[0]["verdict"] == "hallucinated"
        assert "pass rate" in c.get(res["files"]["report"]).text
        usage = c.get("/v1/usage", headers=h).json()
    with psycopg.connect(env["db"]) as c:
        n, has_text = c.execute("select count(*), bool_or(parsed is not null) from judgments where job_id=%s",
                                (jid,)).fetchone()
        assert n >= 3 and not has_text                   # scores only: no judged content in the database
        actions = [r[0] for r in c.execute("select action from audit_log where target=%s order by id", (jid,))]
        assert actions == ["job.create", "job.succeeded"]
    assert "rows" in usage


def test_scopes_limits_and_cancel(env):
    clear_jobs(env)
    with TestClient(create_app(env["s"])) as c:
        assert c.post("/v1/uploads", headers=env["keys"]["judge"], json={"filename": "x.jsonl"}).status_code == 403
        h = env["keys"]["all"]
        assert c.post("/v1/uploads", headers=h, json={"filename": "x.exe"}).status_code == 400
        assert c.post("/v1/jobs", headers=h, json={"dataset_url": "https://evil/x"}).status_code == 400
        assert c.post("/v1/jobs", headers=h, json={"upload_id": str(uuid.uuid4())}).status_code == 404
        up = upload(c, h, ROWS[:1], "../../etc/passwd.jsonl")
        ids = [c.post("/v1/jobs", headers=h, json={"upload_id": up}).json()["id"] for _ in range(3)]
        r = c.post("/v1/jobs", headers=h, json={"upload_id": up})
        assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
        assert c.post(f"/v1/jobs/{ids[0]}/cancel", headers=h).json()["status"] == "cancelled"
        assert c.post(f"/v1/jobs/{ids[0]}/cancel", headers=h).status_code == 409
        assert c.get(f"/v1/jobs/{uuid.uuid4()}", headers=h).status_code == 404
        assert c.get("/v1/jobs/not-a-uuid", headers=h).status_code == 404
    with psycopg.connect(env["db"]) as c:
        path = c.execute("select storage_path from datasets where id=%s", (up,)).fetchone()[0]
    assert ".." not in path and path.endswith("/passwd.jsonl")


def test_cancel_running_job_stops_worker(env):
    clear_jobs(env)
    s = Settings(**{**env["s"].__dict__, "vllm_base_url": env["slow_url"]})  # every judge call takes 1 s
    with TestClient(create_app(s)) as c:
        h = env["keys"]["all"]
        up = upload(c, h, ROWS * 4)
        jid = c.post("/v1/jobs", headers=h, json={"upload_id": up}).json()["id"]
        w = Worker(s, worker_id="slow-worker", work_dir=str(env["tmp"] / "work2"), heartbeat_s=0.5)
        t = threading.Thread(target=run_one, args=(w,))
        t.start()
        for _ in range(100):
            if c.get(f"/v1/jobs/{jid}", headers=h).json()["status"] == "running":
                break
            time.sleep(0.1)
        time.sleep(1.0)
        assert c.post(f"/v1/jobs/{jid}/cancel", headers=h).json()["cancel_requested"] is True
        t.join(60)
        assert not t.is_alive()
        assert c.get(f"/v1/jobs/{jid}", headers=h).json()["status"] == "cancelled"


def test_mcp_job_tools(env):
    clear_jobs(env)
    from test_gateway import mcp_call

    h = env["keys"]["all"]
    with TestClient(create_app(env["s"])) as c:
        up = upload(c, h, ROWS[:1])
        r = mcp_call(c, h, "tools/call", {"name": "lorde_create_gating_job",
                                          "arguments": {"upload_id": up, "reward": None}}).json()
        out = r["result"].get("structuredContent") or json.loads(r["result"]["content"][0]["text"])
        assert out["status"] == "queued"
        r = mcp_call(c, h, "tools/call", {"name": "lorde_job_status", "arguments": {"job_id": out["job_id"]}},
                     i=2).json()
        st = r["result"].get("structuredContent") or json.loads(r["result"]["content"][0]["text"])
        assert st["config"]["gates"] == {"grounding": 0.7, "reward": None}


def test_sdk_and_cli_jobs(env, tmp_path, monkeypatch, capsys):
    clear_jobs(env)
    from lorde import Lorde
    from lorde.cli import main as cli

    key = env["keys"]["all"]["Authorization"][7:]
    data = tmp_path / "d.jsonl"
    data.write_text("\n".join(json.dumps(r) for r in ROWS))
    with TestClient(create_app(env["s"])) as c:
        sdk = Lorde(api_key=key, base_url="http://testserver", http_client=c)
        job = sdk.jobs.create(data, gates={"grounding": 0.7, "reward": None})
        assert job.status == "queued"
        assert run_one(work(env)) is True
        job.wait(poll_s=0.1, timeout_s=10)
        assert job.status == "succeeded" and job.counts["passed"] == 2
        files = job.download(tmp_path / "out")
        assert len(files["passed"].read_text().splitlines()) == 2 and "Rejection reasons" in files["report"].read_text()
        assert sdk.jobs.get(job.id).cancel  # handle round-trips
        # CLI against the same app (TestClient) by patching the SDK's default client
        import lorde.client as lc

        monkeypatch.setattr(lc.httpx, "Client", lambda **kw: c)
        monkeypatch.setenv("LORDE_API_KEY", key)
        monkeypatch.setenv("LORDE_BASE_URL", "http://testserver")
        assert cli(["jobs", "status", job.id]) == 0
        assert json.loads(capsys.readouterr().out)["status"] == "succeeded"
