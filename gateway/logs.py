"""Structured JSON logs (spec Section 11.3): one object per line, never request content or keys."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
               "level": record.levelname.lower(), "logger": record.name, "msg": record.getMessage()}
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            out.update(fields)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str, ensure_ascii=False)


def setup(level: int = logging.INFO) -> None:
    """Send the ``lorde.*`` loggers to stderr as JSON once (uvicorn configures only its own loggers)."""
    root = logging.getLogger("lorde")
    if any(getattr(h, "_lorde", False) for h in root.handlers):
        return
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter())
    h._lorde = True  # type: ignore[attr-defined]
    root.addHandler(h)
    root.setLevel(level)
    root.propagate = False
