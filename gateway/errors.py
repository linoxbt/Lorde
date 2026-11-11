"""Error bodies: {"error": {"code", "message", "request_id"}} (spec Section 11.2)."""

from __future__ import annotations

from typing import Any


class LordeError(Exception):
    status = 500
    code = "internal_error"

    def __init__(self, message: str, *, headers: dict[str, str] | None = None, extra: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.headers = headers or {}
        self.extra = extra or {}

    def __str__(self) -> str:  # MCP tool errors and logs show the code too
        return f"{self.code}: {self.message}"


def _make(name: str, status: int, code: str) -> type[LordeError]:
    return type(name, (LordeError,), {"status": status, "code": code})


InvalidRequest = _make("InvalidRequest", 400, "invalid_request")
Unauthenticated = _make("Unauthenticated", 401, "unauthenticated")
Forbidden = _make("Forbidden", 403, "forbidden")
NotFound = _make("NotFound", 404, "not_found")
Conflict = _make("Conflict", 409, "conflict")
PayloadTooLarge = _make("PayloadTooLarge", 413, "payload_too_large")
JudgeUnparseable = _make("JudgeUnparseable", 422, "judge_unparseable")
RateLimited = _make("RateLimited", 429, "rate_limited")
ModelUnavailable = _make("ModelUnavailable", 503, "model_unavailable")
Unavailable = _make("Unavailable", 503, "unavailable")          # a backing store (database) is unreachable
InternalError = _make("InternalError", 500, "internal_error")


def body(err: LordeError, request_id: str) -> dict[str, Any]:
    return {"error": {"code": err.code, "message": err.message, "request_id": request_id, **err.extra}}
