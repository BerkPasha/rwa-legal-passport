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


async def _send_error(send: Send, status_code: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status_code,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"connection", b"close"),
        ],
    })
    await send({"type": "http.response.body", "body": body})
