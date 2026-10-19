"""
Stage 10C-3B, T3 — deployment profiles and the startup checks of staging and production
(app/deployment_checks.py, the lifespan in app/main.py, DEPLOYMENT.md §8).

Coverage:
 A. The development defaults and the test profile have no checks; the suite's own settings pass
 B. A test-profile server starts with temporary resources (the lifespan, in-process)
 C. Staging and production refuse the per-client limits off or above their ceilings
 D. They refuse an unset, plain-http or loopback CORS allowlist (as each profile defines it);
    wildcards and malformed origins are refused by Settings in every profile, as before
 E. Production refuses the API docs; staging refuses them unless enabled explicitly
 F. A safe staging or production configuration starts (the real lifespan, temporary resources);
    an unsafe one is refused before the registry or the database is touched
 G. An unrecognized APP_ENV is refused instead of running as development
 H. READ_ONLY_MODE changes no rule
 I. A refusal names settings and rules only: no origin, path, key, token or APP_ENV value
 J. The checks are deterministic and change neither the settings nor any file
 K. Real uvicorn processes on 127.0.0.1: unsafe and unrecognized configurations exit before
    serving; safe staging and production servers start, and /health and /ready answer as before
"""

import importlib.util
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from app import main
from app.config import Settings, settings
from app.db.session import create_database_engine
from app.deployment_checks import (
    AUTH_FAILURE_LIMIT_RANGE,
    CLIENT_RATE_LIMIT_RANGE,
    UnsafeDeploymentConfiguration,
    configuration_problems,
    deployment_profile,
    ensure_safe_configuration,
)
from tests.conftest import ADMIN_TOKEN, build_test_access_config
from tests.test_logging_config import ROOT, _free_port

# Settings of a publicly reachable read-only showcase (scripts/build_showcase.py) and of a private
# staging server; every other setting keeps its default
SAFE_PRODUCTION = {"app_env": "production", "api_docs_enabled": False, "cors_allowed_origins": "",
                   "client_rate_limit_per_minute": 300, "auth_failure_limit_per_minute": 10}
SAFE_STAGING = {"app_env": "staging", "api_docs_enabled": False,
                "cors_allowed_origins": "https://staging.example.org",
                "client_rate_limit_per_minute": 300, "auth_failure_limit_per_minute": 10}
_CHECKED_VARIABLES = ("APP_ENV", "API_DOCS_ENABLED", "CORS_ALLOWED_ORIGINS", "CLIENT_RATE_LIMIT_PER_MINUTE",
                      "AUTH_FAILURE_LIMIT_PER_MINUTE", "READ_ONLY_MODE")


