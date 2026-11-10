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
        body = {"kind": kind, "items": _items(kind, items), "threshold": threshold}
        return BatchResult.model_validate(self._c._post("/v1/judge/batch", body))


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

    def _request(self, method: str, path: str, json: dict | None = None) -> dict[str, Any]:
        if not self._checked:
            self._checked = True
            _check_version(self.health())
        payload = {k: v for k, v in (json or {}).items() if v is not None} if json else None
        last: httpx.Response | None = None
        for attempt in range(self.max_retries + 1):
            try:
                r = self._http.request(method, path, json=payload, headers=self._headers)
            except httpx.TransportError:
                if attempt == self.max_retries:
                    raise
                time.sleep(_delay(attempt, None))
                continue
            if r.status_code < 400:
                return r.json()
            err = _error(r)
            if not err.retryable or attempt == self.max_retries:
                raise err
            last = r
            time.sleep(_delay(attempt, last))
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
        return BatchResult.model_validate(await self._c._post(
            "/v1/judge/batch", {"kind": kind, "items": _items(kind, items), "threshold": threshold}))


class AsyncLorde:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, *, timeout: float = 60.0,
                 max_retries: int = 4, http_client: httpx.AsyncClient | None = None) -> None:
        self.api_key, self.base_url = _resolve(api_key, base_url)
        self.max_retries = max_retries
        self._http = http_client or httpx.AsyncClient(base_url=self.base_url, timeout=timeout)
        self._headers = {"Authorization": f"Bearer {self.api_key}", "User-Agent": "lorde-python/0.1.0"}
        self._checked = False
        self.judge = _AsyncJudge(self)

    async def _post(self, path: str, body: dict) -> dict[str, Any]:
        if not self._checked:
            self._checked = True
            r = await self._http.get("/healthz")
            _check_version(r.json() if r.status_code == 200 else {})
        payload = {k: v for k, v in body.items() if v is not None}
        for attempt in range(self.max_retries + 1):
            try:
                r = await self._http.post(path, json=payload, headers=self._headers)
            except httpx.TransportError:
                if attempt == self.max_retries:
                    raise
                await asyncio.sleep(_delay(attempt, None))
                continue
            if r.status_code < 400:
                return r.json()
            err = _error(r)
            if not err.retryable or attempt == self.max_retries:
                raise err
            await asyncio.sleep(_delay(attempt, r))
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> AsyncLorde:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
