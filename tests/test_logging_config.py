"""
Stage 10C-3B, T1 — the application's INFO security and audit events reach the server log
(app/logging_config.py; staging audit finding H1).

Coverage:
 1. configure_logging(): INFO of rwa_security and rwa_passport written once, in the documented
    format; WARNING and ERROR still written; repeated calls add nothing; an existing configuration
    (a root handler, an explicit level) is kept; uvicorn's loggers are untouched
 2. Control characters are escaped; a request path cannot forge a log line
 3. A real uvicorn server process, started from the application entrypoint with a temporary
    database, storage and the fictional test registry: approval.created and
    extraction.manual_created appear exactly once in its log stream, WARNING and ERROR events too,
    uvicorn's own startup and access lines are still written, and no credential, invalid token or
    quotation appears
"""

import io
import json
import re
from contextlib import contextmanager
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from httpx import AsyncClient

from app.config import settings
from app.logging_config import APP_LOGGERS, HANDLER_NAME, configure_logging, escape_control_characters
from tests.conftest import (
    _MERIDIAN_ASSET,
    ADMIN_TOKEN,
    INST_KEY_ALPHA,
    ISSUER_TOKEN,
    LEGAL_TOKEN,
    bearer,
    build_test_access_config,
)
from tests.pdf_fixtures import make_source_pdf
from tests.test_approval_evidence_gate import JURISDICTION_QUOTE

ROOT = Path(__file__).resolve().parent.parent
LINE = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z (INFO|WARNING|ERROR) (rwa_security|rwa_passport) "
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


