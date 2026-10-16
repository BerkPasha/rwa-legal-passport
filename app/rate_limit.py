"""
Simple in-memory request rate limiter.

Uses a sliding time-window algorithm.
Keeps an independent counter for each client (client_id).
Can be replaced with a Redis-based solution in production.
"""

import time
from collections import defaultdict
from threading import Lock

from app.config import settings


class InMemoryRateLimiter:
    """
    Sliding-window rate limiter.

    Thread-safe; can be called from asyncio tasks
    (GIL protection + Lock is sufficient, no asyncio lock is needed).
    """

    def __init__(self, max_requests: int = 60, window_seconds: int = 60) -> None:
        self._max = max_requests
        self._window = float(window_seconds)
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def is_allowed(self, key: str) -> bool:
        """
        Checks whether the request is allowed and updates the counter.
        Returns True if allowed, False if the limit has been exceeded.
        """
        now = time.monotonic()
        with self._lock:
            # Remove expired entries
            self._buckets[key] = [
                t for t in self._buckets[key] if now - t < self._window
            ]
            if len(self._buckets[key]) >= self._max:
                return False
            self._buckets[key].append(now)
            return True


# ── Singletons (production) ───────────────────────────────────────────────────
# Passport: 60 requests per minute / institutional client.
_limiter = InMemoryRateLimiter(max_requests=60, window_seconds=60)

# AI extraction trigger: N requests per hour / user (paid API cost control).
_extraction_limiter = InMemoryRateLimiter(
    max_requests=settings.extraction_rate_limit_per_hour, window_seconds=3600
)


def get_rate_limiter() -> InMemoryRateLimiter:
    """FastAPI dependency — replaced with a fresh instance in tests."""
    return _limiter


def get_extraction_rate_limiter() -> InMemoryRateLimiter:
    """AI extraction trigger limiter — replaced with a fresh instance in tests."""
    return _extraction_limiter
