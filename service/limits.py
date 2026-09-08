from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass
from time import monotonic


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after: int


class SlidingWindowRateLimiter:
    """Small in-process guardrail for costly authenticated operations."""

    def __init__(self, *, requests: int, window_seconds: int = 60):
        if requests < 1 or window_seconds < 1:
            raise ValueError("requests and window_seconds must be positive")
        self.requests = requests
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def consume(self, key: str, now: float | None = None) -> RateLimitDecision:
        current = monotonic() if now is None else now
        cutoff = current - self.window_seconds
        with self._lock:
            events = self._events.setdefault(key, deque())
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.requests:
                retry_after = max(1, math.ceil(events[0] + self.window_seconds - current))
                return RateLimitDecision(False, 0, retry_after)
            events.append(current)
            return RateLimitDecision(True, self.requests - len(events), 0)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
