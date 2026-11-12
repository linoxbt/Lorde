"""Error bodies: {"error": {"code", "message", "request_id"}} (spec Section 11.2).

Codes: 400 invalid_request, 401 unauthenticated, 403 forbidden, 404 not_found, 409 conflict, 413 payload_too_large,
422 judge_unparseable, 422 unsupported_dataset (a job's dataset_url names a Hub repo but no file), 429 rate_limited (a key's or an address's requests per minute, or the model is busy: retry
after Retry-After), 429 quota_exceeded (the organization's daily judgments are used up: Retry-After is the time
until 00:00 UTC, so clients should not retry sooner), 429 capacity_exceeded (the whole service's daily judgment
capacity, LORDE_GLOBAL_DAILY_CAP, is used up; Retry-After as for quota_exceeded), 503 model_unavailable, 503 unavailable, 503 not_configured,
500 internal_error.
"""

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
UnsupportedDataset = _make("UnsupportedDataset", 422, "unsupported_dataset")  # e.g. a Hub repo without a file
RateLimited = _make("RateLimited", 429, "rate_limited")
QuotaExceeded = _make("QuotaExceeded", 429, "quota_exceeded")      # the org's daily judgments (audit N1b)
CapacityExceeded = _make("CapacityExceeded", 429, "capacity_exceeded")  # the service's daily judgments (S1)
ModelUnavailable = _make("ModelUnavailable", 503, "model_unavailable")
Unavailable = _make("Unavailable", 503, "unavailable")          # a backing store (database) is unreachable
InternalError = _make("InternalError", 500, "internal_error")


# Quota spent on a judgment that never reached the model (or that Serve refused) is given back.
# client_disconnected marks batch items never started because the caller had gone (audit 2, B11).
REFUNDED = frozenset({"rate_limited", "model_unavailable", "invalid_request", "unavailable", "client_disconnected"})


def refundable(err: LordeError) -> bool:
    """Refund the quota for errors where the model did no work. An upstream timeout may have run the
    judgment (the model server was busy with it), so it is charged (``err.charged``)."""
    return err.code in REFUNDED and not getattr(err, "charged", False)


def body(err: LordeError, request_id: str) -> dict[str, Any]:
    return {"error": {"code": err.code, "message": err.message, "request_id": request_id, **err.extra}}
