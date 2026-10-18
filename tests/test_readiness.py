"""
Stage 10C-3B, T2 — readiness probe GET /ready (app/routers/health.py).

Coverage:
 1. Database, access registry and storage usable → 200 {"status": "ready"}
 2. Each required component failing → 503 {"status": "not_ready"}: a missing or corrupt database
    file (never created by the probe), a slow database (bounded in time), a missing or invalid
    registry (and recovery without a restart), a missing, non-directory, unreadable or unwritable
    storage root (only read access is required in READ_ONLY_MODE)
 3. An unexpected exception → the same generic 503; the log names the component and exception
    type only, never its message; the answer never names a path or an issuer
 4. Repeated probes change no byte of the database, the registry or the storage
 5. /health is unchanged and stays 200 while the server is not ready; /ready ignores credentials,
    and the protected routes still refuse a request without them
 6. A real uvicorn server process (temporary database, storage and fictional registry): /ready
    follows the registry and the storage, and /health stays 200

Every test points the probe at a temporary database, registry and storage (the `ready_env`
fixture), never at the development database or the real access registry.
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.db import session as db_session
from app.routers import health
from app.services import storage as storage_module
from tests.conftest import (
    ISSUER_TOKEN,
    MERIDIAN_ISSUER,
    _MERIDIAN_ASSET,
    bearer,
    build_test_access_config,
)
from tests.test_logging_config import ROOT, _free_port

READY = {"status": "ready"}
NOT_READY = {"status": "not_ready"}


class ReadyEnv:
    def __init__(self, tmp_path: Path) -> None:
        # Names of their own: the `client` fixture creates tmp_path/storage for the other routes
        self.database = tmp_path / "ready.db"
        self.registry = tmp_path / "ready-access-control.json"
        self.storage = tmp_path / "ready-storage"


@pytest.fixture
async def ready_env(tmp_path: Path, monkeypatch):
    """A usable temporary database file, the fictional registry and an empty storage root."""
    env = ReadyEnv(tmp_path)
    with contextlib.closing(sqlite3.connect(env.database)) as connection:
        connection.execute("CREATE TABLE marker (id INTEGER PRIMARY KEY)")
        connection.commit()
    env.registry.write_text(json.dumps(build_test_access_config()), encoding="utf-8")
    env.storage.mkdir()
    engine = create_async_engine(f"sqlite+aiosqlite:///{env.database.as_posix()}")
    monkeypatch.setattr(db_session, "engine", engine)
    monkeypatch.setattr(settings, "access_control_file", str(env.registry))
    monkeypatch.setattr(settings, "storage_path", str(env.storage))
    yield env
    await engine.dispose()


def use_database(monkeypatch, path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
    monkeypatch.setattr(db_session, "engine", engine)
    return engine


def readiness_warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("readiness.not_ready")]


async def assert_not_ready(client: AsyncClient, caplog, failure: str, patches: dict | None = None) -> None:
    """`patches` (module, name) → value apply during the request only (e.g. functions of os)."""
    with caplog.at_level(logging.WARNING, logger="rwa_security"), pytest.MonkeyPatch.context() as m:
        for (target, name), value in (patches or {}).items():
            m.setattr(target, name, value)
        r = await client.get("/ready")
    assert r.status_code == 503
    assert r.json() == NOT_READY
    (message,) = readiness_warnings(caplog)
    assert message == f"readiness.not_ready failed={failure}"
    caplog.clear()


# ── 1. Ready ──────────────────────────────────────────────────────────────────


async def test_ready_when_the_database_the_registry_and_the_storage_are_usable(client: AsyncClient, ready_env, caplog):
    with caplog.at_level(logging.WARNING, logger="rwa_security"):
        r = await client.get("/ready")
    assert r.status_code == 200
    assert r.json() == READY
    assert readiness_warnings(caplog) == []
    assert r.headers["cache-control"] == "no-store"          # the security headers apply as everywhere


# ── 2. Required components ────────────────────────────────────────────────────


async def test_a_missing_database_file_is_not_ready_and_is_not_created(client: AsyncClient, ready_env, monkeypatch, caplog):
    missing = ready_env.database.with_name("missing.db")
    engine = use_database(monkeypatch, missing)
    try:
        await assert_not_ready(client, caplog, "database:database_file_missing")
    finally:
        await engine.dispose()
    assert not missing.exists()


async def test_a_corrupt_database_file_is_not_ready_and_is_left_untouched(client: AsyncClient, ready_env, monkeypatch, caplog):
    corrupt = ready_env.database.with_name("corrupt.db")
    corrupt.write_bytes(b"not an sqlite database " * 100)
    before = corrupt.read_bytes()
    engine = use_database(monkeypatch, corrupt)
    try:
        await assert_not_ready(client, caplog, "database:DatabaseError")
    finally:
        await engine.dispose()
    assert corrupt.read_bytes() == before


async def test_a_database_that_does_not_answer_in_time_is_not_ready(client: AsyncClient, ready_env, monkeypatch, caplog):
    async def hanging_database() -> None:
        await asyncio.sleep(30)

    monkeypatch.setattr(health, "_check_database", hanging_database)
    monkeypatch.setattr(health, "READINESS_TIMEOUT_SECONDS", 0.2)
    started = time.monotonic()
    await assert_not_ready(client, caplog, "database:timeout")
    assert time.monotonic() - started < 5


async def test_a_missing_registry_is_not_ready(client: AsyncClient, ready_env, caplog):
    ready_env.registry.unlink()
    await assert_not_ready(client, caplog, "access_registry:missing")


@pytest.mark.parametrize("content", [
    "{ not json",
    json.dumps({"issuers": [{"id": "Not A Valid Id"}], "users": [], "institutional_clients": []}),
])
async def test_an_invalid_registry_is_not_ready_until_it_is_repaired(client: AsyncClient, ready_env, caplog, content):
    ready_env.registry.write_text(content, encoding="utf-8")
    await assert_not_ready(client, caplog, "access_registry:invalid")
    ready_env.registry.write_text(json.dumps(build_test_access_config()), encoding="utf-8")
    r = await client.get("/ready")                              # no restart needed
    assert (r.status_code, r.json()) == (200, READY)


async def test_a_missing_storage_root_is_not_ready_and_is_not_created(client: AsyncClient, ready_env, caplog):
    ready_env.storage.rmdir()
    await assert_not_ready(client, caplog, "storage:missing")
    assert not ready_env.storage.exists()


async def test_a_storage_root_that_is_a_file_is_not_ready(client: AsyncClient, ready_env, caplog):
    ready_env.storage.rmdir()
    ready_env.storage.write_bytes(b"")
    await assert_not_ready(client, caplog, "storage:not_a_directory")


def _denied(*args, **kwargs):
    raise PermissionError("access denied")


def _write_denied(path, mode, **kwargs) -> bool:
    return not mode & os.W_OK


async def test_an_unreadable_storage_root_is_not_ready(client: AsyncClient, ready_env, caplog):
    # Permissions cannot be withdrawn portably in a test: listing the root fails as if denied
    await assert_not_ready(client, caplog, "storage:not_readable", {(storage_module.os, "scandir"): _denied})


async def test_an_unwritable_storage_root_is_ready_only_in_read_only_mode(client: AsyncClient, ready_env, monkeypatch, caplog):
    write_denied = {(storage_module.os, "access"): _write_denied}
    await assert_not_ready(client, caplog, "storage:not_writable", write_denied)
    monkeypatch.setattr(settings, "read_only_mode", True)      # a showcase only reads its documents
    with pytest.MonkeyPatch.context() as m:
        m.setattr(storage_module.os, "access", _write_denied)
        r = await client.get("/ready")
    assert (r.status_code, r.json()) == (200, READY)


async def test_every_failing_component_is_logged(client: AsyncClient, ready_env, caplog):
    ready_env.registry.unlink()
    ready_env.storage.rmdir()
    await assert_not_ready(client, caplog, "access_registry:missing,storage:missing")


# ── 3. Unexpected failures ────────────────────────────────────────────────────


async def test_an_unexpected_exception_gives_the_generic_503_and_reveals_nothing(client: AsyncClient, ready_env, monkeypatch, caplog):
    secret_detail = f"internal detail: /srv/private/access_control.json issuer={MERIDIAN_ISSUER} token={ISSUER_TOKEN}"

    def broken_registry():
        raise RuntimeError(secret_detail)

    monkeypatch.setattr(health, "get_access_registry", broken_registry)
    with caplog.at_level(logging.DEBUG):
        r = await client.get("/ready")
    assert r.status_code == 503
    assert r.json() == NOT_READY
    for value in ("/srv/private", MERIDIAN_ISSUER, ISSUER_TOKEN, str(ready_env.storage), str(ready_env.database)):
        assert value not in r.text
        assert all(value not in record.getMessage() for record in caplog.records)
    assert readiness_warnings(caplog) == ["readiness.not_ready failed=access_registry:RuntimeError"]


async def test_no_answer_names_a_path_or_an_issuer(client: AsyncClient, ready_env, caplog):
    ready_env.registry.write_text("{ not json", encoding="utf-8")
    ready_env.storage.rmdir()
    r = await client.get("/ready")
    assert r.status_code == 503 and r.content == b'{"status":"not_ready"}'


# ── 4. No side effects ────────────────────────────────────────────────────────


def _snapshot(*roots: Path) -> dict:
    files = {}
    for root in roots:
        for path in [root, *root.rglob("*")] if root.is_dir() else [root]:
            stat = path.stat()
            content = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "dir"
            files[str(path)] = (content, stat.st_size, stat.st_mtime_ns)
    return files


async def test_repeated_probes_change_nothing(client: AsyncClient, ready_env):
    document = ready_env.storage / "00000000-0000-0000-0000-000000000000" / "v1" / ("0" * 32 + ".pdf")
    document.parent.mkdir(parents=True)
    document.write_bytes(b"%PDF-1.4 fictional")
    before = _snapshot(ready_env.database.parent)
    for _ in range(25):
        r = await client.get("/ready")
        assert (r.status_code, r.json()) == (200, READY)
    assert _snapshot(ready_env.database.parent) == before


# ── 5. /health, credentials and the security model ────────────────────────────


async def test_health_is_unchanged_and_stays_live_while_not_ready(client: AsyncClient, ready_env, monkeypatch):
    expected = {"status": "ok", "version": settings.app_version, "environment": settings.app_env,
                "read_only": False, "synthetic_data": False, "api_docs": True}
    assert (await client.get("/health")).json() == expected
    engine = use_database(monkeypatch, ready_env.database.with_name("missing.db"))
    try:
        ready_env.registry.unlink()
        assert (await client.get("/ready")).status_code == 503
        r = await client.get("/health")
    finally:
        await engine.dispose()
    assert (r.status_code, r.json()) == (200, expected)


async def test_readiness_ignores_credentials(client: AsyncClient, ready_env):
    for headers in ({}, bearer(ISSUER_TOKEN), bearer("not-a-token"), {"X-API-Key": "not-a-key"}):
        r = await client.get("/ready", headers=headers)
        assert (r.status_code, r.json()) == (200, READY)


async def test_protected_routes_still_require_credentials(client: AsyncClient, ready_env):
    assert (await client.get("/assets")).status_code == 403
    assert (await client.get("/assets", headers=bearer("not-a-token"))).status_code == 401
    assert (await client.get("/assets", headers=bearer(ISSUER_TOKEN))).status_code == 200


async def test_readiness_is_documented_in_the_openapi_schema(client: AsyncClient):
    operation = (await client.get("/openapi.json")).json()["paths"]["/ready"]["get"]
    assert set(operation["responses"]) >= {"200", "503"}
    assert "security" not in operation


# ── 6. A real uvicorn server process ──────────────────────────────────────────


def test_a_real_uvicorn_server_reports_readiness(tmp_path: Path):
    """
    `python -m uvicorn app.main:app` in a temporary directory (no .env), with a temporary database,
    storage and the fictional registry, AI disabled and outbound HTTP pointed at a closed port.
    """
    registry, storage = tmp_path / "access_control.json", tmp_path / "storage"
    registry.write_text(json.dumps(build_test_access_config()), encoding="utf-8")
    storage.mkdir()
    port, closed = _free_port(), f"http://127.0.0.1:{_free_port()}"
    keep = {k: v for k, v in os.environ.items()
            if k.upper() in {"PATH", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
                              "PATHEXT", "COMSPEC", "WINDIR", "LOCALAPPDATA", "APPDATA", "HOME"}}
    env = {**keep, "PYTHONUNBUFFERED": "1", "APP_ENV": "readiness-check",
           "DATABASE_URL": f"sqlite+aiosqlite:///{(tmp_path / 'server.db').as_posix()}",
           "STORAGE_PATH": str(storage), "ACCESS_CONTROL_FILE": str(registry),
           "STELLAR_ANCHOR_SECRET_FILE": str(tmp_path / "no-anchor-account.json"), "AI_PROVIDER": "disabled",
           "ANTHROPIC_API_KEY": "", "HTTP_PROXY": closed, "HTTPS_PROXY": closed, "ALL_PROXY": closed,
           "NO_PROXY": "127.0.0.1,localhost"}
    log_path = tmp_path / "server.log"
    observed = []
    with open(log_path, "wb") as log:
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", str(ROOT), "--host", "127.0.0.1",
             "--port", str(port), "--workers", "1"], cwd=tmp_path, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            base = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 30
            while True:
                try:
                    if httpx.get(f"{base}/health", timeout=2).json().get("environment") == "readiness-check":
                        break
                except (httpx.HTTPError, ValueError):
                    pass
                if server.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("the disposable server did not start")
                time.sleep(0.2)
            with httpx.Client(base_url=base, timeout=10) as api:
                def probe(step: str) -> None:
                    ready, live = api.get("/ready"), api.get("/health")
                    observed.append((step, ready.status_code, ready.json(), live.status_code))

                probe("started")
                assert api.post("/assets", headers=bearer(ISSUER_TOKEN), json=_MERIDIAN_ASSET).status_code == 403
                registry.write_text("{ not json", encoding="utf-8")
                probe("registry invalid")
                registry.write_text(json.dumps(build_test_access_config()), encoding="utf-8")
                probe("registry repaired")
                storage.rmdir()
                probe("storage missing")
                storage.mkdir()
                probe("storage restored")
        finally:
            server.terminate()
            server.wait(timeout=30)
    assert observed == [
        ("started", 200, READY, 200),
        ("registry invalid", 503, NOT_READY, 200),
        ("registry repaired", 200, READY, 200),
        ("storage missing", 503, NOT_READY, 200),
        ("storage restored", 200, READY, 200),
    ]
    text = log_path.read_text(encoding="utf-8", errors="replace")
    assert text.count("readiness.not_ready failed=access_registry:invalid") == 1
    assert text.count("readiness.not_ready failed=storage:missing") == 1
    assert '"GET /ready HTTP/1.1" 503' in text and '"GET /ready HTTP/1.1" 200' in text
    assert str(storage) not in "\n".join(line for line in text.splitlines() if "readiness." in line)
    assert ISSUER_TOKEN not in text
