from __future__ import annotations

from typing import Any


class LordeAPIError(Exception):
    """An error body from the Gateway: {"error": {"code", "message", "request_id"}}."""

    def __init__(self, status: int, code: str, message: str, request_id: str | None = None,
                 body: dict[str, Any] | None = None) -> None:
        super().__init__(f"{status} {code}: {message}" + (f" (request {request_id})" if request_id else ""))
        self.status, self.code, self.message, self.request_id, self.body = status, code, message, request_id, body

    @property
    def retryable(self) -> bool:
        return self.status in (429, 502, 503, 504)


class IncompatibleGateway(LordeAPIError):
    pass