def _showcase_settings() -> dict[str, str]:
    """SHOWCASE_SETTINGS of scripts/build_showcase.py: the showcase.env of the deployable bundle."""
    spec = importlib.util.spec_from_file_location("build_showcase", ROOT / "scripts" / "build_showcase.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SHOWCASE_SETTINGS


@pytest.fixture(autouse=True)
def no_inherited_settings(monkeypatch):
    """Only the values a test passes count as configured (no variable of this process applies)."""
    for name in _CHECKED_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def configured(**values) -> Settings:
    """Settings as a server would load them, with exactly these variables set (no .env file)."""
    return Settings(_env_file=None, **values)


def problem_settings(**values) -> list[str]:
    return [problem.split(":", 1)[0] for problem in configuration_problems(configured(**values))]


# ── A. Local profiles ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("app_env", ["development", "test", "development-demo", "test-e2e-synthetic"])
def test_the_local_profiles_keep_the_development_defaults(app_env):
    local = configured(app_env=app_env)
    assert (local.api_docs_enabled, local.client_rate_limit_per_minute, local.auth_failure_limit_per_minute) == (True, 0, 0)
    assert configuration_problems(local) == []
    assert ensure_safe_configuration(local) == app_env.split("-")[0]


def test_the_default_and_this_suites_own_settings_pass():
    assert configuration_problems(configured()) == []           # APP_ENV unset: development
    assert configuration_problems(settings) == []               # what the rest of the suite runs with


# ── C. Per-client limits ──────────────────────────────────────────────────────


@pytest.mark.parametrize("safe", [SAFE_STAGING, SAFE_PRODUCTION], ids=["staging", "production"])
@pytest.mark.parametrize("name, value", [
    ("client_rate_limit_per_minute", 0), ("client_rate_limit_per_minute", CLIENT_RATE_LIMIT_RANGE[1] + 1),
    ("auth_failure_limit_per_minute", 0), ("auth_failure_limit_per_minute", AUTH_FAILURE_LIMIT_RANGE[1] + 1),
])
def test_a_disabled_or_unbounded_client_limit_is_refused(safe, name, value):
    assert problem_settings(**{**safe, name: value}) == [name.upper()]


@pytest.mark.parametrize("safe", [SAFE_STAGING, SAFE_PRODUCTION], ids=["staging", "production"])
def test_the_limits_are_accepted_up_to_their_ceilings(safe):
    for requests in CLIENT_RATE_LIMIT_RANGE:
        for failures in AUTH_FAILURE_LIMIT_RANGE:
            values = {**safe, "client_rate_limit_per_minute": requests, "auth_failure_limit_per_minute": failures}
            assert problem_settings(**values) == [], values


def test_the_ceilings_are_above_the_rehearsed_showcase_values():
    """The showcase bundle passed the browser tests with 300 and 10 (scripts/build_showcase.py)."""
    showcase = _showcase_settings()
    assert CLIENT_RATE_LIMIT_RANGE[0] <= int(showcase["CLIENT_RATE_LIMIT_PER_MINUTE"]) <= CLIENT_RATE_LIMIT_RANGE[1]
    assert AUTH_FAILURE_LIMIT_RANGE[0] <= int(showcase["AUTH_FAILURE_LIMIT_PER_MINUTE"]) <= AUTH_FAILURE_LIMIT_RANGE[1]


# ── D. CORS ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("safe", [SAFE_STAGING, SAFE_PRODUCTION], ids=["staging", "production"])
def test_the_development_cors_default_is_refused(safe):
    values = {k: v for k, v in safe.items() if k != "cors_allowed_origins"}
    assert problem_settings(**values) == ["CORS_ALLOWED_ORIGINS"]


@pytest.mark.parametrize("origins, production_ok, staging_ok", [
    ("", True, True),                                            # same origin only
    ("https://app.example.org", True, True),
    ("https://app.example.org,https://admin.example.org:8443", True, True),
    ("http://app.example.org", False, False),
    ("http://192.168.1.20:5173", False, False),
    ("https://app.example.org,http://app.example.org", False, False),
    ("http://localhost:5173", False, True),                      # staging through a tunnel
    ("http://127.0.0.1:5173,http://[::1]:5173", False, True),
    ("https://localhost", False, True),
    ("https://127.0.0.5:8443", False, True),
    ("https://demo.localhost", False, True),
])
def test_cors_origins_by_profile(origins, production_ok, staging_ok):
    assert (problem_settings(**{**SAFE_PRODUCTION, "cors_allowed_origins": origins}) == []) is production_ok
    assert (problem_settings(**{**SAFE_STAGING, "cors_allowed_origins": origins}) == []) is staging_ok


@pytest.mark.parametrize("origins", ["*", "https://*.example.org", "https://app.example.org/path", "app.example.org"])
def test_wildcard_and_malformed_origins_are_refused_in_every_profile(origins):
    for app_env in ("development", "staging", "production"):
        with pytest.raises(ValidationError):
            configured(**{**SAFE_PRODUCTION, "app_env": app_env, "cors_allowed_origins": origins})


# ── E. API documentation ──────────────────────────────────────────────────────


def test_production_refuses_the_api_docs_whether_set_or_left_on():
    assert problem_settings(**{**SAFE_PRODUCTION, "api_docs_enabled": True}) == ["API_DOCS_ENABLED"]
    unset = {k: v for k, v in SAFE_PRODUCTION.items() if k != "api_docs_enabled"}
    assert problem_settings(**unset) == ["API_DOCS_ENABLED"]


def test_staging_shows_the_api_docs_only_when_enabled_explicitly():
    unset = {k: v for k, v in SAFE_STAGING.items() if k != "api_docs_enabled"}
    assert problem_settings(**unset) == ["API_DOCS_ENABLED"]
    assert problem_settings(**{**SAFE_STAGING, "api_docs_enabled": True}) == []
    assert problem_settings(**{**SAFE_STAGING, "api_docs_enabled": False}) == []


# ── G. APP_ENV ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("app_env", [
    "prod", "Production", "PRODUCTION", "live", "stage", "staging ", " production", "", "dev", "local",
    "showcase", "e2e-synthetic", "production-", "production_eu", "production-EU", "production--x",
    "productionx", "test-" + "x" * 64,
])
def test_an_unrecognized_app_env_is_refused(app_env):
    assert deployment_profile(app_env) is None
    unsafe = configured(**{**SAFE_PRODUCTION, "app_env": app_env})
    assert [p.split(":", 1)[0] for p in configuration_problems(unsafe)] == ["APP_ENV"]
    with pytest.raises(UnsafeDeploymentConfiguration) as refusal:
        ensure_safe_configuration(unsafe)
    assert refusal.value.profile == "unrecognized" and refusal.value.settings == ["APP_ENV"]


