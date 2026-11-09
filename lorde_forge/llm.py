"""Async OpenAI-compatible client for vLLM servers and the Cohere Compatibility API."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from tqdm import tqdm


@dataclass
class Endpoint:
    """``name=openai/model@http://host:port/v1`` (name and ``openai/`` prefix optional)."""

    name: str
    model: str
    base_url: str
    api_key_env: str = "VLLM_API_KEY"
    gpu_hourly: float | None = None

    @classmethod
    def parse(cls, spec: str, api_key_env: str = "VLLM_API_KEY") -> Endpoint:
        name, _, rest = spec.partition("=") if "=" in spec.split("@")[0] else ("", "", spec)
        model, _, base = rest.partition("@")
        if not base:
            raise ValueError(f"endpoint spec needs @base_url: {spec!r}")
        model = model.removeprefix("openai/")
        return cls(name=name or model, model=model, base_url=base.rstrip("/"), api_key_env=api_key_env)

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env) or "EMPTY"


@dataclass
class Request:
    uid: str
    messages: list[dict[str, str]]
    params: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Result:
    uid: str
    text: str | None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def as_row(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "raw": self.text,
            "latency_s": round(self.latency_s, 4),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "call_error": self.error,
            **self.meta,
        }


async def _one(client: Any, ep: Endpoint, req: Request, sem: asyncio.Semaphore, retries: int) -> Result:
    import openai

    params = dict(req.params)
    extra_body = params.pop("extra_body", None)
    async with sem:
        last: Exception | None = None
        for attempt in range(retries + 1):
            t0 = time.perf_counter()
            try:
                resp = await client.chat.completions.create(
                    model=ep.model, messages=req.messages, extra_body=extra_body, **params
                )
                dt = time.perf_counter() - t0
                usage = resp.usage
                return Result(
                    uid=req.uid,
                    text=resp.choices[0].message.content or "",
                    prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                    completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                    latency_s=dt,
                    meta=req.meta,
                )
            except (openai.APIConnectionError, openai.APITimeoutError, openai.RateLimitError,
                    openai.InternalServerError) as e:
                last = e
                await asyncio.sleep(min(2 ** attempt, 20))
            except openai.APIStatusError as e:  # 4xx: do not retry
                return Result(uid=req.uid, text=None, error=f"{type(e).__name__}: {e}"[:300], meta=req.meta)
        return Result(uid=req.uid, text=None, error=f"{type(last).__name__}: {last}"[:300], meta=req.meta)


async def arun(
    ep: Endpoint,
    requests: Iterable[Request],
    *,
    concurrency: int = 16,
    on_result: Callable[[Result], None] | None = None,
    retries: int = 2,
    timeout: float = 120.0,
    desc: str | None = None,
) -> tuple[list[Result], float]:
    """Run requests concurrently. Returns (results, wall-clock seconds)."""
    import openai

    reqs = list(requests)
    client = openai.AsyncOpenAI(base_url=ep.base_url, api_key=ep.api_key, timeout=timeout, max_retries=0)
    sem = asyncio.Semaphore(concurrency)
    results: list[Result] = []
    t0 = time.perf_counter()
    tasks = [asyncio.create_task(_one(client, ep, r, sem, retries)) for r in reqs]
    with tqdm(total=len(tasks), desc=desc or ep.name, unit="req") as bar:
        for fut in asyncio.as_completed(tasks):
            res = await fut
            results.append(res)
            if on_result:
                on_result(res)
            bar.update(1)
    wall = time.perf_counter() - t0
    await client.close()
    return results, wall


def run(ep: Endpoint, requests: Iterable[Request], **kw: Any) -> tuple[list[Result], float]:
    return asyncio.run(arun(ep, requests, **kw))


def judge_params(kind: str | None = None, structured: bool = False) -> dict[str, Any]:
    """CuratorKIT's judge decoding contract, optionally with structured outputs."""
    from .config import JUDGE_MAX_TOKENS, JUDGE_TEMPERATURE
    from .schemas import response_format

    p: dict[str, Any] = {"temperature": JUDGE_TEMPERATURE, "max_tokens": JUDGE_MAX_TOKENS}
    if structured:
        if kind is None:
            raise ValueError("structured outputs need the gate kind")
        p["extra_body"] = {"response_format": response_format(kind)}
    return p
