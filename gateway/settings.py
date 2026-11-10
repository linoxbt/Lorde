"""Gateway configuration from environment variables (spec Section 16)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


@dataclass
class Settings:
    vllm_base_url: str = field(default_factory=lambda: _env("VLLM_BASE_URL", "http://localhost:8002/v1"))
    vllm_api_key: str = field(default_factory=lambda: _env("VLLM_API_KEY", "EMPTY"))
    served_model: str = field(default_factory=lambda: _env("LORDE_SERVED_MODEL", "lorde"))
    model_repo: str = field(default_factory=lambda: _env("LORDE_MODEL_REPO", "lorde-tiny-aya"))
    model_revision: str = field(default_factory=lambda: _env("LORDE_MODEL_REVISION", "dev"))
    key_pepper: str = field(default_factory=lambda: _env("LORDE_KEY_PEPPER", ""))
    keys_file: str = field(default_factory=lambda: _env("LORDE_KEYS_FILE", "keys.json"))
    usage_file: str | None = field(default_factory=lambda: _env("LORDE_USAGE_FILE"))
    metrics_token: str | None = field(default_factory=lambda: _env("LORDE_METRICS_TOKEN"))
    allowed_origins: list[str] = field(
        default_factory=lambda: [o.strip() for o in (_env("ALLOWED_ORIGINS", "") or "").split(",") if o.strip()])
    default_threshold: float = field(default_factory=lambda: float(_env("LORDE_DEFAULT_THRESHOLD", "0.7")))
    max_batch: int = field(default_factory=lambda: int(_env("LORDE_MAX_BATCH", "256")))
    max_body_bytes: int = 5 * 1024 * 1024
    max_field_chars: int = 32_000
    max_source_chars: int = 24_000
    upstream_timeout_s: float = 30.0
    upstream_concurrency: int = field(default_factory=lambda: int(_env("LORDE_UPSTREAM_CONCURRENCY", "16")))
    breaker_failures: int = 20
    breaker_window_s: float = 30.0
    max_temperature: float = 0.3
    mcp_batch_limit: int = 64
    database_url: str | None = field(default_factory=lambda: _env("DATABASE_URL"))
    redis_url: str | None = field(default_factory=lambda: _env("REDIS_URL"))
    supabase_url: str | None = field(default_factory=lambda: _env("SUPABASE_URL"))
    supabase_service_key: str | None = field(default_factory=lambda: _env("SUPABASE_SERVICE_ROLE_KEY"))
    storage_dir: str | None = field(default_factory=lambda: _env("LORDE_STORAGE_DIR"))
    public_url: str = field(default_factory=lambda: _env("LORDE_PUBLIC_URL", "") or "")
    max_upload_bytes: int = 200 * 1024 * 1024
    max_active_jobs: int = 3
    mcp_allowed_hosts: list[str] = field(
        default_factory=lambda: [h.strip() for h in (_env("LORDE_MCP_ALLOWED_HOSTS", "") or "").split(",") if h.strip()])

    @property
    def model_label(self) -> str:
        return f"{self.model_repo.split('/')[-1]}@{self.model_revision}"

    def check(self) -> None:
        if not self.key_pepper or len(self.key_pepper) < 32:
            raise RuntimeError("LORDE_KEY_PEPPER must be set to 32+ random characters")
