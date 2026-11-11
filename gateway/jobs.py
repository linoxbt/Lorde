"""Uploads and gating jobs (spec Sections 11 and 11.4).

POST /v1/uploads            signed upload URL for a dataset (JSONL, CSV or Parquet, <= 200 MB)
POST /v1/jobs               create a gating job (<= 3 queued or running per org; 429 quota_exceeded once the
                            org's daily judgments are used up; the worker caps a job's rows to what is left)
GET  /v1/jobs/{id}          status, progress, counts
GET  /v1/jobs/{id}/results  signed URLs: passed.jsonl, rejected.jsonl, report.md, manifest, provenance
POST /v1/jobs/{id}/cancel   cancel a queued or running job
GET  /v1/usage              usage by day, key and endpoint
"""

from __future__ import annotations

import re
import uuid
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .db import job_view
from .errors import Conflict, InvalidRequest, LordeError, NotFound, PayloadTooLarge, RateLimited
from .keys import ApiKey

if TYPE_CHECKING:
    from .app import Gateway

NotConfigured = type("NotConfigured", (LordeError,), {"status": 503, "code": "not_configured"})
FORMATS = {".jsonl": "jsonl", ".json": "jsonl", ".csv": "csv", ".parquet": "parquet"}
HF_DATASET = re.compile(r"^hf://datasets/[\w.-]+/[\w.-]+(/[\w./-]+)?$")
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(..., min_length=1, max_length=200)
    bytes: int | None = Field(None, ge=1)


class Gates(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grounding: float | None = Field(0.7, ge=0, le=1)
    reward: float | None = Field(0.7, ge=0, le=1)

    @model_validator(mode="after")
    def one_gate(self):
        if self.grounding is None and self.reward is None:
            raise ValueError("enable at least one gate")
        return self


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pii: bool = False
    dedup: Literal["exact", "minhash", "none"] = "exact"


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upload_id: uuid.UUID | None = None
    dataset_url: str | None = Field(None, description="hf://datasets/<owner>/<name>[/<file>]")
    # defaults come from Field(); mypy runs without the pydantic plugin, hence the ignore
    gates: Gates = Gates()  # type: ignore[call-arg]
    options: Options = Options()

    @model_validator(mode="after")
    def one_source(self):
        if (self.upload_id is None) == (self.dataset_url is None):
            raise ValueError("give exactly one of upload_id or dataset_url")
        if self.dataset_url and not HF_DATASET.match(self.dataset_url):
            raise ValueError("dataset_url must look like hf://datasets/<owner>/<name>[/<file>]")
        return self


def safe_name(filename: str) -> str:
    name = PurePosixPath(filename.replace("\\", "/")).name
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:120]
    if not name or name.startswith("."):
        raise InvalidRequest("filename is not usable")
    return name


async def submit_job(gw: Gateway, key: ApiKey, req: JobRequest, idempotency_key: str | None = None) -> tuple[dict, bool]:
    """Create a job for ``key``'s org (REST and MCP share this). Returns (job row, created)."""
    if gw.jobs is None or gw.storage is None:
        raise NotConfigured("jobs need DATABASE_URL and storage (SUPABASE_URL + key, or LORDE_STORAGE_DIR)")
    jobs = gw.jobs
    if idempotency_key is not None:
        if not IDEMPOTENCY_KEY.match(idempotency_key):
            raise InvalidRequest("Idempotency-Key must be 8-128 characters of A-Z a-z 0-9 _ . : -")
        prior = await jobs.job_by_idempotency_key(key.org_id, idempotency_key)
        if prior:
            return prior, False
    if await jobs.active_jobs(key.org_id) >= gw.s.max_active_jobs:
        raise RateLimited(f"at most {gw.s.max_active_jobs} jobs may be queued or running per organization",
                          headers={"Retry-After": "30"})
    if (lim := gw.s.org_daily_judgments) > 0:  # a job cannot start on a used-up daily quota (audit N1b)
        used = max(await gw.quota.used(key.org_id), await jobs.judgments_today(key.org_id))
        if used >= lim:
            raise gw.quota_error(used)
    source: dict = {"dataset_url": req.dataset_url}
    dataset_id = None
    if req.upload_id:
        ds = await jobs.get_dataset(key.org_id, str(req.upload_id))
        if not ds:
            raise NotFound("no such upload in this organization")
        dataset_id, source = str(ds["id"]), {"storage_path": ds["storage_path"], "format": ds["format"]}
    config = {"gates": req.gates.model_dump(), "options": req.options.model_dump(), "source": source}
    job = await jobs.create_job(key.org_id, dataset_id, key.id, config, gw.s.model_label, actor=key.prefix,
                                idempotency_key=idempotency_key)
    return job, True


