"""
Request body size limit (Stage 9B-2).

Starlette reads/buffers the whole multipart body before the endpoint runs and
before authentication happens. This ASGI middleware limits the body before it
reaches the application:
- If Content-Length exceeds the limit, the request gets 413 without being parsed
  (for a moderate overshoot the body is read and discarded without being stored,
  so that the client can receive the response).
- Without Content-Length (chunked), the bytes read are counted; when the limit is
  exceeded, reading stops and 413 is returned.

Limit: MAX_UPLOAD_SIZE_MB + a fixed allowance for the multipart envelope. The actual
file size limit is enforced separately inside the endpoint.
"""

import json

from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings

# Allowance for multipart boundary lines, form fields and headers
_ENVELOPE_ALLOWANCE = 64 * 1024
# A body that exceeds the limit moderately is read and discarded without being
# stored, so the client receives a readable 413 instead of a connection reset.
# A body above this cap is not read at all (connection closes; the client may see a reset).
_DRAIN_FACTOR = 4
_DRAIN_HARD_CAP = 64 * 1024 * 1024
_DETAIL = "Request body exceeds the maximum allowed size."


def max_request_body_bytes() -> int:
    return settings.max_upload_size_mb * 1024 * 1024 + _ENVELOPE_ALLOWANCE


def _drain_cap(limit: int) -> int:
    return min(limit * _DRAIN_FACTOR, _DRAIN_HARD_CAP)


async def _drain(receive: Receive, cap: int) -> None:
    """Reads and discards the body without keeping it in memory (at most `cap` bytes)."""
    consumed = 0
    while consumed <= cap:
        message = await receive()
        if message["type"] != "http.request":
            return
        consumed += len(message.get("body", b""))
        if not message.get("more_body", False):
            return


class _BodyTooLarge(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=413, detail=_DETAIL)


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = max_request_body_bytes()
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    await _send_error(send, 400, "Invalid Content-Length header.")
                    return
                if declared > limit:
                    if declared <= _drain_cap(limit):
                        await _drain(receive, _drain_cap(limit))
                    await _send_error(send, 413, _DETAIL)
                    return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # FastAPI re-raises HTTPException unchanged → 413
                    raise _BodyTooLarge()
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not response_started:
                await _send_error(send, 413, _DETAIL)


async def _send_error(send: Send, status_code: int, detail: str, **extra: object) -> None:
    body = json.dumps({"detail": detail, **extra}).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"connection", b"close"),
    ]
    if status_code == 429:
        headers.append((b"retry-after", b"60"))
    await send({
        "type": "http.response.start",
        "status": status_code,
        "headers": headers,
    })
    await send({"type": "http.response.body", "body": body})


# ── Showcase deployment policies (Stage 10C-1, GRANT_DEMO.md) ─────────────────
# Each is read from the settings on every request, so a test can switch it on and off.

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
READ_ONLY_DETAIL = "This server is a read-only showcase: no data can be created or changed."
_API_DOCS_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"})


class ReadOnlyModeMiddleware:
    """
    READ_ONLY_MODE: every request other than GET, HEAD and OPTIONS is refused with 403 before
    authentication and before any route runs, whatever the credential. Being structural, it also
    covers write routes added later. No GET route of the application changes stored data.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and settings.read_only_mode and scope["method"].upper() not in _SAFE_METHODS:
            from app.access_registry import security_logger
            security_logger.info("request.refused_read_only method=%s path=%s", scope["method"], scope["path"])
            await _send_error(send, 403, READ_ONLY_DETAIL, read_only_mode=True)
            return
        await self.app(scope, receive, send)


class ApiDocsGateMiddleware:
    """API_DOCS_ENABLED=false: /docs, /redoc and /openapi.json answer 404 like any unknown path."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not settings.api_docs_enabled and scope["path"] in _API_DOCS_PATHS:
            await _send_error(send, 404, "Not Found")
            return
        await self.app(scope, receive, send)


# Every response: no MIME sniffing, no framing, no referrer, no caching of API data (it can hold
# legal records) and no powerful browser features. The API serves no HTML or scripts, so its
# content security policy allows nothing; only Swagger UI and ReDoc, which load their scripts
# and styles from a CDN, are left without one.
_SECURITY_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
    (b"cache-control", b"no-store"),
)
_API_CSP = (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'")
_HTML_DOCS_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc"})


class SecurityHeadersMiddleware:
    """Adds the headers above to every HTTP response, without replacing one a route has set."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        extra = _SECURITY_HEADERS if scope["path"] in _HTML_DOCS_PATHS else (*_SECURITY_HEADERS, _API_CSP)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers.extend((name, value) for name, value in extra if name not in present)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


# ── Per-client rate limits of a public deployment (Stage 10C-2, DEPLOYMENT.md) ─

_RATE_LIMIT_DETAIL = "Too many requests from this address. Try again in a minute."
_AUTH_FAILURE_DETAIL = "Too many failed authentication attempts from this address. Try again in a minute."


def _client_key(scope: Scope) -> str:
    client = scope.get("client")
    return client[0] if client else "unknown"


def _hashed(key: str) -> str:
    """Logged instead of the address itself."""
    import hashlib
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


class ClientRateLimitMiddleware:
    """
    CLIENT_RATE_LIMIT_PER_MINUTE: requests per client address and minute (429 beyond it).
    AUTH_FAILURE_LIMIT_PER_MINUTE: once an address has had that many 401 answers within a minute,
    every request from it gets 429 until the minute has passed, which slows down credential guessing.
    Both are off (0) by default. CORS preflights are not counted. The address is the ASGI client:
    behind a reverse proxy, uvicorn must be started with --proxy-headers and --forwarded-allow-ips
    set to the proxy, otherwise every visitor shares the proxy's address.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._limiters: dict[tuple[str, int], object] = {}

    def _limiter(self, kind: str, per_minute: int):
        from app.rate_limit import ClientRateLimiter
        key = (kind, per_minute)
        if key not in self._limiters:   # the setting is read per request, so a test can change it
            self._limiters[key] = ClientRateLimiter(per_minute, 60.0)
        return self._limiters[key]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        requests, failures = settings.client_rate_limit_per_minute, settings.auth_failure_limit_per_minute
        if scope["type"] != "http" or scope["method"].upper() == "OPTIONS" or not (requests or failures):
            await self.app(scope, receive, send)
            return
        from app.access_registry import security_logger
        client = _client_key(scope)
        failure_limiter = self._limiter("auth_failures", failures) if failures else None
        if failure_limiter is not None and failure_limiter.exceeded(client):
            security_logger.warning("rate_limit.refused kind=authentication_failures client=%s", _hashed(client))
            await _send_error(send, 429, _AUTH_FAILURE_DETAIL, rate_limit="authentication_failures")
            return
        if requests and not self._limiter("requests", requests).hit(client):
            security_logger.warning("rate_limit.refused kind=requests client=%s", _hashed(client))
            await _send_error(send, 429, _RATE_LIMIT_DETAIL, rate_limit="requests")
            return

        async def counting_send(message: Message) -> None:
            if failure_limiter is not None and message["type"] == "http.response.start" and message["status"] == 401:
                failure_limiter.record(client)
            await send(message)

        await self.app(scope, receive, counting_send)