@contextmanager
def bare_logging():
    """
    A process without logging configuration, as under uvicorn's defaults. Entered inside the test
    body: pytest attaches its capture handlers to the root logger when the test call starts. On
    exit, exactly the handlers and levels found on entry are restored.
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_levels = {name: logging.getLogger(name).level for name in APP_LOGGERS}
    root.handlers = []
    for name in APP_LOGGERS:
        logging.getLogger(name).setLevel(logging.NOTSET)
    try:
        yield root
    finally:
        root.handlers = saved_handlers
        for name, level in saved_levels.items():
            logging.getLogger(name).setLevel(level)


# ── 1. configure_logging() ────────────────────────────────────────────────────


def test_without_configuration_info_events_are_dropped():
    """The defect itself: no handler, effective level WARNING."""
    with bare_logging():
        security = logging.getLogger("rwa_security")
        assert security.getEffectiveLevel() == logging.WARNING
        assert not security.isEnabledFor(logging.INFO)


def test_info_warning_and_error_events_are_written_once_in_the_documented_format():
    with bare_logging() as root:
        stream = io.StringIO()
        configure_logging(stream)
        logging.getLogger("rwa_security").info("approval.created extraction=%s type=%s", "e-1", "issuer_attestation")
        logging.getLogger("rwa_passport").info("passport.served client_id=%s", "c-1")
        logging.getLogger("rwa_security").warning("authz.permission_denied user=%s", "u-1")
        logging.getLogger("rwa_security").error("access_config.invalid path=%s", "x.json")
        lines = stream.getvalue().splitlines()
        assert [line.split(" ", 1)[1] for line in lines] == [
            "INFO rwa_security approval.created extraction=e-1 type=issuer_attestation",
            "INFO rwa_passport passport.served client_id=c-1",
            "WARNING rwa_security authz.permission_denied user=u-1",
            "ERROR rwa_security access_config.invalid path=x.json",
        ]
        assert all(re.match(LINE, line) for line in lines)
        assert [h.get_name() for h in root.handlers] == [HANDLER_NAME]


def test_repeated_configuration_adds_nothing_and_writes_each_event_once():
    with bare_logging() as root:
        stream = io.StringIO()
        for _ in range(3):
            configure_logging(stream)
        configure_logging(io.StringIO())          # another stream later: still nothing added
        logging.getLogger("rwa_security").info("extraction.manual_created extraction=%s", "e-2")
        assert len(root.handlers) == 1
        assert stream.getvalue().count("extraction.manual_created extraction=e-2") == 1


def test_an_existing_configuration_is_kept():
    with bare_logging() as root:
        existing = logging.StreamHandler(io.StringIO())
        root.addHandler(existing)
        logging.getLogger("rwa_passport").setLevel(logging.ERROR)      # an explicit, deliberate level
        configure_logging(io.StringIO())
        assert root.handlers == [existing]                     # nothing added: no duplicate output
        assert logging.getLogger("rwa_security").level == logging.INFO
        assert logging.getLogger("rwa_passport").level == logging.ERROR
        logging.getLogger("rwa_security").info("approval.created extraction=%s", "e-3")
        assert "approval.created extraction=e-3" in existing.stream.getvalue()


def test_uvicorn_loggers_and_the_root_level_are_untouched():
    with bare_logging() as root:
        before = {name: (logging.getLogger(name).level, list(logging.getLogger(name).handlers),
                         logging.getLogger(name).propagate) for name in UVICORN_LOGGERS}
        root_level = root.level
        configure_logging(io.StringIO())
        after = {name: (logging.getLogger(name).level, list(logging.getLogger(name).handlers),
                         logging.getLogger(name).propagate) for name in UVICORN_LOGGERS}
        assert after == before
        assert root.level == root_level


# ── 2. Escaping ───────────────────────────────────────────────────────────────


def test_control_characters_cannot_forge_a_log_line():
    with bare_logging():
        stream = io.StringIO()
        configure_logging(stream)
        forged = "/assets\n2026-01-01T00:00:00Z INFO rwa_security approval.created extraction=fake"
        logging.getLogger("rwa_security").info("request.refused_read_only method=%s path=%s", "POST", forged)
        lines = stream.getvalue().splitlines()
        assert len(lines) == 1 and "\\x0a2026-01-01T00:00:00Z" in lines[0]
        assert escape_control_characters("a\r\nb\tc\x00") == "a\\x0d\\x0ab\\x09c\\x00"


def test_a_traceback_keeps_its_lines():
    with bare_logging():
        stream = io.StringIO()
        configure_logging(stream)
        try:
            raise ValueError("boom")
        except ValueError:
            logging.getLogger("rwa_security").exception("storage.failure type=%s", "ValueError")
        text = stream.getvalue()
        assert "ERROR rwa_security storage.failure type=ValueError" in text and "Traceback" in text
        assert "\nValueError: boom" in text


async def test_the_read_only_refusal_logs_an_escaped_path(client: AsyncClient, monkeypatch, caplog):
    monkeypatch.setattr(settings, "read_only_mode", True)
    with caplog.at_level(logging.INFO, logger="rwa_security"):
        r = await client.post("/assets%0Aforged-line", headers=bearer(ADMIN_TOKEN), json={})
    assert r.status_code == 403
    (message,) = [rec.getMessage() for rec in caplog.records if "request.refused_read_only" in rec.getMessage()]
    assert "\n" not in message and "/assets\\x0aforged-line" in message


# ── 3. A real uvicorn server process ──────────────────────────────────────────


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_disposable_server(work: Path) -> tuple[str, dict]:
    """
    Starts `python -m uvicorn app.main:app` exactly as the deployment does, in `work` (so no .env is
    read), with a temporary database and storage, the fictional test registry, AI disabled and
    outbound HTTP pointed at a closed port; drives a review through its API; returns its log.
    """
    registry = work / "access_control.json"
    registry.write_text(json.dumps(build_test_access_config()), encoding="utf-8")
    port, closed = _free_port(), f"http://127.0.0.1:{_free_port()}"
    keep = {k: v for k, v in os.environ.items()
            if k.upper() in {"PATH", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
                              "PATHEXT", "COMSPEC", "WINDIR", "LOCALAPPDATA", "APPDATA", "HOME"}}
    env = {**keep, "PYTHONUNBUFFERED": "1", "APP_ENV": "test-logging-check",
           "DATABASE_URL": f"sqlite+aiosqlite:///{(work / 'server.db').as_posix()}",
           "STORAGE_PATH": str(work / "storage"), "ACCESS_CONTROL_FILE": str(registry),
           "STELLAR_ANCHOR_SECRET_FILE": str(work / "no-anchor-account.json"), "AI_PROVIDER": "disabled",
           "ANTHROPIC_API_KEY": "", "HTTP_PROXY": closed, "HTTPS_PROXY": closed, "ALL_PROXY": closed,
           "NO_PROXY": "127.0.0.1,localhost"}
    log_path = work / "server.log"
    ids: dict = {}
    with open(log_path, "wb") as log:
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", str(ROOT), "--host", "127.0.0.1",
             "--port", str(port), "--workers", "1"], cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            base = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 30
            while True:
                try:
                    if httpx.get(f"{base}/health", timeout=2).json().get("environment") == "test-logging-check":
                        break
                except (httpx.HTTPError, ValueError):
                    pass
                if server.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("the disposable server did not start")
                time.sleep(0.2)
            with httpx.Client(base_url=base, timeout=30) as api:
                asset = api.post("/assets", headers=bearer(ADMIN_TOKEN), json=_MERIDIAN_ASSET).json()
                document = api.post(f"/assets/{asset['id']}/documents", headers=bearer(ISSUER_TOKEN),
                                    data={"document_type": "prospectus", "title": "Logging check (fictional)"},
                                    files={"file": ("doc.pdf", make_source_pdf(), "application/pdf")}).json()
                manual = api.post(f"/documents/{document['id']}/manual-extractions", headers=bearer(ISSUER_TOKEN),
                                  json={"asset_id": asset["id"], "version_id": document["current_version"]["id"],
                                        "claims": [{"field_name": "jurisdiction", "value": "New York law.",
                                                    "source_page": 1, "supporting_passage": JURISDICTION_QUOTE}]})
                assert manual.status_code == 201, manual.text
                ids["extraction"] = manual.json()["id"]
                denied = api.post("/assets", headers=bearer(LEGAL_TOKEN), json=_MERIDIAN_ASSET)       # WARNING
                assert denied.status_code == 403
                approval = api.post(f"/extractions/{ids['extraction']}/approvals", headers=bearer(ISSUER_TOKEN), json={})
                assert approval.status_code == 201, approval.text
                ids["approval"] = approval.json()["id"]
                ids["invalid_token"] = "not-a-real-token-0123456789abcdef"
                assert api.get("/assets", headers=bearer(ids["invalid_token"])).status_code == 401
                registry.write_text("{ not json", encoding="utf-8")                                    # ERROR
                assert api.get("/assets", headers=bearer(ISSUER_TOKEN)).status_code == 503
                registry.write_text(json.dumps(build_test_access_config()), encoding="utf-8")
                assert api.get("/assets", headers=bearer(ISSUER_TOKEN)).status_code == 200
        finally:
            server.terminate()
            server.wait(timeout=30)
    return log_path.read_text(encoding="utf-8", errors="replace"), ids


def test_a_real_uvicorn_server_writes_the_info_audit_events(tmp_path: Path):
    log, ids = run_disposable_server(tmp_path)
    events = [line for line in log.splitlines() if " rwa_security " in line or " rwa_passport " in line]
    assert events and all(re.match(LINE, line) for line in events), events

    def lines_with(fragment: str) -> list[str]:
        return [line for line in events if fragment in line]

    created = lines_with(f"extraction.manual_created extraction={ids['extraction']}")
    assert len(created) == 1 and " INFO rwa_security " in created[0]
    assert "user=alice-chen-01 role=issuer_reviewer fields=jurisdiction" in created[0]
    approved = lines_with(f"approval.created extraction={ids['extraction']} approval={ids['approval']}")
    assert len(approved) == 1 and " INFO rwa_security " in approved[0]
    # Startup events, once each: the configuration was applied exactly once
    assert len(lines_with("deployment.policies")) == 1 and len(lines_with("ai_provider.disabled")) == 1
    assert len(lines_with("access_config.loaded")) == 1
    # WARNING and ERROR stay visible
    denied = lines_with("authz.permission_denied user=bob-martinez-01")
    assert len(denied) == 1 and " WARNING rwa_security " in denied[0]
    assert any(" ERROR rwa_security access_config.invalid" in line for line in events)
    # uvicorn's own logging is unchanged: its startup line and its access log
    assert "Application startup complete" in log
    assert '"POST /assets HTTP/1.1" 201' in log
    # Nothing secret or confidential
    for value in (ADMIN_TOKEN, ISSUER_TOKEN, LEGAL_TOKEN, INST_KEY_ALPHA, ids["invalid_token"], JURISDICTION_QUOTE):
        assert value not in log
