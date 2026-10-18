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


# ── Per-client limits of a public deployment (Stage 10C-2, DEPLOYMENT.md) ─────


class ClientRateLimiter:
    """
    Sliding-window limit per client address whose memory stays bounded: a client's timestamps are
    dropped once they leave the window, and idle clients are swept out regularly, so that many
    different addresses cannot make it grow without limit.
    """

    _SWEEP_EVERY = 1000

    def __init__(self, max_events: int, window_seconds: float = 60.0) -> None:
        self.max_events = max_events
        self._window = float(window_seconds)
        self._events: dict[str, list[float]] = {}
        self._calls = 0
        self._lock = Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        events = [t for t in self._events.get(key, ()) if now - t < self._window]
        if events:
            self._events[key] = events
        else:
            self._events.pop(key, None)
        return events

    def _sweep(self, now: float) -> None:
        self._calls += 1
        if self._calls % self._SWEEP_EVERY == 0:
            for key in list(self._events):
                self._recent(key, now)

    def hit(self, key: str) -> bool:
        """Counts one event; False if the client already reached the limit (not counted then)."""
        now = time.monotonic()
        with self._lock:
            self._sweep(now)
            events = self._recent(key, now)
            if len(events) >= self.max_events:
                return False
            self._events[key] = [*events, now]
            return True

    def exceeded(self, key: str) -> bool:
        """Whether the client has reached the limit, without counting anything."""
        now = time.monotonic()
        with self._lock:
            self._sweep(now)
            return len(self._recent(key, now)) >= self.max_events

    def record(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._events[key] = [*self._recent(key, now), now]

    def tracked_clients(self) -> int:
        with self._lock:
            return len(self._events)
