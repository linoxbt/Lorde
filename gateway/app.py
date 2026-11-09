"""Lorde Gateway (FastAPI): ``uvicorn gateway.app:app``.

Routes (spec Section 11): POST /v1/judge/{reward,grounding,batch}, POST /v1/chat/completions (OpenAI-
compatible, for CuratorKIT and LiteLLM), GET /v1/models, /mcp (MCP Streamable HTTP), /healthz, /readyz,
/metrics. Every response carries x-request-id; judge responses also carry x-lorde-model and
x-lorde-template. Jobs and uploads arrive with the worker and Supabase in Phase 1/2.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import secrets
import time
from collections.abc import Mapping
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import generate_latest
from starlette.exceptions import HTTPException as StarletteHTTPException

from lorde_forge import contract

from . import API_MAJOR, __version__
from .errors import (
    Forbidden,
    InvalidRequest,
    LordeError,
    NotFound,
    PayloadTooLarge,
    RateLimited,
    Unauthenticated,
    body,
)
from .keys import ApiKey, KeyStore
from .limits import TokenBuckets
from .mcp_server import build as build_mcp
from .mcp_server import load_bench
from .metering import UsageSink
from .metrics import Metrics
from .models import BatchRequest, GroundingRequest, RewardRequest
from .service import JudgeService
from .settings import Settings
from .upstream import Upstream

log = logging.getLogger("lorde.gateway")
_RID = re.compile(r"^[A-Za-z0-9_.-]{6,64}$")


class Gateway:
    """Everything a request needs, shared by REST routes and MCP tools."""

    def __init__(self, s: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.s = s
        self.keys = KeyStore(s.keys_file, s.key_pepper)
        self.buckets = TokenBuckets()
        self.metrics = Metrics()
        self.usage = UsageSink(s.usage_file)
        self.upstream = Upstream(s, transport)
        self.service = JudgeService(s, self.upstream, self.metrics)
        self._ready: tuple[float, bool] = (0.0, False)

    def auth_headers(self, headers: Mapping[str, str], scope: str = "judge") -> ApiKey:
        auth = headers.get("authorization") or headers.get("Authorization") or ""
        if not auth.lower().startswith("bearer "):
            raise Unauthenticated("missing API key: send Authorization: Bearer lorde_sk_...")
        key = self.keys.verify(auth[7:].strip())
        if key is None:
            raise Unauthenticated("unknown or revoked API key")
        if scope not in key.scopes:
            raise Forbidden(f"this key lacks the '{scope}' scope")
        return key

    def rate_limit(self, key: ApiKey, cost: int = 1) -> None:
        wait = self.buckets.take(key.prefix, key.rpm, cost)
        if wait:
            self.metrics.rate_limited.inc()
            retry = "60" if wait == float("inf") else str(int(wait) + 1)
            raise RateLimited(f"rate limit of {key.rpm} judgments/minute exceeded", headers={"Retry-After": retry})

    def model_info(self) -> dict[str, Any]:
        return {"model": self.s.model_label, "repo": self.s.model_repo, "revision": self.s.model_revision,
                "api_version": API_MAJOR, "gateway_version": __version__,
                "bench": load_bench(_env_bench()), **contract.fingerprints()}

    async def ready(self) -> bool:
        now = time.monotonic()
        if now - self._ready[0] > 5:
            self._ready = (now, await self.upstream.ready())
        return self._ready[1]


def _env_bench() -> str | None:
    import os

    return os.environ.get("LORDE_BENCH_FILE")


class RequestContext:
    """Pure ASGI middleware: request ids, body-size limit, latency metrics. Safe for MCP streaming."""

    def __init__(self, app: Any, gw: Gateway) -> None:
        self.app, self.gw = app, gw

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        rid = headers.get("x-request-id", "")
        rid = rid if _RID.match(rid) else "req_" + secrets.token_hex(10)
        scope.setdefault("state", {})["request_id"] = rid
        route = scope["path"] if scope["path"].startswith(("/v1/judge", "/v1/chat", "/mcp")) else "other"
        t0 = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(msg):
            if msg["type"] == "http.response.start":
                status_holder["status"] = msg["status"]
                msg["headers"] = [*msg.get("headers", []), (b"x-request-id", rid.encode())]
            await send(msg)

        cl = headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > self.gw.s.max_body_bytes:
            err = PayloadTooLarge(f"request body over {self.gw.s.max_body_bytes // (1024 * 1024)} MB")
            resp = JSONResponse(body(err, rid), status_code=err.status)
            await resp(scope, receive, send_wrapper)
        else:
            await self.app(scope, receive, send_wrapper)
        self.gw.metrics.requests.labels(route, str(status_holder["status"])).inc()
        self.gw.metrics.latency.labels(route).observe(time.perf_counter() - t0)


class McpAuth:
    """Rejects /mcp requests without a valid key before they reach the MCP transport."""

    def __init__(self, app: Any, gw: Gateway) -> None:
        self.app, self.gw = app, gw

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            rid = scope.get("state", {}).get("request_id", "")
            if scope["path"].rstrip("/") != "/mcp":
                err = NotFound("no such route")
                return await JSONResponse(body(err, rid), status_code=404)(scope, receive, send)
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            try:
                self.gw.auth_headers(headers, scope="judge")
            except LordeError as e:
                resp = JSONResponse(body(e, rid), status_code=e.status, headers=e.headers)
                return await resp(scope, receive, send)
        return await self.app(scope, receive, send)


def create_app(s: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    s = s or Settings()
    s.check()
    gw = Gateway(s, transport)
    mcp = build_mcp(gw)
    from mcp.server.transport_security import TransportSecuritySettings

    security = (TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=s.mcp_allowed_hosts)
                if s.mcp_allowed_hosts else TransportSecuritySettings(enable_dns_rebinding_protection=False))
    mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True,
                                      transport_security=security)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        gw.usage.start()
        async with mcp.session_manager.run():
            yield
        await gw.usage.stop()
        await gw.upstream.close()

    app = FastAPI(title="Lorde Gateway", version=__version__, lifespan=lifespan,
                  description="A 3B judge for CuratorKIT's quality gates.")
    app.state.gw = gw
    if s.allowed_origins:
        app.add_middleware(CORSMiddleware, allow_origins=s.allowed_origins, allow_methods=["GET", "POST"],
                           allow_headers=["Authorization", "Content-Type", "X-Request-Id"])

    def rid(request: Request) -> str:
        return request.scope.get("state", {}).get("request_id", "")

    @app.exception_handler(LordeError)
    async def lorde_error(request: Request, e: LordeError):
        return JSONResponse(body(e, rid(request)), status_code=e.status, headers=e.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, e: RequestValidationError):
        errs = e.errors()
        loc = ".".join(str(x) for x in errs[0]["loc"][1:]) if errs else ""
        too_long = any(x.get("type") == "string_too_long" for x in errs)
        err = (PayloadTooLarge if too_long else InvalidRequest)(f"{loc}: {errs[0]['msg']}" if errs else "invalid")
        return JSONResponse(body(err, rid(request)), status_code=err.status)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, e: StarletteHTTPException):
        err = NotFound("no such route") if e.status_code == 404 else InvalidRequest(str(e.detail))
        if e.status_code not in (404,):
            err.status = e.status_code
        return JSONResponse(body(err, rid(request)), status_code=err.status)

    def auth(request: Request, scope: str = "judge") -> ApiKey:
        return gw.auth_headers(request.headers, scope)

    def headers(kind: str) -> dict[str, str]:
        return {"x-lorde-model": s.model_label, "x-lorde-template": contract.template_id(kind)}

    def meter(key: ApiKey, route: str, n: int, status: int, usage: dict | None = None) -> None:
        gw.usage.record(org_id=key.org_id, key_prefix=key.prefix, route=route, judgments=n, status=status,
                        model_revision=s.model_label, **(usage or {}))

    async def run_judge(request: Request, kind: str, req: RewardRequest | GroundingRequest):
        key = auth(request)
        gw.rate_limit(key)
        fields = req.model_dump(exclude={"threshold"})
        try:
            res, usage = await gw.service.judge(kind, fields, req.threshold, rid(request))
        except LordeError as e:
            meter(key, f"/v1/judge/{kind}", 0, e.status)
            raise
        meter(key, f"/v1/judge/{kind}", 1, 200, usage)
        return JSONResponse(res.model_dump(), headers=headers(kind))

    @app.post("/v1/judge/reward")
    async def judge_reward(request: Request, req: RewardRequest):
        return await run_judge(request, "reward", req)

    @app.post("/v1/judge/grounding")
    async def judge_grounding(request: Request, req: GroundingRequest):
        return await run_judge(request, "grounding", req)

    @app.post("/v1/judge/batch")
    async def judge_batch(request: Request, req: BatchRequest):
        key = auth(request)
        if len(req.items) > s.max_batch:
            raise PayloadTooLarge(f"batch of {len(req.items)} exceeds {s.max_batch} items")
        gw.rate_limit(key, len(req.items))
        res, usage = await gw.service.batch(req.kind, req.items, req.threshold, rid(request))
        meter(key, "/v1/judge/batch", res.summary["total"] - res.summary["errors"], 200, usage)
        return JSONResponse(res.model_dump(), headers=headers(req.kind))

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        key = auth(request)
        gw.rate_limit(key)
        try:
            payload = json.loads(await request.body())
        except json.JSONDecodeError as e:
            raise InvalidRequest("body is not JSON") from e
        if not isinstance(payload, dict):
            raise InvalidRequest("body must be a JSON object")
        resp, template = await gw.service.chat(payload)
        meter(key, "/v1/chat/completions", 1, 200, resp.get("usage") and {
            "prompt_tokens": resp["usage"].get("prompt_tokens", 0),
            "completion_tokens": resp["usage"].get("completion_tokens", 0)})
        resp["model"] = s.served_model
        return JSONResponse(resp, headers={"x-lorde-model": s.model_label, "x-lorde-template": template})

    @app.get("/v1/models")
    async def models(request: Request):
        auth(request)
        return {"object": "list", "data": [{"id": s.served_model, "object": "model", "owned_by": "lorde",
                                            "revision": s.model_label}]}

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "version": __version__, "api_version": API_MAJOR}

    @app.get("/readyz")
    async def readyz():
        up = await gw.ready()
        ok = up and not gw.upstream.breaker.is_open
        return JSONResponse({"status": "ready" if ok else "not_ready", "serve": up,
                             "breaker_open": gw.upstream.breaker.is_open}, status_code=200 if ok else 503)

    @app.get("/metrics")
    async def metrics(request: Request):
        if s.metrics_token and request.headers.get("authorization") != f"Bearer {s.metrics_token}":
            raise NotFound("no such route")
        return PlainTextResponse(generate_latest(gw.metrics.registry).decode(),
                                 media_type="text/plain; version=0.0.4")

    app.mount("/", McpAuth(mcp_app, gw))  # after every route: only /mcp reaches the MCP transport
    return RequestContext(app, gw)  # type: ignore[return-value]


def __getattr__(name: str):  # lazy module-level app for `uvicorn gateway.app:app`
    if name == "app":
        return create_app()
    raise AttributeError(name)