@pytest.mark.parametrize("app_env, profile", [
    ("development", "development"), ("test", "test"), ("staging", "staging"), ("production", "production"),
    ("production-showcase", "production"), ("staging-private-demo", "staging"), ("test-e2e-demo-offline", "test"),
])
def test_accepted_app_env_values(app_env, profile):
    assert deployment_profile(app_env) == profile


def test_a_label_never_turns_a_deployment_profile_into_a_local_one():
    assert problem_settings(**{**SAFE_PRODUCTION, "app_env": "production-development", "client_rate_limit_per_minute": 0}) \
        == ["CLIENT_RATE_LIMIT_PER_MINUTE"]


# ── H. READ_ONLY_MODE ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("unsafe", [
    {**SAFE_PRODUCTION, "client_rate_limit_per_minute": 0, "auth_failure_limit_per_minute": 0},
    {**SAFE_PRODUCTION, "api_docs_enabled": True},
    {**SAFE_PRODUCTION, "cors_allowed_origins": "http://app.example.org"},
    {**SAFE_STAGING, "client_rate_limit_per_minute": 5000},
])
def test_read_only_mode_bypasses_no_rule(unsafe):
    writable = problem_settings(**unsafe, read_only_mode=False)
    assert writable and problem_settings(**unsafe, read_only_mode=True) == writable


def test_the_showcase_bundle_settings_pass_the_production_checks():
    variables = {k.lower(): v for k, v in _showcase_settings().items()}
    showcase = configured(**variables)
    assert deployment_profile(showcase.app_env) == "production" and showcase.read_only_mode is True
    assert configuration_problems(showcase) == []


# ── I. Nothing confidential in a refusal ──────────────────────────────────────


def test_a_refusal_names_settings_and_rules_only(caplog):
    confidential = ["https://internal-portal.example.org", "http://10.20.30.40:5173",
                    "sk-ant-fictional-0123456789abcdefghij", "/srv/private/access_control.json",
                    "/srv/private/storage", "S3cr3tDbPassword", ADMIN_TOKEN]
    unsafe = configured(app_env="production", api_docs_enabled=True,
                        cors_allowed_origins="https://internal-portal.example.org,http://10.20.30.40:5173",
                        anthropic_api_key=confidential[2], access_control_file=confidential[3],
                        storage_path=confidential[4],
                        database_url=f"postgresql+asyncpg://passport:{confidential[5]}@db.internal/passport",
                        reviewer_issuer_token=ADMIN_TOKEN)
    with pytest.raises(UnsafeDeploymentConfiguration) as refusal:
        ensure_safe_configuration(unsafe)
    message = str(refusal.value)
    assert refusal.value.settings == ["API_DOCS_ENABLED", "AUTH_FAILURE_LIMIT_PER_MINUTE", "CLIENT_RATE_LIMIT_PER_MINUTE",
                                      "CORS_ALLOWED_ORIGINS"]
    assert "origin 2 must be an https origin" in message
    for value in confidential + ["internal-portal", "10.20.30.40", "db.internal"]:
        assert value not in message, value


def test_an_unrecognized_app_env_is_not_repeated():
    with pytest.raises(UnsafeDeploymentConfiguration) as refusal:
        ensure_safe_configuration(configured(app_env="live-x7q-secret"))
    assert "x7q" not in str(refusal.value) and "live" not in str(refusal.value)


# ── J. Deterministic, without side effects ────────────────────────────────────


