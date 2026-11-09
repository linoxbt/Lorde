"""Per-key token buckets. In-process (one replica); Redis-backed buckets replace this when scaling out."""

from __future__ import annotations

import threading
import time


class TokenBuckets:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_ts)

    def take(self, key: str, per_minute: int, cost: int = 1) -> float:
        """Spend ``cost`` tokens; return 0 if allowed, else seconds until enough tokens refill."""
        rate = per_minute / 60.0
        cap = float(per_minute)
        now = time.monotonic()
        with self._lock:
            tokens, last = self._state.get(key, (cap, now))
            tokens = min(cap, tokens + (now - last) * rate)
            if cost > cap:
                return float("inf")
            if tokens >= cost:
                self._state[key] = (tokens - cost, now)
                return 0.0
            self._state[key] = (tokens, now)
            return (cost - tokens) / rate
