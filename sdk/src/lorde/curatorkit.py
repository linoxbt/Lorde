"""CuratorKIT preset: ``CuratorConfig(..., judge_llm=judge_llm())`` uses Lorde as the judge.

The Gateway's OpenAI-compatible endpoint recognises CuratorKIT's gate prompts by their template
fingerprint and constrains each reply to the gate's JSON schema, so every reply is gate-usable.
"""

from __future__ import annotations

import os


def judge_llm(api_key: str | None = None, base_url: str | None = None, *, concurrency: int = 16,
              timeout: float = 60.0) -> dict:
    key = api_key or os.environ.get("LORDE_API_KEY")
    if not key:
        raise ValueError("pass api_key= or set LORDE_API_KEY")
    base = (base_url or os.environ.get("LORDE_BASE_URL") or "http://localhost:8080").rstrip("/")
    return {"model": "openai/lorde", "api_base": f"{base}/v1", "api_key": key, "temperature": 0.1,
            "max_tokens": 512, "concurrency": concurrency, "timeout": timeout}
