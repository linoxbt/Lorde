"""Lorde Gateway (FastAPI): ``uvicorn gateway.app:app``.

Routes (spec Section 11): POST /v1/judge/{reward,grounding,batch}, POST /v1/chat/completions (OpenAI-
compatible, for CuratorKIT and LiteLLM), GET /v1/models, /mcp (MCP Streamable HTTP), the jobs API
(gateway/jobs.py), /healthz, /readyz, /metrics. Every response carries x-request-id; judge responses also
carry x-lorde-model and x-lorde-template.

Request order (RequestContext): request id -> per-IP token bucket for /v1/* and /mcp (LORDE_IP_RPM; before any
key lookup) -> API key check for /v1/* and /mcp (malformed keys never reach the database; revoked keys are 403) -> body size limit while streaming (5 MB; the signed storage route
/v1/_storage/* allows the 200 MB upload limit and streams to disk) -> route -> one JSON access log line.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import re
import secrets
import time
from collections.abc import Mapping
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import generate_latest
from starlette.exceptions import HTTPException as StarletteHTTPException

from lorde_forge import contract

from . import API_MAJOR, __version__
from . import logs as lorde_logs
from .errors import (
    REFUNDED,
    Forbidden,
    InternalError,
    InvalidRequest,
    LordeError,
    NotFound,
    PayloadTooLarge,
    QuotaExceeded,
    RateLimited,
    Unauthenticated,
    Unavailable,
    body,
    refundable,
)
from .keys import KEY_FORMAT, ApiKey, KeyStore
from .limits import make_buckets
from .mcp_server import build as build_mcp
from .mcp_server import load_bench
from .metering import UsageSink
from .metrics import Metrics
from .models import BatchRequest, GroundingRequest, RewardRequest
from .quota import make_quota, seconds_to_midnight
from .service import JudgeService
from .settings import Settings
from .upstream import Upstream

log = logging.getLogger("lorde.gateway")
access_log = logging.getLogger("lorde.access")
_RID = re.compile(r"^[A-Za-z0-9_.-]{6,64}$")
STORAGE_PREFIX = "/v1/_storage/"


def _ip(v: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(v.strip().strip("[]"))
    except ValueError:
        return None


def _db_error(e: BaseException) -> bool:
    try:
        import psycopg
        import psycopg_pool
    except ImportError:  # pragma: no cover
        return False
    return isinstance(e, psycopg.OperationalError | psycopg.InterfaceError | psycopg_pool.PoolTimeout)


class Gateway:
    """Everything a request needs, shared by REST routes and MCP tools."""

    def __init__(self, s: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        from .storage import from_settings

        self.s = s
        self.pool: Any = None
        self.jobs: Any = None
        self.keys: Any
        self.usage: Any
        if s.database_url:
            from .db import JobStore, PgKeyStore, PgUsageSink, make_pool

            self.pool = make_pool(s.database_url)
            self.keys = PgKeyStore(self.pool, s.key_pepper, s.key_cache_size)
            self.usage = PgUsageSink(self.pool, s.usage_queue_max)
            self.jobs = JobStore(self.pool)
        else:
            self.keys = KeyStore(s.keys_file, s.key_pepper, s.key_cache_size)
            self.usage = UsageSink(s.usage_file)
        self.storage = from_settings(s)
        self.buckets = make_buckets(s.redis_url)
        self.quota = make_quota(s.redis_url)
        self._quota_seeded: dict[str, float] = {}  # org -> monotonic time of the last reconcile with usage_events
        self.trust_all = "*" in s.trusted_proxies
        self.trusted = [ipaddress.ip_network(x, strict=False) for x in s.trusted_proxies if x != "*"]
        self.metrics = Metrics()
        self.upstream = Upstream(s, transport)
        self.service = JudgeService(s, self.upstream, self.metrics)
        self._ready: tuple[float, dict[str, Any]] = (0.0, {})
        self._model_task: asyncio.Task | None = None

    async def resolve_key(self, headers: Mapping[str, str]) -> ApiKey:
        """The caller's key, or 401 (missing, malformed, unknown) / 403 (revoked) / 503 (key store down)."""
        auth = headers.get("authorization") or headers.get("Authorization") or ""
        if not auth[:7].lower() == "bearer ":
            raise Unauthenticated("missing API key: send Authorization: Bearer lorde_sk_...")
        token = auth[7:].strip()
        if not KEY_FORMAT.match(token):
            raise Unauthenticated("malformed API key")
        try:
            key = await self.keys.alookup(token)
        except Exception as e:
            if _db_error(e):
                raise Unavailable("the key store is unreachable; retry shortly", headers={"Retry-After": "5"}) from e
            raise
        if key is None:
            raise Unauthenticated("unknown API key")
        if key.revoked_at:
            raise Forbidden("this API key has been revoked")
        return key

    async def authenticate(self, headers: Mapping[str, str], scope: str | None = "judge",
                           state: Mapping[str, Any] | None = None) -> ApiKey:
        key = (state or {}).get("api_key") or await self.resolve_key(headers)
        if scope and scope not in key.scopes:
            raise Forbidden(f"this key lacks the '{scope}' scope")
        return key

    async def rate_limit(self, key: ApiKey, cost: int = 1) -> None:
        if cost > key.rpm:  # can never succeed, so not a 429: the request itself is too big for this key
            raise PayloadTooLarge(f"{cost} judgments exceed this key's limit of {key.rpm} judgments/minute; "
                                  "send smaller batches")
        wait = await self.buckets.atake(key.prefix, key.rpm, cost)
        if wait:
            self.metrics.rate_limited.inc()
            retry = "60" if wait == float("inf") else str(int(wait) + 1)
            raise RateLimited(f"rate limit of {key.rpm} judgments/minute exceeded", headers={"Retry-After": retry})

    # ------------------------------------------------------------------ per-IP limit (audit N2)
    def client_ip(self, scope: Mapping[str, Any], headers: Mapping[str, str]) -> str:
        """The address to limit: the Console relay's forwarded user address (with the relay secret), X-Forwarded-
        For's first hop when the peer is a trusted proxy, else the peer. IPv6 is grouped per /64."""
        peer_s = (scope.get("client") or ("unknown", 0))[0] or "unknown"
        ip = _ip(peer_s)
        relay = self.s.relay_secret
        if relay and secrets.compare_digest(headers.get("x-lorde-relay-secret", "").encode(), relay.encode()):
            ip = _ip(headers.get("x-lorde-client-ip", "")) or ip
        elif ip is not None and (self.trust_all or any(ip in n for n in self.trusted)):
            ip = _ip(headers.get("x-forwarded-for", "").split(",")[0]) or ip
        if ip is None:
            return peer_s[:64]
        if isinstance(ip, ipaddress.IPv6Address):
            if ip.ipv4_mapped:
                return str(ip.ipv4_mapped)
            if not ip.is_loopback:
                return str(ipaddress.ip_network(f"{ip}/64", strict=False))
        return str(ip)

    async def limit_ip(self, scope: Mapping[str, Any], headers: Mapping[str, str]) -> None:
        if self.s.ip_rpm <= 0:
            return
        ip = self.client_ip(scope, headers)
        wait = await self.buckets.atake(f"ip:{ip}", self.s.ip_rpm)
        if wait:
            self.metrics.rate_limited.inc()
            retry = "60" if wait == float("inf") else str(int(wait) + 1)
            raise RateLimited(f"too many requests from this address (limit {self.s.ip_rpm}/minute)",
                              headers={"Retry-After": retry})

    # ------------------------------------------------------------------ daily quota (audit N1b)
    def quota_error(self, used: int) -> LordeError:
        lim = self.s.org_daily_judgments
        return QuotaExceeded(f"this organization's daily quota of {lim} judgments is used up ({used} today); "
                             "it resets at 00:00 UTC", headers={"Retry-After": str(seconds_to_midnight())},
                             extra={"limit": lim, "used": used})

    async def spend(self, key: ApiKey, n: int = 1) -> None:
        """Spend ``n`` of the org's judgments for today, or 429 quota_exceeded (nothing is spent then)."""
        lim = self.s.org_daily_judgments
        if lim <= 0:
            return
        if n > lim:
            raise PayloadTooLarge(f"{n} judgments exceed this organization's daily quota of {lim}")
        await self._seed_quota(key.org_id)
        ok, used = await self.quota.take(key.org_id, n, lim)
        if not ok:
            self.metrics.rate_limited.inc()
            raise self.quota_error(used)

    async def _seed_quota(self, org_id: str) -> None:
        """Every 60 s per org, raise the counter to what usage_events metered today (atomically: concurrent first
        requests cannot add the difference twice), so a Gateway restart, a Redis flush or metered job work is
        reflected. Best effort: a database error leaves the counter as it is."""
        if self.jobs is None:
            return
        now = time.monotonic()
        last = self._quota_seeded.get(org_id)
        if last is not None and now - last < 60:
            return
        if len(self._quota_seeded) > 50_000:
            self._quota_seeded = {k: t for k, t in self._quota_seeded.items() if now - t < 60}
        self._quota_seeded[org_id] = now
        try:
            metered = await self.jobs.judgments_today(org_id)
        except Exception:  # noqa: BLE001  (the database is down: fail open, try again next minute)
            return
        await self.quota.raise_to(org_id, metered)

    async def refund(self, key: ApiKey, n: int) -> None:
        if n > 0 and self.s.org_daily_judgments > 0:
            await self.quota.add(key.org_id, -n)

    async def quota_left(self, org_id: str) -> int | None:
        """Judgments the org may still make today (None: unlimited)."""
        lim = self.s.org_daily_judgments
        return None if lim <= 0 else max(0, lim - await self.quota.used(org_id))

    def model_info(self) -> dict[str, Any]:
        return {"model": self.s.model_label, "repo": self.s.model_repo, "revision": self.s.model_revision,
                "api_version": API_MAJOR, "gateway_version": __version__,
                "bench": load_bench(_env_bench()), **contract.fingerprints()}

    # ------------------------------------------------------------------ model registry (spec FR-12)
    async def load_active_model(self) -> bool:
        """Adopt model_versions' active row (state='active') as the served revision; env stays the fallback."""
        if self.pool is None or not self.s.model_registry:
            return False
        try:
            async with self.pool.connection() as c:
                r: Any = await (await c.execute("select hf_repo, revision from model_versions where state = 'active' "
                                           "order by promoted_at desc nulls last limit 1")).fetchone()
        except Exception:  # noqa: BLE001
            log.warning("could not read model_versions; keeping %s", self.s.model_label, exc_info=True)
            return False
        if not r:
            return False
        repo, rev = r["hf_repo"] or self.s.model_repo, r["revision"]
        if (repo, rev) != (self.s.model_repo, self.s.model_revision):
            log.info("active model from registry: %s@%s", repo, rev)
            self.s.model_repo, self.s.model_revision = repo, rev
        return True

    def start_model_refresh(self, every_s: float = 60.0) -> None:
        if self.pool is None or not self.s.model_registry:
            return

        async def loop():
            while True:
                await asyncio.sleep(every_s)
                await self.load_active_model()

        self._model_task = asyncio.create_task(loop())

    # ------------------------------------------------------------------ readiness (DB, Redis, Serve)
    async def ready(self) -> dict[str, Any]:
        now = time.monotonic()
        if now - self._ready[0] <= 5:
            return self._ready[1]

        async def db() -> bool | None:
            if self.pool is None:
                return None
            try:
                async with asyncio.timeout(3):
                    async with self.pool.connection(timeout=3) as c:
                        await c.execute("select 1")
                return True
            except Exception:  # noqa: BLE001
                return False

        async def redis() -> bool | None:
            if not self.s.redis_url:
                return None
            try:
                async with asyncio.timeout(3):
                    return await self.buckets.ping()
            except Exception:  # noqa: BLE001
                return False

        serve, d, r = await asyncio.gather(self.upstream.ready(), db(), redis())
        checks = {"serve": serve, "database": d, "redis": r, "breaker_open": self.upstream.breaker.is_open}
        checks["ok"] = bool(serve) and d is not False and r is not False and not checks["breaker_open"]
        self._ready = (now, checks)
        return checks


