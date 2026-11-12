"""The judge service behind the REST routes and the MCP tools.

Prompts are rendered by CuratorKIT's gate classes and replies parsed by CuratorKIT's parser
(``lorde_forge.contract``), so native and OpenAI-compatible calls are byte-identical and a
decision means exactly what CuratorKIT would decide. Structured outputs are always on here.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError

from lorde_forge import canonical, contract
from lorde_forge.config import JUDGE_MAX_TOKENS, JUDGE_TEMPERATURE
from lorde_forge.schemas import response_format

from .errors import InvalidRequest, JudgeUnparseable, LordeError
from .metrics import Metrics
from .models import (
    BatchItemResult,
    BatchResult,
    GroundingItem,
    GroundingResult,
    RewardItem,
    RewardResult,
)
from .settings import Settings
from .slots import Busy
from .upstream import Upstream

MAX_SCHEMA_BYTES = 16 * 1024   # response_format / structured_outputs / guided_json, serialized (audit N10)
MAX_SCHEMA_DEPTH = 12


def _depth(v: Any) -> int:
    """Nesting depth of a JSON value (iterative, so a hostile document cannot exhaust the stack)."""
    deepest, stack = 0, [(v, 1)]
    while stack:
        x, d = stack.pop()
        if isinstance(x, dict | list):
            deepest = max(deepest, d)
            if d > MAX_SCHEMA_DEPTH:
                return d
            stack.extend((y, d + 1) for y in (x.values() if isinstance(x, dict) else x))
    return deepest


def check_schema(name: str, v: Any) -> None:
    """Structured-output schemas are compiled into a grammar by Serve: keep them small (400 otherwise)."""
    import json

    if isinstance(v, str):
        size = len(v.encode())
        if size <= MAX_SCHEMA_BYTES:
            try:
                v = json.loads(v)
            except ValueError:
                v = None  # a grammar or regex string: the size limit is all that applies
    else:
        size = len(json.dumps(v, separators=(",", ":")).encode())
    if size > MAX_SCHEMA_BYTES:
        raise InvalidRequest(f"{name} is limited to {MAX_SCHEMA_BYTES // 1024} KB of JSON")
    if _depth(v) > MAX_SCHEMA_DEPTH:
        raise InvalidRequest(f"{name} is nested more than {MAX_SCHEMA_DEPTH} levels deep")


class JudgeService:
    def __init__(self, s: Settings, upstream: Upstream, metrics: Metrics) -> None:
        self.s, self.up, self.m = s, upstream, metrics

    # ------------------------------------------------------------------ native judging
    async def judge(self, kind: str, fields: dict[str, Any], threshold: float | None, request_id: str,
                    org_id: str | None = None) -> tuple[RewardResult | GroundingResult, dict[str, int]]:
        thr = self.s.default_threshold if threshold is None else threshold
        item = {"kind": kind, **fields}
        body = {"model": self.s.served_model, "messages": contract.messages(item),
                "temperature": JUDGE_TEMPERATURE, "max_tokens": JUDGE_MAX_TOKENS,
                "response_format": response_format(kind)}
        t0 = time.perf_counter()
        resp = await self.up.chat(body, org_id)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        raw = resp["choices"][0]["message"].get("content") or ""
        usage = resp.get("usage") or {}
        j = contract.parse_reply(kind, raw)
        target = canonical.canonical(j) if j.ok else None
        if target is None:
            self.m.unparseable.labels(kind).inc()
            raise JudgeUnparseable("the model reply failed the judge contract", extra={"raw": raw[:2000]})
        decision = "pass" if j.decision(thr) else "reject"
        self.m.judgments.labels(kind, decision).inc()
        meta: dict[str, Any] = {"decision": decision, "threshold": thr, "model": self.s.model_label,
                "template": contract.template_id(kind), "latency_ms": latency_ms, "request_id": request_id}
        # the decision uses CuratorKIT's own score; canonical() only normalizes the other fields
        if kind == "reward":
            result: RewardResult | GroundingResult = RewardResult(**{**target, "overall_score": j.score}, **meta)
        else:
            result = GroundingResult(**{**target, "grounding_score": j.score}, **meta)
        return result, {"prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0)}

    async def batch(self, kind: str, items: list[dict], threshold: float | None, request_id: str,
                    org_id: str | None = None,
                    disconnected: Callable[[], Awaitable[bool]] | None = None) -> tuple[BatchResult, dict[str, int]]:
        """Judge every item; an item's failure is reported in its slot. Items run at most
        min(API slots, org slots) at a time, so a batch never queues against its own org's slots. Once an item
        is refused as busy, the items not yet started are refused too (no point queueing them); when nothing was
        judged at all, the whole batch is 429 rate_limited. ``disconnected`` is checked before each item: once the
        client has gone, the remaining items are not started (error ``client_disconnected``, refunded)."""
        if len(items) > self.s.max_batch:
            from .errors import PayloadTooLarge

            raise PayloadTooLarge(f"batch of {len(items)} exceeds {self.s.max_batch} items")
        model = RewardItem if kind == "reward" else GroundingItem
        parsed: list[Any] = []
        for i, it in enumerate(items):
            try:
                parsed.append(model.model_validate(it))
            except ValidationError as e:
                raise InvalidRequest(f"items[{i}]: {e.errors()[0]['msg']}") from e
        width = self.s.api_upstream_slots
        if org_id is not None:
            width = min(width, self.s.org_upstream_concurrency)
        sem = asyncio.Semaphore(max(1, width))
        tokens = {"prompt_tokens": 0, "completion_tokens": 0}
        busy: list[Busy] = []
        gone: list[bool] = []

        async def one(i: int, it: Any) -> BatchItemResult:
            fields = it.model_dump(exclude={"id"})
            async with sem:
                if gone or (disconnected is not None and await disconnected()):
                    gone.append(True)
                    return BatchItemResult(index=i, id=it.id, error={
                        "code": "client_disconnected", "message": "not judged: the client disconnected"})
                try:
                    if busy:
                        raise busy[0]
                    res, usage = await self.judge(kind, fields, threshold, f"{request_id}.{i}", org_id)
                    for k in tokens:
                        tokens[k] += usage[k]
                    return BatchItemResult(index=i, id=it.id, result=res)
                except Busy as e:
                    busy.append(e)
                    return BatchItemResult(index=i, id=it.id, error={"code": e.code, "message": e.message})
                except LordeError as e:
                    return BatchItemResult(index=i, id=it.id, error={"code": e.code, "message": e.message})

        results = await asyncio.gather(*(one(i, it) for i, it in enumerate(parsed)))
        if busy and all(r.result is None and r.error and r.error["code"] == busy[0].code for r in results):
            raise busy[0]
        summary = {"passed": sum(r.result is not None and r.result.decision == "pass" for r in results),
                   "rejected": sum(r.result is not None and r.result.decision == "reject" for r in results),
                   "errors": sum(r.error is not None for r in results), "total": len(results)}
        return BatchResult(kind=kind, results=list(results), summary=summary, model=self.s.model_label,
                           template=contract.template_id(kind), request_id=request_id), tokens

    # --------------------------------------------------------- OpenAI-compatible proxy
    def sanitize_chat(self, body: dict[str, Any]) -> dict[str, Any]:
        """Allow-list and clamp an OpenAI chat request so the proxy stays a judge, not a general LLM.

        Kept: messages, temperature (finite, clamped to [0, 0.3]), top_p, max_tokens (<= 512; also read from
        max_completion_tokens), stop, seed, response_format and vLLM's structured_outputs / guided_json (each at
        most 16 KB of JSON nested at most 12 levels: Serve compiles them into a grammar).
        n must be 1 and stream false. Every other field (logit_bias, tools, llama.cpp sampler knobs, ...) is
        dropped. Wrong types are 400 invalid_request.
        """
        if body.get("stream") not in (None, False):
            raise InvalidRequest("streaming is not supported; send stream=false")
        if body.get("n") not in (None, 1):
            raise InvalidRequest("n must be 1")
        msgs = body.get("messages")
        if not isinstance(msgs, list) or not msgs:
            raise InvalidRequest("messages must be a non-empty list")
        chars = 0
        clean_msgs = []
        for i, m in enumerate(msgs):
            if not isinstance(m, dict) or m.get("role") not in ("system", "user", "assistant"):
                raise InvalidRequest(f"messages[{i}] must be an object with role system, user or assistant")
            content = m.get("content")
            if isinstance(content, list):  # OpenAI content parts: text only
                if not all(isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str)
                           for p in content):
                    raise InvalidRequest(f"messages[{i}].content parts must be text")
                content = "".join(p["text"] for p in content)
            if not isinstance(content, str):
                raise InvalidRequest(f"messages[{i}].content must be a string")
            chars += len(content)
            clean_msgs.append({"role": m["role"], "content": content})
        if chars > self.s.max_chat_chars:
            from .errors import PayloadTooLarge

            raise PayloadTooLarge(f"messages are limited to {self.s.max_chat_chars} characters in total")

        def number(name: str, default: float | None, lo: float, hi: float) -> float | None:
            v = body.get(name)
            if v is None:
                return default
            if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
                raise InvalidRequest(f"{name} must be a finite number")
            return min(max(float(v), lo), hi)

        out: dict[str, Any] = {"model": self.s.served_model, "messages": clean_msgs,
                               "temperature": number("temperature", JUDGE_TEMPERATURE, 0.0, self.s.max_temperature)}
        top_p = number("top_p", None, 0.0, 1.0)
        if top_p is not None:
            out["top_p"] = top_p if top_p > 0 else 1.0
        mt = body.get("max_tokens", body.get("max_completion_tokens"))
        if mt is not None and (isinstance(mt, bool) or not isinstance(mt, int) or mt < 1):
            raise InvalidRequest("max_tokens must be a positive integer")
        out["max_tokens"] = min(mt or self.s.max_chat_tokens, self.s.max_chat_tokens)
        stop = body.get("stop")
        if stop is not None:
            if isinstance(stop, str):
                stop = [stop]
            if not (isinstance(stop, list) and len(stop) <= 4 and all(isinstance(x, str) and len(x) <= 64
                                                                        for x in stop)):
                raise InvalidRequest("stop must be a string or up to 4 strings")
            out["stop"] = stop
        seed = body.get("seed")
        if seed is not None:
            if isinstance(seed, bool) or not isinstance(seed, int):
                raise InvalidRequest("seed must be an integer")
            out["seed"] = seed
        for k in ("response_format", "structured_outputs", "guided_json"):
            if body.get(k) is not None:
                if not isinstance(body[k], dict | str):
                    raise InvalidRequest(f"{k} must be an object")
                check_schema(k, body[k])
                out[k] = body[k]
        return out

    async def chat(self, body: dict[str, Any], org_id: str | None = None) -> tuple[dict[str, Any], str]:
        """Pass-through for CuratorKIT/LiteLLM. Judge-shaped prompts get the gate's schema.

        Returns (upstream response, template id or "unknown").
        """
        out = self.sanitize_chat(body)
        last = out["messages"][-1]["content"]
        match = contract.match_template(last)
        if match:
            kind = match[0]
            template = contract.template_id(kind)
            if not any(k in out for k in ("response_format", "structured_outputs", "guided_json")):
                out["response_format"] = response_format(kind)
            self.m.template.labels("known").inc()
        else:
            template = "unknown"
            self.m.template.labels("unknown").inc()
        return await self.up.chat(out, org_id), template
