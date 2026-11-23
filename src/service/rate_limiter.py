"""Thread-safe Token-Bucket Rate Limiter for abuse and burst protection."""
from __future__ import annotations

import threading
import time
from typing import Dict, Tuple


class TokenBucketRateLimiter:
    """In-memory per-client token bucket rate limiter."""

    def __init__(self, capacity: int = 60, refill_rate_per_sec: float = 1.0):
        self.capacity = capacity
        self.refill_rate_per_sec = refill_rate_per_sec
        self._buckets: Dict[str, Tuple[float, float]] = {}  # client_id -> (tokens, last_ts)
        self._lock = threading.Lock()

    def allow(self, client_id: str, tokens: float = 1.0) -> bool:
        now = time.monotonic()
        with self._lock:
            current_tokens, last_ts = self._buckets.get(client_id, (float(self.capacity), now))
            elapsed = max(0.0, now - last_ts)
            refilled = min(float(self.capacity), current_tokens + elapsed * self.refill_rate_per_sec)
            if refilled >= tokens:
                self._buckets[client_id] = (refilled - tokens, now)
                return True
            self._buckets[client_id] = (refilled, now)
            return False

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()