def test_the_checks_are_deterministic_and_change_no_setting():
    for values in (SAFE_PRODUCTION, {**SAFE_STAGING, "api_docs_enabled": True},
                   {**SAFE_PRODUCTION, "client_rate_limit_per_minute": 0}, {"app_env": "prod"}):
        subject = configured(**values)
        before = (subject.model_dump(), set(subject.model_fields_set))
        results = [configuration_problems(subject) for _ in range(5)]
        assert all(result == results[0] for result in results)
        assert (subject.model_dump(), set(subject.model_fields_set)) == before
    before = (settings.model_dump(), set(settings.model_fields_set))
    configuration_problems(settings)
    assert (settings.model_dump(), set(settings.model_fields_set)) == before


# ── F. The real startup (lifespan), in-process ────────────────────────────────


def _lifespan_resources(tmp_path: Path, **values):
    registry, storage, database = tmp_path / "access_control.json", tmp_path / "storage", tmp_path / "server.db"
    registry.write_text(json.dumps(build_test_access_config()), encoding="utf-8")
    storage.mkdir()
    subject = configured(**values, access_control_file=str(registry), storage_path=str(storage),
                         database_url=f"sqlite+aiosqlite:///{database.as_posix()}",
                         stellar_anchor_secret_file=str(tmp_path / "none.json"), ai_provider="disabled")
    # Made like the application's engine (app/db/session.py): its startup refuses one without foreign keys
    engine = create_database_engine(subject.database_url, busy_timeout_seconds=subject.sqlite_busy_timeout_seconds,
                                    journal_mode=subject.sqlite_journal_mode, read_only=subject.read_only_mode)
    return subject, engine, registry, database


@pytest.mark.parametrize("values, profile", [
    (SAFE_PRODUCTION, "production"), ({**SAFE_PRODUCTION, "read_only_mode": True}, "production"),
    ({**SAFE_STAGING, "api_docs_enabled": True, "cors_allowed_origins": "http://localhost:5173"}, "staging"),
    ({"app_env": "test-lifespan"}, "test"),
])
async def test_a_safe_configuration_starts(tmp_path, monkeypatch, caplog, values, profile):
    subject, engine, _, database = _lifespan_resources(tmp_path, **values)
    monkeypatch.setattr(main, "settings", subject)
    monkeypatch.setattr(main, "engine", engine)
    try:
        with caplog.at_level(logging.INFO, logger="rwa_security"):
            async with main.lifespan(main.app):
                pass
    finally:
        await engine.dispose()
    checks = "passed" if profile in ("staging", "production") else "not_applicable"
    assert f"deployment.profile profile={profile} checks={checks}" in caplog.text
    assert "access_config.loaded" in caplog.text and database.is_file()


async def test_an_unsafe_configuration_is_refused_before_anything_is_read_or_created(tmp_path, monkeypatch, caplog):
    subject, engine, registry, database = _lifespan_resources(tmp_path, **{**SAFE_PRODUCTION, "client_rate_limit_per_minute": 0})
    registry.write_text("{ not json", encoding="utf-8")         # would fail differently if it were read first
    monkeypatch.setattr(main, "settings", subject)
    monkeypatch.setattr(main, "engine", engine)
    try:
        with caplog.at_level(logging.INFO, logger="rwa_security"), pytest.raises(UnsafeDeploymentConfiguration):
            async with main.lifespan(main.app):
                pass                                            # pragma: no cover
    finally:
        await engine.dispose()
    assert not database.exists()
    assert registry.read_text(encoding="utf-8") == "{ not json"
    assert "deployment.configuration_refused profile=production settings=CLIENT_RATE_LIMIT_PER_MINUTE" in caplog.text
    assert "access_config" not in caplog.text and "deployment.profile" not in caplog.text


# ── K. Real uvicorn processes ─────────────────────────────────────────────────


def _server(tmp_path: Path, port: int, **variables) -> tuple[list[str], dict]:
    registry, storage = tmp_path / "access_control.json", tmp_path / "storage"
    registry.write_text(json.dumps(build_test_access_config()), encoding="utf-8")
    storage.mkdir(exist_ok=True)
    keep = {k: v for k, v in os.environ.items()
            if k.upper() in {"PATH", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
                              "PATHEXT", "COMSPEC", "WINDIR", "LOCALAPPDATA", "APPDATA", "HOME"}}
    closed = f"http://127.0.0.1:{_free_port()}"
    env = {**keep, "PYTHONUNBUFFERED": "1",
           "DATABASE_URL": f"sqlite+aiosqlite:///{(tmp_path / 'server.db').as_posix()}",
           "STORAGE_PATH": str(storage), "ACCESS_CONTROL_FILE": str(registry),
           "STELLAR_ANCHOR_SECRET_FILE": str(tmp_path / "no-anchor-account.json"), "AI_PROVIDER": "disabled",
           "ANTHROPIC_API_KEY": "", "HTTP_PROXY": closed, "HTTPS_PROXY": closed, "ALL_PROXY": closed,
           "NO_PROXY": "127.0.0.1,localhost", **variables}
    command = [sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", str(ROOT), "--host", "127.0.0.1",
               "--port", str(port), "--workers", "1"]
    return command, env


