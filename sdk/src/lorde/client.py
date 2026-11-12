"""Sync and async clients for the Lorde Gateway."""

from __future__ import annotations

import asyncio
import os
import random
import time
from typing import Any, Literal

import httpx

from .errors import IncompatibleGateway, LordeAPIError
from .models import BatchResult, GroundingResult, RewardResult

DEFAULT_BASE_URL = "http://localhost:8080"
API_MAJOR = 1


def _resolve(api_key: str | None, base_url: str | None) -> tuple[str, str]:
    key = api_key or os.environ.get("LORDE_API_KEY")
    if not key:
        raise ValueError("pass api_key= or set LORDE_API_KEY")
    return key, (base_url or os.environ.get("LORDE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def _error(r: httpx.Response) -> LordeAPIError:
    try:
        b = r.json()
        e = b.get("error", {})
        return LordeAPIError(r.status_code, e.get("code", "error"), e.get("message", r.text[:200]),
                             e.get("request_id") or r.headers.get("x-request-id"), b)
    except ValueError:
        return LordeAPIError(r.status_code, "error", r.text[:200], r.headers.get("x-request-id"))


# Transport errors where the request never reached the Gateway: always safe to retry.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


def _retry_transport(e: httpx.TransportError, method: str, idempotent: bool) -> bool:
    """POSTs are retried only if they cannot have been processed (or carry an Idempotency-Key): a read
    timeout on a judge call may mean the judgment ran and was billed."""
    return isinstance(e, _NOT_SENT) or method == "GET" or idempotent


def _retry_status(r: httpx.Response, method: str, idempotent: bool) -> bool:
    if r.status_code == 429:
        # refused before any work was done; but a used-up daily quota only resets at 00:00 UTC
        return _error_code(r) != "quota_exceeded"
    if r.status_code == 503:
        return method == "GET" or idempotent or "retry-after" in r.headers
    return r.status_code in (502, 504) and (method == "GET" or idempotent)


def _error_code(r: httpx.Response) -> str | None:
    try:
        return (r.json().get("error") or {}).get("code")
    except Exception:  # noqa: BLE001 - non-JSON error bodies just have no code
        return None


def _delay(attempt: int, r: httpx.Response | None) -> float:
    if r is not None and r.headers.get("retry-after", "").isdigit():
        return min(float(r.headers["retry-after"]), 60.0)
    return min(0.5 * 2 ** attempt, 20.0) * random.uniform(0.5, 1.5)


def _check_version(h: dict[str, Any]) -> None:
    v = h.get("api_version")
    if v is not None and int(v) != API_MAJOR:
        raise IncompatibleGateway(0, "incompatible_gateway",
                                  f"Gateway API v{v} is not supported by this SDK (v{API_MAJOR}); upgrade lorde")


def _items(kind: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(i) for i in items]


BATCH_CHUNK = 8  # the Gateway's default LORDE_MAX_BATCH: larger batches are sent as several requests


def _chunks(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    return [items[i:i + BATCH_CHUNK] for i in range(0, len(items), BATCH_CHUNK)] or [[]]


def _merge(parts: list[dict[str, Any]]) -> BatchResult:
    """One BatchResult from consecutive chunk responses (indices continue across chunks, summaries add up)."""
    results: list[dict[str, Any]] = []
    summary: dict[str, int] = {}
    offset = 0
    for p in parts:
        for r in p["results"]:
            results.append({**r, "index": r["index"] + offset})
        offset += len(p["results"])
        for k, v in p["summary"].items():
            summary[k] = summary.get(k, 0) + v
    last = parts[-1]
    return BatchResult.model_validate({**last, "results": results, "summary": summary,
                                       "request_id": ",".join(p["request_id"] for p in parts)})


class _Judge:
    def __init__(self, c: Lorde) -> None:
        self._c = c

    def reward(self, instruction: str, response: str, threshold: float | None = None) -> RewardResult:
        body = {"instruction": instruction, "response": response, "threshold": threshold}
        return RewardResult.model_validate(self._c._post("/v1/judge/reward", body))

    def grounding(self, source_text: str, question: str, answer: str,
                  threshold: float | None = None) -> GroundingResult:
        body = {"source_text": source_text, "question": question, "answer": answer, "threshold": threshold}
        return GroundingResult.model_validate(self._c._post("/v1/judge/grounding", body))

    def batch(self, kind: Literal["reward", "grounding"], items: list[dict[str, Any]],
              threshold: float | None = None) -> BatchResult:
        return _merge([self._c._post("/v1/judge/batch", {"kind": kind, "items": chunk, "threshold": threshold})
                       for chunk in _chunks(_items(kind, items))])


class Lorde:
    """``Lorde(api_key=..., base_url=...)``; defaults to LORDE_API_KEY and LORDE_BASE_URL."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None, *, timeout: float = 60.0,
                 max_retries: int = 4, http_client: httpx.Client | None = None) -> None:
        self.api_key, self.base_url = _resolve(api_key, base_url)
        self.max_retries = max_retries
        self._http = http_client or httpx.Client(base_url=self.base_url, timeout=timeout)
        self._headers = {"Authorization": f"Bearer {self.api_key}", "User-Agent": "lorde-python/0.1.0"}
        self._checked = False
        self.judge = _Judge(self)
        from .jobs import Jobs

        self.jobs = Jobs(self)

    def _storage(self, method: str, url: str, **kw: Any) -> httpx.Response:
        from .jobs import storage_request

        return storage_request(self._http, self.base_url, method, url, **kw)

    def _request(self, method: str, path: str, json: dict | None = None,
                 headers: dict[str, str] | None = None) -> dict[str, Any]:
        if not self._checked:
            self._checked = True
            _check_version(self.health())
        payload = {k: v for k, v in (json or {}).items() if v is not None} if json else None
        hdrs = {**self._headers, **(headers or {})}
        idempotent = "Idempotency-Key" in hdrs
        for attempt in range(self.max_retries + 1):
            try:
                r = self._http.request(method, path, json=payload, headers=hdrs)
            except httpx.TransportError as e:
                if attempt == self.max_retries or not _retry_transport(e, method, idempotent):
                    raise
                time.sleep(_delay(attempt, None))
                continue
            if r.status_code < 400:
                return r.json()
            err = _error(r)
            if attempt == self.max_retries or not _retry_status(r, method, idempotent):
                raise err
            time.sleep(_delay(attempt, r))
        raise AssertionError("unreachable")

    def _post(self, path: str, body: dict) -> dict[str, Any]:
        return self._request("POST", path, body)

    def health(self) -> dict[str, Any]:
        r = self._http.get("/healthz")
        return r.json() if r.status_code == 200 else {}

    def models(self) -> dict[str, Any]:
        return self._request("GET", "/v1/models")

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Lorde:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class _AsyncJudge:
    def __init__(self, c: AsyncLorde) -> None:
        self._c = c

    async def reward(self, instruction: str, response: str, threshold: float | None = None) -> RewardResult:
        return RewardResult.model_validate(await self._c._post(
            "/v1/judge/reward", {"instruction": instruction, "response": response, "threshold": threshold}))

    async def grounding(self, source_text: str, question: str, answer: str,
                        threshold: float | None = None) -> GroundingResult:
        return GroundingResult.model_validate(await self._c._post(
            "/v1/judge/grounding",
            {"source_text": source_text, "question": question, "answer": answer, "threshold": threshold}))

    async def batch(self, kind: Literal["reward", "grounding"], items: list[dict[str, Any]],
                    threshold: float | None = None) -> BatchResult:
        return _merge([await self._c._post("/v1/judge/batch", {"kind": kind, "items": chunk, "threshold": threshold})
                       for chunk in _chunks(_items(kind, items))])


class AsyncLorde:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, *, timeout: float = 60.0,
                 max_retries: int = 4, http_client: httpx.AsyncClient | None = None) -> None:
        self.api_key, self.base_url = _resolve(api_key, base_url)
        self.max_retries = max_retries
        self._http = http_client or httpx.AsyncClient(base_url=self.base_url, timeout=timeout)
        self._headers = {"Authorization": f"Bearer {self.api_key}", "User-Agent": "lorde-python/0.1.0"}
        self._checked = False
        self.judge = _AsyncJudge(self)

    async def _request(self, method: str, path: str, json: dict | None = None,
                       headers: dict[str, str] | None = None) -> dict[str, Any]:
        if not self._checked:
            self._checked = True
            r = await self._http.get("/healthz")
            _check_version(r.json() if r.status_code == 200 else {})
        payload = {k: v for k, v in (json or {}).items() if v is not None} if json else None
        hdrs = {**self._headers, **(headers or {})}
        idempotent = "Idempotency-Key" in hdrs
        for attempt in range(self.max_retries + 1):
            try:
                r = await self._http.request(method, path, json=payload, headers=hdrs)
            except httpx.TransportError as e:
                if attempt == self.max_retries or not _retry_transport(e, method, idempotent):
                    raise
                await asyncio.sleep(_delay(attempt, None))
                continue
            if r.status_code < 400:
                return r.json()
            err = _error(r)
            if attempt == self.max_retries or not _retry_status(r, method, idempotent):
                raise err
            await asyncio.sleep(_delay(attempt, r))
        raise AssertionError("unreachable")

    async def _post(self, path: str, body: dict) -> dict[str, Any]:
        return await self._request("POST", path, body)

    async def model_info(self) -> dict[str, Any]:
        """The active model revision, its Lorde Bench scores and the template fingerprints."""
        models = await self._request("GET", "/v1/models")
        served = models["data"][0]["id"]
        return await self._request("GET", f"/v1/models/{served}")

    async def create_job(self, *, upload_id: str | None = None, dataset_url: str | None = None,
                         gates: dict[str, float | None] | None = None) -> dict[str, Any]:
        """POST /v1/jobs with an Idempotency-Key, so a retried request never starts a second job."""
        import uuid

        body: dict[str, Any] = {"upload_id": upload_id, "dataset_url": dataset_url,
                                "gates": gates or {"grounding": 0.7, "reward": 0.7}}
        return await self._request("POST", "/v1/jobs", body, headers={"Idempotency-Key": f"sdk-{uuid.uuid4()}"})

    async def job_status(self, job_id: str) -> dict[str, Any]:
        job = await self._request("GET", f"/v1/jobs/{job_id}")
        if job.get("status") == "succeeded":
            job["files"] = (await self._request("GET", f"/v1/jobs/{job_id}/results"))["files"]
        return job

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> AsyncLorde:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
