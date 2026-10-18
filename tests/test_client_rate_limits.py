"""
Stage 10C-2 — per-client rate limits of a public deployment (app/middleware.py, DEPLOYMENT.md).

Coverage:
 1. Off by default
 2. CLIENT_RATE_LIMIT_PER_MINUTE: 429 beyond the limit, per client address, with Retry-After, CORS
    and security headers; preflights are not counted
 3. AUTH_FAILURE_LIMIT_PER_MINUTE: after that many 401 answers, every request from the address is
    refused until the minute has passed; other addresses are unaffected
 4. The limiter's memory stays bounded; logs carry no address
"""

import logging

import pytest
from httpx import ASGITransport, AsyncClient

from app import rate_limit
from app.config import settings
from app.main import app
from app.rate_limit import ClientRateLimiter
from tests.conftest import ISSUER_TOKEN, bearer


def _client_from(address: str) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, client=(address, 50000)), base_url="http://test")


@pytest.fixture
def fresh_limits(monkeypatch):
    """A new middleware instance per test would need a new app: reset its limiter cache instead."""
    stack = app.middleware_stack
    if stack is not None:
        layer = stack
        while layer is not None:
            if hasattr(layer, "_limiters"):
                layer._limiters.clear()
            layer = getattr(layer, "app", None)
    yield monkeypatch


async def test_the_limits_are_off_by_default(client: AsyncClient):
    assert settings.client_rate_limit_per_minute == 0 and settings.auth_failure_limit_per_minute == 0
    for _ in range(50):
        assert (await client.get("/health")).status_code == 200


async def test_requests_beyond_the_limit_are_refused_per_address(client: AsyncClient, fresh_limits):
    fresh_limits.setattr(settings, "client_rate_limit_per_minute", 3)
    origin = settings.cors_origins[0]
    async with _client_from("203.0.113.7") as first, _client_from("203.0.113.8") as second:
        for _ in range(3):
            assert (await first.get("/health")).status_code == 200
        refused = await first.get("/health", headers={"Origin": origin})
        assert refused.status_code == 429
        assert refused.json() == {"detail": "Too many requests from this address. Try again in a minute.",
                                  "rate_limit": "requests"}
        assert refused.headers["retry-after"] == "60"
        assert refused.headers["access-control-allow-origin"] == origin
        assert refused.headers["x-content-type-options"] == "nosniff"
        # Another address has its own allowance; preflights are never counted
        assert (await second.get("/health")).status_code == 200
        preflight = await first.options("/assets", headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
        assert preflight.status_code == 200


async def test_repeated_failed_authentication_blocks_the_address(client: AsyncClient, fresh_limits, caplog):
    fresh_limits.setattr(settings, "auth_failure_limit_per_minute", 2)
    async with _client_from("198.51.100.4") as guesser, _client_from("198.51.100.5") as other:
        for _ in range(2):
            assert (await guesser.get("/assets", headers=bearer("guess-1234567890"))).status_code == 401
        with caplog.at_level(logging.WARNING, logger="rwa_security"):
            blocked = await guesser.get("/assets", headers=bearer(ISSUER_TOKEN))   # even a valid credential
        assert blocked.status_code == 429
        assert blocked.json()["rate_limit"] == "authentication_failures"
        assert (await other.get("/assets", headers=bearer(ISSUER_TOKEN))).status_code == 200
        # Refusals without a credential (403) are not authentication failures
        for _ in range(5):
            assert (await other.get("/assets")).status_code == 403
        assert (await other.get("/assets", headers=bearer(ISSUER_TOKEN))).status_code == 200
    messages = [r.getMessage() for r in caplog.records if "rate_limit.refused" in r.getMessage()]
    assert messages and all("198.51.100" not in m for m in messages)
    assert "guess-1234567890" not in caplog.text and ISSUER_TOKEN not in caplog.text


async def test_the_block_ends_when_the_minute_has_passed(client: AsyncClient, fresh_limits):
    fresh_limits.setattr(settings, "auth_failure_limit_per_minute", 1)
    clock = [1000.0]
    fresh_limits.setattr(rate_limit.time, "monotonic", lambda: clock[0])
    async with _client_from("192.0.2.9") as guesser:
        assert (await guesser.get("/assets", headers=bearer("wrong-token-0000000"))).status_code == 401
        assert (await guesser.get("/assets", headers=bearer(ISSUER_TOKEN))).status_code == 429
        clock[0] += 61
        assert (await guesser.get("/assets", headers=bearer(ISSUER_TOKEN))).status_code == 200


def test_the_limiter_memory_stays_bounded(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: clock[0])
    limiter = ClientRateLimiter(5, 60.0)
    for i in range(5000):
        limiter.hit(f"10.0.{i // 250}.{i % 250}")
    assert limiter.tracked_clients() <= 5000
    clock[0] += 61          # every client is idle now; the next sweep drops them
    for i in range(1000):
        limiter.hit("10.9.9.9")
    assert limiter.tracked_clients() == 1
