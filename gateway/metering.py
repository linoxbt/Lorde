"""Usage events: one row per call, batched every second, no request content (spec Section 11.3)."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("lorde.usage")


class UsageSink:
    """Appends usage events to a JSONL file (or the log). Phase 2 writes them to Supabase usage_events."""

    def __init__(self, path: str | None) -> None:
        self.path = Path(path) if path else None
        self.queue: list[dict[str, Any]] = []
        self._task: asyncio.Task | None = None

    def record(self, **event: Any) -> None:
        event.setdefault("ts", datetime.now(UTC).isoformat())
        self.queue.append(event)

    def flush(self) -> None:
        if not self.queue:
            return
        batch, self.queue = self.queue, []
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                for e in batch:
                    f.write(json.dumps(e) + "\n")
        else:
            for e in batch:
                log.info("usage %s", json.dumps(e))

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            self.flush()

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        self.flush()