def _env_bench() -> str | None:
    import os

    return os.environ.get("LORDE_BENCH_FILE")


class _Disconnected(Exception):
    pass


class RequestContext:
    """Pure ASGI middleware (safe for MCP streaming): request ids, early key check, streaming body limit,
    a catch-all error envelope, latency metrics and one JSON access log line per request."""

    def __init__(self, app: Any, gw: Gateway) -> None:
        self.app, self.gw = app, gw

    @staticmethod
    def needs_key(method: str, path: str) -> bool:
        if method == "OPTIONS":
            return False
        return (path.startswith("/v1/") and not path.startswith(STORAGE_PREFIX)) or path.rstrip("/") == "/mcp"

    @staticmethod
    async def _buffer(receive, limit: int):
        """Read the whole body (at most ``limit`` bytes, however it is framed), then replay it."""
        chunks, total = [], 0
        while True:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                raise _Disconnected
            if msg["type"] == "http.request":
                chunk = msg.get("body", b"")
                total += len(chunk)
                if total > limit:
                    raise PayloadTooLarge(f"request body over {limit // (1024 * 1024)} MB")
                chunks.append(chunk)
                if not msg.get("more_body"):
                    break
        data, sent = b"".join(chunks), False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": data, "more_body": False}
            return await receive()

        return replay

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        s = self.gw.s
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        rid = headers.get("x-request-id", "")
        rid = rid if _RID.match(rid) else "req_" + secrets.token_hex(10)
        state = scope.setdefault("state", {})
        state["request_id"] = rid
        path, method = scope["path"], scope["method"]
        route = path if path.startswith(("/v1/judge", "/v1/chat", "/mcp")) else "other"
        t0 = time.perf_counter()
        status_holder = {"status": 500, "started": False}

        async def send_wrapper(msg):
            if msg["type"] == "http.response.start":
                status_holder["status"], status_holder["started"] = msg["status"], True
                msg["headers"] = [*msg.get("headers", []), (b"x-request-id", rid.encode())]
            await send(msg)

        async def reply(err: LordeError) -> None:
            if not status_holder["started"]:
                resp = JSONResponse(body(err, rid), status_code=err.status, headers=err.headers)
                await resp(scope, receive, send_wrapper)

        try:
            if self.needs_key(method, path):
                await self.gw.limit_ip(scope, headers)  # before any key lookup: a prefix flood stops here
                key = await self.gw.resolve_key(headers)
                state["api_key"] = key
            storage = path.startswith(STORAGE_PREFIX)
            limit = s.max_upload_bytes if storage else s.max_body_bytes
            cl = headers.get("content-length")
            if cl and cl.isdigit() and int(cl) > limit:
                raise PayloadTooLarge(f"request body over {limit // (1024 * 1024)} MB")
            if not storage and method in ("POST", "PUT", "PATCH", "DELETE"):
                receive = await self._buffer(receive, limit)
            await self.app(scope, receive, send_wrapper)
        except _Disconnected:
            status_holder["status"] = 499
        except LordeError as e:
            await reply(e)
        except Exception as e:  # noqa: BLE001  (the error envelope, whatever failed)
            if _db_error(e):
                await reply(Unavailable("a backing service is unreachable; retry shortly",
                                        headers={"Retry-After": "5"}))
            else:
                if not status_holder["started"]:
                    log.exception("unhandled error", extra={"fields": {"request_id": rid}})
                await reply(InternalError("internal error; quote the request_id if you report it"))
        finally:
            dt = time.perf_counter() - t0
            status = str(status_holder["status"])
            self.gw.metrics.requests.labels(route, status).inc()
            self.gw.metrics.latency.labels(route).observe(dt)
            if s.access_log:
                key = state.get("api_key")
                tmpl = getattr(scope.get("route"), "path", None)
                access_log.info("request", extra={"fields": {
                    "method": method, "route": tmpl or ("/v1/_storage/{token}" if path.startswith(STORAGE_PREFIX)
                                                        else route if route != "other" else path[:100]),
                    "status": int(status), "duration_ms": round(dt * 1000, 1), "request_id": rid,
                    "org_id": key.org_id if key else None, "key_prefix": key.prefix if key else None}})


