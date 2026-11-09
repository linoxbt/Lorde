"""The judge service behind the REST routes and the MCP tools.

Prompts are rendered by CuratorKIT's gate classes and replies parsed by CuratorKIT's parser
(``lorde_forge.contract``), so native and OpenAI-compatible calls are byte-identical and a
decision means exactly what CuratorKIT would decide. Structured outputs are always on here.
"""

from __future__ import annotations

import asyncio
import time
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
from .upstream import Upstream


class JudgeService:
    def __init__(self, s: Settings, upstream: Upstream, metrics: Metrics) -> None:
        self.s, self.up, self.m = s, upstream, metrics

    # ------------------------------------------------------------------ native judging
    async def judge(self, kind: str, fields: dict[str, Any], threshold: float | None,
                    request_id: str) -> tuple[RewardResult | GroundingResult, dict[str, int]]:
        thr = self.s.default_threshold if threshold is None else threshold
        item = {"kind": kind, **fields}
        body = {"model": self.s.served_model, "messages": contract.messages(item),
                "temperature": JUDGE_TEMPERATURE, "max_tokens": JUDGE_MAX_TOKENS,
                "response_format": response_format(kind)}
        t0 = time.perf_counter()
        resp = await self.up.chat(body)
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
        meta = {"decision": decision, "threshold": thr, "model": self.s.model_label,
                "template": contract.template_id(kind), "latency_ms": latency_ms, "request_id": request_id}
        # the decision uses CuratorKIT's own score; canonical() only normalizes the other fields
        if kind == "reward":
            result: RewardResult | GroundingResult = RewardResult(**{**target, "overall_score": j.score}, **meta)
        else:
            result = GroundingResult(**{**target, "grounding_score": j.score}, **meta)
        return result, {"prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0)}

    async def batch(self, kind: str, items: list[dict], threshold: float | None,
                    request_id: str) -> tuple[BatchResult, dict[str, int]]:
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
        sem = asyncio.Semaphore(self.s.upstream_concurrency)
        tokens = {"prompt_tokens": 0, "completion_tokens": 0}

        async def one(i: int, it: Any) -> BatchItemResult:
            fields = it.model_dump(exclude={"id"})
            async with sem:
                try:
                    res, usage = await self.judge(kind, fields, threshold, f"{request_id}.{i}")
                    for k in tokens:
                        tokens[k] += usage[k]
                    return BatchItemResult(index=i, id=it.id, result=res)
                except LordeError as e:
                    return BatchItemResult(index=i, id=it.id, error={"code": e.code, "message": e.message})

        results = await asyncio.gather(*(one(i, it) for i, it in enumerate(parsed)))
        summary = {"passed": sum(r.result is not None and r.result.decision == "pass" for r in results),
                   "rejected": sum(r.result is not None and r.result.decision == "reject" for r in results),
                   "errors": sum(r.error is not None for r in results), "total": len(results)}
        return BatchResult(kind=kind, results=list(results), summary=summary, model=self.s.model_label,
                           template=contract.template_id(kind), request_id=request_id), tokens

    # --------------------------------------------------------- OpenAI-compatible proxy
    async def chat(self, body: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """Pass-through for CuratorKIT/LiteLLM. Judge-shaped prompts get the gate's schema.

        Returns (upstream response, template id or "unknown").
        """
        if body.get("stream"):
            raise InvalidRequest("streaming is not supported; send stream=false")
        msgs = body.get("messages")
        if not isinstance(msgs, list) or not msgs:
            raise InvalidRequest("messages must be a non-empty list")
        out = dict(body)
        out["model"] = self.s.served_model
        temp = out.get("temperature")
        out["temperature"] = min(float(temp), self.s.max_temperature) if temp is not None else JUDGE_TEMPERATURE
        last = msgs[-1].get("content") if isinstance(msgs[-1], dict) else None
        match = contract.match_template(last) if isinstance(last, str) else None
        if match:
            kind = match[0]
            template = contract.template_id(kind)
            if "response_format" not in out and "structured_outputs" not in (out.get("extra_body") or {}):
                out["response_format"] = response_format(kind)
            self.m.template.labels("known").inc()
        else:
            template = "unknown"
            self.m.template.labels("unknown").inc()
        return await self.up.chat(out), template
