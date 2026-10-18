"""
Logging of the application's own events (Stage 10C-3B, T1).

The application writes its security and audit events (approval.created, extraction.manual_created,
authz.permission_denied, ...) to the logger rwa_security, and its passport and anchoring events to
rwa_passport. Unconfigured, both inherit the root logger's level, WARNING, and have no handler, and
uvicorn's default logging configures only its own loggers: in a real server every INFO event was
dropped, although pytest's log capture saw it (staging audit finding H1).

configure_logging() therefore:
- sets both application loggers to INFO, unless a level has already been configured for them;
- adds one handler, writing to stderr like uvicorn's own error log, to the root logger, and only if
  the root logger has no handler yet. A configuration that already handles records (a deployment's
  uvicorn --log-config, pytest's log capture) is never replaced, and a repeated call adds nothing,
  so no event is written twice;
- leaves the root level and uvicorn's loggers unchanged (uvicorn's loggers do not propagate to the
  root logger, so its access and error logs are written exactly as before).

Each line is "<UTC time> <LEVEL> <logger> <event> key=value ...". Control characters in a message are
escaped, so that a value taken from a request cannot forge a log line. The events themselves carry
identifiers, roles and counts only: never a credential, a document's text or a quotation.

This is operational logging to a process stream. Whether events are retained, for how long, and
whether they are tamper-evident is decided by whatever collects that stream (journald, a container
platform, a log service). The durable records of reviews are the approval and correction records
in the database.
"""

import logging
import re
import sys
import time
from typing import Optional, TextIO

APP_LOGGERS = ("rwa_security", "rwa_passport")
HANDLER_NAME = "rwa-application-stream"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


def escape_control_characters(text: str) -> str:
    """Escapes line breaks and other control characters (\\n → \\x0a), e.g. in a request path."""
    return _CONTROL_CHARACTERS.sub(lambda match: f"\\x{ord(match.group()):02x}", text)


class EventFormatter(logging.Formatter):
    """UTC timestamps; control characters of the message escaped (a traceback keeps its lines)."""

    converter = time.gmtime

    def __init__(self) -> None:
        super().__init__(LOG_FORMAT, datefmt=DATE_FORMAT)

    def formatMessage(self, record: logging.LogRecord) -> str:
        # A copy: other handlers of the same record (e.g. pytest's capture) see it unchanged
        escaped = logging.makeLogRecord(record.__dict__)
        escaped.message = escape_control_characters(record.message)
        return super().formatMessage(escaped)


def configure_logging(stream: Optional[TextIO] = None) -> None:
    """Makes the application's INFO events visible; safe to call more than once."""
    for name in APP_LOGGERS:
        logger = logging.getLogger(name)
        if logger.level == logging.NOTSET:
            logger.setLevel(logging.INFO)
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.set_name(HANDLER_NAME)
    handler.setFormatter(EventFormatter())
    root.addHandler(handler)
