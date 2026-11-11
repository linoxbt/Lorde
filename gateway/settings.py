"""Gateway and worker configuration from environment variables (spec Section 16).

DATABASE_URL: use Supabase's connection string with ``sslmode=verify-full`` and ``sslrootcert`` pointing at
the Supabase CA certificate (Dashboard > Database > SSL), e.g.
``postgresql://...@db.<ref>.supabase.co:5432/postgres?sslmode=verify-full&sslrootcert=/etc/ssl/supabase-ca.crt``.
``sslmode=require`` encrypts but does not verify the server, so it does not stop a man in the middle.

Tenant fairness (audit N1; see gateway/slots.py and gateway/quota.py):

- ``LORDE_UPSTREAM_CONCURRENCY`` is the number of calls Serve should have in flight at once (llama.cpp: its
  ``--parallel`` slots; vLLM: what it batches well). With ``LORDE_WORKER_SHARES_UPSTREAM=1`` (the default: the
  job worker calls the same Serve), the Gateway keeps ``LORDE_WORKER_JUDGE_CONCURRENCY`` of them for the
  worker and uses the rest (at least one) for API calls: ``api_upstream_slots``. So on the one-slot CPU
  deployment Serve sees at most one API call plus one job judgment at a time, and an API call waits for at most
  one job judgment inside Serve.
- ``LORDE_ORG_UPSTREAM_CONCURRENCY`` (1): API calls in flight per organization.
- ``LORDE_UPSTREAM_QUEUE_TIMEOUT_S`` (30) and ``LORDE_UPSTREAM_MAX_WAITERS`` (32): how long, and how many
  requests may, wait for a slot; past either, 429 ``rate_limited`` "model busy, retry later".
- ``LORDE_ORG_DAILY_JUDGMENTS`` (5000, 0 = unlimited): judgments per organization per UTC day, API and jobs
  together; past it, 429 ``quota_exceeded`` until 00:00 UTC.

Per-IP limit (audit N2): ``LORDE_IP_RPM`` (300, 0 = off) requests per minute per client IP on every keyed route,
checked before the API key is looked up, so a flood of made-up keys from one address is refused without
reaching the database. The client IP is the TCP peer as uvicorn reports it. Behind Railway's proxy run uvicorn
with ``--proxy-headers --forwarded-allow-ips='*'`` (infra/start-api.sh does): uvicorn then reports the first
X-Forwarded-For hop, the address Railway's edge saw. Without uvicorn's proxy headers, list the proxy addresses
(IPs or CIDRs, or ``*``) in ``LORDE_TRUSTED_PROXIES`` and the Gateway reads X-Forwarded-For's first hop itself,
only for requests that come from one of them. The Console relays its users' calls from Vercel's few egress
addresses; with ``LORDE_RELAY_SECRET`` set (32+ characters, the same value in the Console), a request carrying
``x-lorde-relay-secret: <secret>`` is limited by its ``x-lorde-client-ip`` header instead. IPv6 clients are
limited per /64.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _csv(name: str) -> list[str]:
    return [x.strip() for x in (_env(name, "") or "").split(",") if x.strip()]


@dataclass
class Settings:
    vllm_base_url: str = field(default_factory=lambda: _env("VLLM_BASE_URL", "http://localhost:8002/v1"))
    vllm_api_key: str = field(default_factory=lambda: _env("VLLM_API_KEY", "EMPTY"))
    served_model: str = field(default_factory=lambda: _env("LORDE_SERVED_MODEL", "lorde"))
    model_repo: str = field(default_factory=lambda: _env("LORDE_MODEL_REPO", "lorde-tiny-aya"))
    model_revision: str = field(default_factory=lambda: _env("LORDE_MODEL_REVISION", "dev"))
    # read the active model from model_versions (state='active') when DATABASE_URL is set; env is the fallback
    model_registry: bool = field(default_factory=lambda: _env("LORDE_MODEL_REGISTRY", "1") == "1")
    key_pepper: str = field(default_factory=lambda: _env("LORDE_KEY_PEPPER", ""))
    keys_file: str = field(default_factory=lambda: _env("LORDE_KEYS_FILE", "keys.json"))
    usage_file: str | None = field(default_factory=lambda: _env("LORDE_USAGE_FILE"))
    metrics_token: str | None = field(default_factory=lambda: _env("LORDE_METRICS_TOKEN"))
    # /docs, /redoc and /openapi.json are off unless LORDE_PUBLIC_DOCS=1
    public_docs: bool = field(default_factory=lambda: _env("LORDE_PUBLIC_DOCS", "0") == "1")
    access_log: bool = field(default_factory=lambda: _env("LORDE_ACCESS_LOG", "1") == "1")
    allowed_origins: list[str] = field(default_factory=lambda: _csv("ALLOWED_ORIGINS"))
    default_threshold: float = field(default_factory=lambda: float(_env("LORDE_DEFAULT_THRESHOLD", "0.7")))
    max_batch: int = field(default_factory=lambda: int(_env("LORDE_MAX_BATCH", "256")))
    max_body_bytes: int = 5 * 1024 * 1024
    max_field_chars: int = 32_000
    max_source_chars: int = 24_000
    max_chat_chars: int = 120_000          # all message contents of one /v1/chat/completions call
    max_chat_tokens: int = 512             # max_tokens clamp on the OpenAI-compatible proxy
    upstream_timeout_s: float = field(default_factory=lambda: float(_env("LORDE_UPSTREAM_TIMEOUT", "30")))
    upstream_concurrency: int = field(default_factory=lambda: int(_env("LORDE_UPSTREAM_CONCURRENCY", "16")))
    breaker_failures: int = 20
    breaker_window_s: float = 30.0
    max_temperature: float = 0.3
    mcp_batch_limit: int = 64
    key_cache_size: int = 10_000           # bounded LRU of key lookups (positive and negative, each)
    usage_queue_max: int = 10_000          # usage events held in memory while the database is unreachable
    database_url: str | None = field(default_factory=lambda: _env("DATABASE_URL"))
    redis_url: str | None = field(default_factory=lambda: _env("REDIS_URL"))
    supabase_url: str | None = field(default_factory=lambda: _env("SUPABASE_URL"))
    supabase_service_key: str | None = field(default_factory=lambda: _env("SUPABASE_SERVICE_ROLE_KEY"))
    storage_dir: str | None = field(default_factory=lambda: _env("LORDE_STORAGE_DIR"))
    public_url: str = field(default_factory=lambda: _env("LORDE_PUBLIC_URL", "") or "")
    max_upload_bytes: int = 200 * 1024 * 1024
    max_active_jobs: int = 3
    # worker limits (spec Section 11.4; audit S4)
    job_max_seconds: int = field(default_factory=lambda: int(_env("LORDE_JOB_MAX_SECONDS", "3600")))
    job_max_rows: int = field(default_factory=lambda: int(_env("LORDE_JOB_MAX_ROWS", "50000")))
    job_max_input_bytes: int = field(
        default_factory=lambda: int(_env("LORDE_JOB_MAX_INPUT_BYTES", str(200 * 1024 * 1024))))
    # one job judgment at a time by default: jobs share Serve with API calls (see the module docstring)
    worker_judge_concurrency: int = field(default_factory=lambda: int(_env("LORDE_WORKER_JUDGE_CONCURRENCY", "1")))
    worker_shares_upstream: bool = field(default_factory=lambda: _env("LORDE_WORKER_SHARES_UPSTREAM", "1") == "1")
    # tenant fairness (audit N1)
    org_upstream_concurrency: int = field(
        default_factory=lambda: int(_env("LORDE_ORG_UPSTREAM_CONCURRENCY", "1")))
    upstream_queue_timeout_s: float = field(
        default_factory=lambda: float(_env("LORDE_UPSTREAM_QUEUE_TIMEOUT_S", "30")))
    upstream_max_waiters: int = field(default_factory=lambda: int(_env("LORDE_UPSTREAM_MAX_WAITERS", "32")))
    org_daily_judgments: int = field(default_factory=lambda: int(_env("LORDE_ORG_DAILY_JUDGMENTS", "5000")))
    # per-IP limit before key resolution (audit N2)
    ip_rpm: int = field(default_factory=lambda: int(_env("LORDE_IP_RPM", "300")))
    trusted_proxies: list[str] = field(default_factory=lambda: _csv("LORDE_TRUSTED_PROXIES"))
    # the Console's server-side relay sends this secret plus x-lorde-client-ip (its user's address)
    relay_secret: str | None = field(default_factory=lambda: _env("LORDE_RELAY_SECRET"))
    mcp_allowed_hosts: list[str] = field(default_factory=lambda: _csv("LORDE_MCP_ALLOWED_HOSTS"))

    def __post_init__(self) -> None:
        if not self.mcp_allowed_hosts and self.public_url:
            host = urlparse(self.public_url).netloc
            if host:  # DNS-rebinding protection for /mcp defaults to the Gateway's own public host
                self.mcp_allowed_hosts = [host, f"{host}:*"]

    @property
    def api_upstream_slots(self) -> int:
        """Global in-flight API calls to Serve: what the job worker does not use (at least one)."""
        if self.worker_shares_upstream:
            return max(1, self.upstream_concurrency - self.worker_judge_concurrency)
        return max(1, self.upstream_concurrency)

    @property
    def model_label(self) -> str:
        return f"{self.model_repo.split('/')[-1]}@{self.model_revision}"

    def check(self) -> None:
        if not self.key_pepper or len(self.key_pepper) < 32:
            raise RuntimeError("LORDE_KEY_PEPPER must be set to 32+ random characters")