class McpAuth:
    """Only /mcp reaches the MCP transport (the key was checked by RequestContext; tools check scopes)."""

    def __init__(self, app: Any, gw: Gateway) -> None:
        self.app, self.gw = app, gw

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            rid = scope.get("state", {}).get("request_id", "")
            if scope["path"].rstrip("/") != "/mcp":
                err = NotFound("no such route")
                return await JSONResponse(body(err, rid), status_code=404)(scope, receive, send)
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
            try:
                await self.gw.authenticate(headers, scope=None, state=scope.get("state"))
            except LordeError as e:
                resp = JSONResponse(body(e, rid), status_code=e.status, headers=e.headers)
                return await resp(scope, receive, send)
        return await self.app(scope, receive, send)


def create_app(s: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    s = s or Settings()
    s.check()
    lorde_logs.setup()
    gw = Gateway(s, transport)
    mcp = build_mcp(gw)
    from mcp.server.transport_security import TransportSecuritySettings

    security = (TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=s.mcp_allowed_hosts,
                                          allowed_origins=s.allowed_origins)
                if s.mcp_allowed_hosts else TransportSecuritySettings(enable_dns_rebinding_protection=False))
    mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True,
                                      transport_security=security)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        if gw.pool is not None:
            await gw.pool.open()
            await gw.load_active_model()
            gw.start_model_refresh()
        gw.usage.start()
        async with mcp.session_manager.run():
            yield
        if gw._model_task:
            gw._model_task.cancel()
        await gw.usage.stop()
        await gw.upstream.close()
        await gw.quota.close()
        if gw.pool is not None:
            await gw.pool.close()

    docs: dict[str, Any] = {} if s.public_docs else {"docs_url": None, "redoc_url": None, "openapi_url": None}
    app = FastAPI(title="Lorde Gateway", version=__version__, lifespan=lifespan,
                  description="A 3B judge for CuratorKIT's quality gates.", **docs)
    app.state.gw = gw

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

    @app.exception_handler(Exception)
    async def unhandled(request: Request, e: Exception):
        if _db_error(e):
            err: LordeError = Unavailable("a backing service is unreachable; retry shortly",
                                          headers={"Retry-After": "5"})
        else:
            log.exception("unhandled error", exc_info=e, extra={"fields": {"request_id": rid(request)}})
            err = InternalError("internal error; quote the request_id if you report it")
        return JSONResponse(body(err, rid(request)), status_code=err.status, headers=err.headers)

    def need(scope: str | None):
        async def dep(request: Request) -> ApiKey:  # runs before the body is validated
            return await gw.authenticate(request.headers, scope, request.scope.get("state"))
        return dep

    def headers(kind: str) -> dict[str, str]:
        return {"x-lorde-model": s.model_label, "x-lorde-template": contract.template_id(kind)}

    def meter(key: ApiKey, route: str, n: int, status: int, t0: float, usage: dict | None = None) -> None:
        gw.usage.record(org_id=key.org_id, api_key_id=key.id, key_prefix=key.prefix, route=route, judgments=n,
                        status=status, model_revision=s.model_label,
                        latency_ms=int((time.perf_counter() - t0) * 1000), **(usage or {}))

    async def run_judge(request: Request, key: ApiKey, kind: str, req: RewardRequest | GroundingRequest):
        t0 = time.perf_counter()
        await gw.rate_limit(key)
        await gw.spend(key)
        fields = req.model_dump(exclude={"threshold"})
        try:
            res, usage = await gw.service.judge(kind, fields, req.threshold, rid(request), key.org_id)
        except LordeError as e:
            if refundable(e):
                await gw.refund(key, 1)
            meter(key, f"/v1/judge/{kind}", 0, e.status, t0)
            raise
        meter(key, f"/v1/judge/{kind}", 1, 200, t0, usage)
        return JSONResponse(res.model_dump(), headers=headers(kind))

    @app.post("/v1/judge/reward")
    async def judge_reward(request: Request, req: RewardRequest, key: ApiKey = Depends(need("judge"))):
        return await run_judge(request, key, "reward", req)

    @app.post("/v1/judge/grounding")
    async def judge_grounding(request: Request, req: GroundingRequest, key: ApiKey = Depends(need("judge"))):
        return await run_judge(request, key, "grounding", req)

    @app.post("/v1/judge/batch")
    async def judge_batch(request: Request, req: BatchRequest, key: ApiKey = Depends(need("judge"))):
        t0 = time.perf_counter()
        if len(req.items) > s.max_batch:
            raise PayloadTooLarge(f"batch of {len(req.items)} exceeds {s.max_batch} items; send several batches "
                                  "or start a gating job (POST /v1/jobs)")
        await gw.rate_limit(key, len(req.items))
        await gw.spend(key, len(req.items))
        try:
            res, usage = await gw.service.batch(req.kind, req.items, req.threshold, rid(request), key.org_id,
                                                disconnected=request.is_disconnected)
        except LordeError as e:
            if refundable(e):
                await gw.refund(key, len(req.items))
            raise
        await gw.refund(key, sum(1 for x in res.results if x.error and x.error.get("code") in REFUNDED))
        meter(key, "/v1/judge/batch", res.summary["total"] - res.summary["errors"], 200, t0, usage)
        return JSONResponse(res.model_dump(), headers=headers(req.kind))

    @app.post("/v1/chat/completions")
    async def chat(request: Request, key: ApiKey = Depends(need("judge"))):
        t0 = time.perf_counter()
        try:
            payload = json.loads(await request.body())
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise InvalidRequest("body is not JSON") from e
        if not isinstance(payload, dict):
            raise InvalidRequest("body must be a JSON object")
        gw.service.sanitize_chat(payload)  # 400 before spending a rate-limit token
        await gw.rate_limit(key)
        await gw.spend(key)
        try:
            resp, template = await gw.service.chat(payload, key.org_id)
        except LordeError as e:
            if refundable(e):
                await gw.refund(key, 1)
            raise
        meter(key, "/v1/chat/completions", 1, 200, t0, resp.get("usage") and {
            "prompt_tokens": resp["usage"].get("prompt_tokens", 0),
            "completion_tokens": resp["usage"].get("completion_tokens", 0)})
        resp["model"] = s.served_model
        return JSONResponse(resp, headers={"x-lorde-model": s.model_label, "x-lorde-template": template})

    @app.get("/v1/models")
    async def models(key: ApiKey = Depends(need(None))):  # any valid key
        return {"object": "list", "data": [{"id": s.served_model, "object": "model", "owned_by": "lorde",
                                            "revision": s.model_label}]}

    @app.get("/v1/models/{model_id}")
    async def model(model_id: str, key: ApiKey = Depends(need(None))):
        if model_id != s.served_model:
            raise NotFound(f"no model '{model_id}'; this Gateway serves '{s.served_model}'")
        return {"id": s.served_model, "object": "model", "owned_by": "lorde", **gw.model_info()}

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "version": __version__, "api_version": API_MAJOR}

    @app.get("/readyz")
    async def readyz():
        c = await gw.ready()
        return JSONResponse({"status": "ready" if c["ok"] else "not_ready",
                             **{k: v for k, v in c.items() if k != "ok"}}, status_code=200 if c["ok"] else 503)

    @app.get("/metrics")
    async def metrics(request: Request):
        if s.metrics_token and not secrets.compare_digest(request.headers.get("authorization", ""),
                                                          f"Bearer {s.metrics_token}"):
            raise NotFound("no such route")
        return PlainTextResponse(generate_latest(gw.metrics.registry).decode(),
                                 media_type="text/plain; version=0.0.4")

    from .jobs import build_router

    app.include_router(build_router(gw, need))
    app.mount("/", McpAuth(mcp_app, gw))  # after every route: only /mcp reaches the MCP transport
    outer: Any = RequestContext(app, gw)
    if s.allowed_origins:  # outermost, so errors raised by RequestContext (401, 413) carry CORS headers too
        outer = CORSMiddleware(outer, allow_origins=s.allowed_origins, allow_methods=["GET", "POST", "PUT", "OPTIONS"],
                               allow_headers=["Authorization", "Content-Type", "X-Request-Id", "Idempotency-Key"],
                               expose_headers=["x-request-id", "retry-after", "x-lorde-model", "x-lorde-template"],
                               max_age=600)
    outer.gw = gw
    return outer  # type: ignore[no-any-return]


def __getattr__(name: str):  # lazy module-level app for `uvicorn gateway.app:app`
    if name == "app":
        return create_app()
    raise AttributeError(name)