@pytest.mark.parametrize("variables, refused", [
    ({"APP_ENV": "production"},                       # every development default
     ["API_DOCS_ENABLED", "CORS_ALLOWED_ORIGINS", "CLIENT_RATE_LIMIT_PER_MINUTE", "AUTH_FAILURE_LIMIT_PER_MINUTE"]),
    ({"APP_ENV": "staging", "READ_ONLY_MODE": "true", "API_DOCS_ENABLED": "false", "CORS_ALLOWED_ORIGINS": "",
      "CLIENT_RATE_LIMIT_PER_MINUTE": "0", "AUTH_FAILURE_LIMIT_PER_MINUTE": "10"}, ["CLIENT_RATE_LIMIT_PER_MINUTE"]),
    ({"APP_ENV": "live-x7q"}, ["APP_ENV"]),
], ids=["production-defaults", "read-only-staging-without-rate-limit", "unrecognized"])
def test_a_real_server_refuses_an_unsafe_configuration(tmp_path, variables, refused):
    port = _free_port()
    command, env = _server(tmp_path, port, **variables)
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, timeout=60)
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode != 0
    assert "Refusing to start: unsafe configuration" in output and "Application startup failed" in output
    for name in refused:
        assert f"  - {name}:" in output
    assert "Uvicorn running" not in output and "Application startup complete" not in output
    assert not (tmp_path / "server.db").exists()                # refused before the database
    assert str(tmp_path) not in output and "x7q" not in output and ADMIN_TOKEN not in output
    with pytest.raises(httpx.HTTPError):
        httpx.get(f"http://127.0.0.1:{port}/health", timeout=1)


@pytest.mark.parametrize("variables, docs", [
    ({"APP_ENV": "staging-private-demo", "API_DOCS_ENABLED": "true", "CORS_ALLOWED_ORIGINS": "http://localhost:5173",
      "CLIENT_RATE_LIMIT_PER_MINUTE": "300", "AUTH_FAILURE_LIMIT_PER_MINUTE": "10"}, 200),
    ({"APP_ENV": "production-showcase", "READ_ONLY_MODE": "true", "SYNTHETIC_DATA_NOTICE": "true",
      "API_DOCS_ENABLED": "false", "CORS_ALLOWED_ORIGINS": "", "CLIENT_RATE_LIMIT_PER_MINUTE": "300",
      "AUTH_FAILURE_LIMIT_PER_MINUTE": "10"}, 404),
], ids=["private-staging", "production-read-only-showcase"])
def test_a_real_server_starts_with_a_safe_configuration(tmp_path, variables, docs):
    port = _free_port()
    command, env = _server(tmp_path, port, **variables)
    log_path = tmp_path / "server.log"
    with open(log_path, "wb") as log:
        server = subprocess.Popen(command, cwd=tmp_path, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            base, deadline = f"http://127.0.0.1:{port}", time.monotonic() + 30
            while True:
                try:
                    health = httpx.get(f"{base}/health", timeout=2)
                    break
                except httpx.HTTPError:
                    if server.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("the disposable server did not start") from None
                    time.sleep(0.2)
            ready = httpx.get(f"{base}/ready", timeout=5)
            api_docs = httpx.get(f"{base}/docs", timeout=5)
        finally:
            server.terminate()
            server.wait(timeout=30)
    assert health.status_code == 200 and health.json()["environment"] == variables["APP_ENV"]
    assert set(health.json()) == {"status", "version", "environment", "read_only", "synthetic_data", "api_docs"}
    assert (ready.status_code, ready.json()) == (200, {"status": "ready"})
    assert api_docs.status_code == docs
    profile = variables["APP_ENV"].split("-")[0]
    text = log_path.read_text(encoding="utf-8", errors="replace")
    assert f"deployment.profile profile={profile} checks=passed" in text
