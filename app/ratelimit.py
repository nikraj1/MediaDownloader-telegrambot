"""Per-user sliding-window rate limiter (clock injectable for tests)."""
from __future__ import annotations

import time
from collections import deque
from typing import Callable


class SlidingWindowLimiter:
    def __init__(self, limit: int, window: float, clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.window = window
        self.clock = clock
        self._hits: dict[int, deque[float]] = {}

    def _trim(self, key: int, now: float) -> deque[float]:
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        return hits

    def retry_after(self, key: int) -> float:
        """0 if allowed now, else seconds until the next slot frees up."""
        now = self.clock()
        hits = self._trim(key, now)
        if len(hits) < self.limit:
            return 0.0
        return max(0.0, self.window - (now - hits[0]))

    def hit(self, key: int) -> None:
        now = self.clock()
        self._trim(key, now).append(now)

    def gc(self) -> None:
        now = self.clock()
        for key in list(self._hits):
            if not self._trim(key, now):
                del self._hits[key]