def build_router(gw: Gateway, auth) -> APIRouter:
    """``auth(scope)`` is the Gateway's key dependency: it runs before the request body is validated."""
    r = APIRouter()

    def need():
        if gw.jobs is None or gw.storage is None:
            raise NotConfigured("jobs need DATABASE_URL and storage (SUPABASE_URL + key, or LORDE_STORAGE_DIR)")
        return gw.jobs, gw.storage

    @r.post("/v1/uploads")
    async def create_upload(req: UploadRequest, key: ApiKey = Depends(auth("jobs:write"))):
        jobs, storage = need()
        name = safe_name(req.filename)
        fmt = FORMATS.get(PurePosixPath(name).suffix.lower())
        if not fmt:
            raise InvalidRequest("upload JSONL, CSV or Parquet (.jsonl, .json, .csv, .parquet)")
        if req.bytes and req.bytes > gw.s.max_upload_bytes:
            raise PayloadTooLarge(f"uploads are limited to {gw.s.max_upload_bytes // (1024 * 1024)} MB")
        ds = await jobs.create_dataset(key.org_id, name, fmt, req.bytes, None, actor=key.prefix)
        signed = await storage.signed_upload("datasets", ds["storage_path"])
        return {"upload_id": str(ds["id"]), "upload_url": signed["url"], "method": signed["method"],
                "headers": signed["headers"], "expires_in": signed["expires_in"], "format": fmt,
                "max_bytes": gw.s.max_upload_bytes}

    @r.post("/v1/jobs")
    async def create_job(request: Request, req: JobRequest, key: ApiKey = Depends(auth("jobs:write"))):
        """With an Idempotency-Key header, a retried request returns the job the first one created (200)."""
        job, created = await submit_job(gw, key, req, request.headers.get("idempotency-key"))
        return JSONResponse(job_view(job), status_code=201 if created else 200)

    async def load(key: ApiKey, job_id: str):
        jobs, _ = need()
        try:
            uuid.UUID(job_id)
        except ValueError as e:
            raise NotFound("no such job") from e
        job = await jobs.get_job(key.org_id, job_id)
        if not job:
            raise NotFound("no such job in this organization")
        return key, job

    @r.get("/v1/jobs/{job_id}")
    async def get_job(job_id: str, key: ApiKey = Depends(auth("jobs:read"))):
        _, job = await load(key, job_id)
        return job_view(job)

    @r.get("/v1/jobs/{job_id}/results")
    async def get_results(job_id: str, key: ApiKey = Depends(auth("jobs:read"))):
        _, job = await load(key, job_id)
        if job["status"] != "succeeded":
            raise Conflict(f"job is {job['status']}; results exist once it has succeeded")
        _, storage = need()
        urls = {name: await storage.signed_download("results", path) for name, path in (job["results"] or {}).items()}
        return {"id": job_id, "status": job["status"], "counts": job["counts"], "files": urls,
                "expires_in": 15 * 60}

    @r.post("/v1/jobs/{job_id}/cancel")
    async def cancel(job_id: str, key: ApiKey = Depends(auth("jobs:write"))):
        key, job = await load(key, job_id)
        jobs, _ = need()
        updated = await jobs.cancel_job(key.org_id, job_id, actor=key.prefix)
        if not updated:
            raise Conflict(f"job already {job['status']}")
        return job_view(updated)

    @r.get("/v1/usage")
    async def usage(days: int = 30, key: ApiKey = Depends(auth("usage:read"))):
        jobs, _ = need()
        async with jobs.pool.connection() as c:
            rows = await (await c.execute(
                "select date_trunc('day', created_at)::date as day, key_prefix, route, count(*) as calls, "
                "coalesce(sum(judgments),0) as judgments, coalesce(sum(prompt_tokens),0) as prompt_tokens, "
                "coalesce(sum(completion_tokens),0) as completion_tokens from usage_events "
                "where org_id = %s and created_at > now() - make_interval(days => %s) "
                "group by 1,2,3 order by 1 desc, 2, 3", (key.org_id, min(max(days, 1), 365)))).fetchall()
        return {"days": days, "rows": [{**x, "day": x["day"].isoformat()} for x in rows]}

    # Local storage only: token-signed upload/download, no API key (the token is the credential).
    @r.put("/v1/_storage/{token}")
    async def local_put(request: Request, token: str):
        from .storage import LocalStorage

        st = gw.storage
        if not isinstance(st, LocalStorage):
            raise NotFound("no such route")
        from .storage import TooLarge

        try:
            bucket, path = st.check(token, "put")
        except PermissionError as e:
            raise NotFound("invalid or expired upload URL") from e
        limit = gw.s.max_upload_bytes
        try:  # streamed to disk in chunks; the request body is never held in memory
            n = await st.write_stream(bucket, path, request.stream(), limit)
        except TooLarge as e:
            raise PayloadTooLarge(f"upload over {limit // (1024 * 1024)} MB") from e
        if gw.jobs:
            async with gw.jobs.pool.connection() as c:
                await c.execute("update datasets set bytes=%s where storage_path=%s", (n, path))
        return {"ok": True, "bytes": n}

    @r.get("/v1/_storage/{token}")
    async def local_get(token: str):
        from .storage import LocalStorage

        st = gw.storage
        if not isinstance(st, LocalStorage):
            raise NotFound("no such route")
        try:
            bucket, path = st.check(token, "get")
        except PermissionError as e:
            raise NotFound("invalid or expired download URL") from e
        return FileResponse(st._file(bucket, path), filename=PurePosixPath(path).name)

    return r
