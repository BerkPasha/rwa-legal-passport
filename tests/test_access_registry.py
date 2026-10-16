"""
Stage 9B-1 — Tests for the access-configuration loader, the helper tool and the schema check.

Rules verified:
- The configuration contains only digests; a plaintext token field is rejected.
- Faulty / ambiguous configuration (unknown issuer, wildcard, duplicate digest,
  reviewer or client without scope) is rejected, and the error text does not
  include input values.
- If the file is missing the registry is empty (fail-closed); if it is corrupt, 503;
  when it changes it is reloaded.
- The example file in the repository matches the schema but cannot be used as is.
- With an old-schema database the application refuses to start; no data is modified.
"""

import copy
import json
import os
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app import access_tool
from app.access_registry import (
    EMPTY_REGISTRY,
    AccessConfigError,
    AccessRegistry,
    get_access_registry,
    hash_token,
    load_access_registry,
)
from app.config import settings
from app.db.base import Base
from app.db.schema_check import SchemaOutOfDateError, verify_schema_columns
from app.enums import UserRole
from tests.conftest import ADMIN_TOKEN, ISSUER_TOKEN, build_test_access_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _write(path: Path, data) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ── Configuration validation ──────────────────────────────────────────────────


def test_valid_config_builds_hash_keyed_registry():
    registry = AccessRegistry.from_dict(build_test_access_config())
    principal = registry.authenticate_user(ISSUER_TOKEN)
    assert principal is not None and principal.user_id == "alice-chen-01"
    assert principal.role == UserRole.issuer_reviewer
    # The registry contains no plaintext tokens; the keys are digests
    assert ISSUER_TOKEN not in registry.users_by_token_hash
    assert hash_token(ISSUER_TOKEN) in registry.users_by_token_hash
    assert ISSUER_TOKEN not in repr(registry)
    # Users with enabled=false are not added to the registry
    assert registry.authenticate_user("test-disabled-token") is None
    assert registry.authenticate_user("") is None


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda c: c["users"][1].update(token="plaintext-token-value"), "Extra inputs"),
        (lambda c: c["users"][1].update(issuer_ids=["ghost-issuer"]), "unknown issuer"),
        (lambda c: c["users"][1].update(issuer_ids=["*"]), "issuer_ids"),
        (lambda c: c["users"][1].update(issuer_ids=[]), "at least one issuer_ids entry"),
        (lambda c: c["users"][0].update(issuer_ids=["meridian-property"]), "global scope"),
        (lambda c: c["users"][1].update(role="superuser"), "role"),
        (lambda c: c["users"][1].update(token_sha256="not-a-sha256"), "token_sha256"),
        (lambda c: c["users"][2].update(token_sha256=c["users"][1]["token_sha256"]), "must be unique"),
        (lambda c: c["institutional_clients"][0].update(key_sha256=c["users"][0]["token_sha256"]), "must be unique"),
        (lambda c: c["users"].append(dict(c["users"][1], token_sha256="a" * 64)), "user_id"),
        (lambda c: c["institutional_clients"][0].update(issuer_ids=[], asset_ids=[]), "at least one issuer_ids or asset_ids"),
        (lambda c: c["institutional_clients"][0].update(issuer_ids=["ghost-issuer"]), "unknown issuer"),
        (lambda c: c["institutional_clients"][0].update(operations=[]), "operations"),
        (lambda c: c["institutional_clients"][0].update(operations=["passport:write"]), "operations"),
        (lambda c: c["issuers"].append({"id": "Bad Issuer!", "name": "x"}), "issuers"),
    ],
)
def test_invalid_configs_are_rejected_without_echoing_values(mutate, expected):
    config = build_test_access_config()
    mutate(config)
    with pytest.raises(AccessConfigError) as exc_info:
        AccessRegistry.from_dict(config)
    message = str(exc_info.value)
    assert expected in message
    # The error text does not contain digest / token values
    assert "plaintext-token-value" not in message
    for user in config["users"]:
        assert user.get("token_sha256", "#") not in message


def test_invalid_json_file_is_rejected(tmp_path: Path):
    bad = tmp_path / "access.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(AccessConfigError, match="JSON"):
        load_access_registry(bad)


# ── Runtime loading (fail-closed, reload) ─────────────────────────────────────


def test_missing_file_yields_empty_registry(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "access_control_file", str(tmp_path / "missing.json"))
    registry = get_access_registry()
    assert registry is EMPTY_REGISTRY
    assert registry.authenticate_user(ADMIN_TOKEN) is None


def test_corrupt_file_fails_closed_with_503(tmp_path: Path, monkeypatch):
    path = tmp_path / "access.json"
    path.write_text('{"issuers": "oops"}', encoding="utf-8")
    monkeypatch.setattr(settings, "access_control_file", str(path))
    with pytest.raises(HTTPException) as exc_info:
        get_access_registry()
    assert exc_info.value.status_code == 503
    assert "oops" not in str(exc_info.value.detail)


def test_registry_reloads_when_file_changes(tmp_path: Path, monkeypatch):
    """Setting a user to enabled=false revokes access without a restart."""
    config = build_test_access_config()
    path = _write(tmp_path / "access.json", config)
    monkeypatch.setattr(settings, "access_control_file", str(path))
    assert get_access_registry().authenticate_user(ISSUER_TOKEN) is not None

    revoked = copy.deepcopy(config)
    revoked["users"][1]["enabled"] = False
    _write(path, revoked)
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert get_access_registry().authenticate_user(ISSUER_TOKEN) is None


# ── Example file in the repository ────────────────────────────────────────────


def test_example_file_matches_schema_but_is_not_usable_as_is():
    example_path = PROJECT_ROOT / "access_control.example.json"
    raw = json.loads(example_path.read_text(encoding="utf-8"))
    # As is: the placeholder digests are invalid → fail-closed
    with pytest.raises(AccessConfigError):
        AccessRegistry.from_dict(raw)
    # Once the placeholders are replaced with real digests, the schema is valid
    for i, user in enumerate(raw["users"]):
        user["token_sha256"] = hash_token(f"example-user-token-{i}")
    for i, client in enumerate(raw["institutional_clients"]):
        client["key_sha256"] = hash_token(f"example-client-key-{i}")
    registry = AccessRegistry.from_dict(raw)
    assert len(registry.users_by_token_hash) == len(raw["users"])


def test_real_access_file_is_gitignored():
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "access_control.json" in gitignore


# ── Helper tool (python -m app.access_tool) ───────────────────────────────────


def test_tool_new_token_prints_matching_hash(capsys):
    assert access_tool.main(["new-token"]) == 0
    lines = [l.strip() for l in capsys.readouterr().out.splitlines() if l.startswith("  ")]
    token, digest = lines
    assert len(token) >= 40
    assert digest == hash_token(token)


def test_tool_hash_env_reads_env_file_without_echoing_token(tmp_path: Path, capsys, monkeypatch):
    secret = "migrated-legacy-token-0123456789abcdefXYZ"
    env_file = tmp_path / ".env"
    env_file.write_text(f"REVIEWER_LEGAL_TOKEN={secret}\n", encoding="utf-8")
    monkeypatch.delenv("REVIEWER_LEGAL_TOKEN", raising=False)
    assert access_tool.main(["hash-env", "REVIEWER_LEGAL_TOKEN", "--env-file", str(env_file)]) == 0
    out = capsys.readouterr().out
    assert out.strip() == hash_token(secret)
    assert secret not in out


def test_tool_hash_env_rejects_short_or_missing_values(tmp_path: Path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("SHORT_TOKEN=abc\n", encoding="utf-8")
    monkeypatch.delenv("SHORT_TOKEN", raising=False)
    monkeypatch.delenv("ABSENT_TOKEN", raising=False)
    assert access_tool.main(["hash-env", "SHORT_TOKEN", "--env-file", str(env_file)]) == 2
    assert access_tool.main(["hash-env", "ABSENT_TOKEN", "--env-file", str(env_file)]) == 2


def test_tool_check_reports_summary_without_hashes(tmp_path: Path, capsys):
    config = build_test_access_config()
    path = _write(tmp_path / "access.json", config)
    assert access_tool.main(["check", "--file", str(path)]) == 0
    out = capsys.readouterr().out
    assert "issuers=4" in out and "platform_admin=1" in out
    assert config["users"][0]["token_sha256"] not in out

    broken = copy.deepcopy(config)
    broken["users"][1]["issuer_ids"] = ["ghost-issuer"]
    _write(path, broken)
    assert access_tool.main(["check", "--file", str(path)]) == 1


# ── Startup schema check ──────────────────────────────────────────────────────


async def test_schema_check_rejects_pre_9b1_assets_table():
    """An old 'assets' table without the issuer_id column → startup is refused, data is not changed."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE assets (id VARCHAR(36) PRIMARY KEY, blockchain_network VARCHAR(50), "
            "stellar_asset_code VARCHAR(12), stellar_issuer_public_key VARCHAR(56), "
            "issuer_organization_name VARCHAR(255), asset_type VARCHAR(50), "
            "jurisdiction VARCHAR(100), status VARCHAR(20), created_at DATETIME, updated_at DATETIME)"
        ))
        await conn.execute(text("INSERT INTO assets (id) VALUES ('legacy-row')"))
        await conn.run_sync(Base.metadata.create_all)
        with pytest.raises(SchemaOutOfDateError, match=r"assets\(issuer_id, material_revision\)"):
            await conn.run_sync(verify_schema_columns)
        rows = (await conn.execute(text("SELECT id FROM assets"))).all()
    await engine.dispose()
    assert rows == [("legacy-row",)]


async def test_schema_check_accepts_current_schema():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(verify_schema_columns)
    await engine.dispose()


def test_legacy_credentials_are_reported_by_name_only(monkeypatch):
    monkeypatch.setattr(settings, "reviewer_issuer_token", "x" * 40)
    monkeypatch.setattr(settings, "institutional_api_key_beta", "")
    names = settings.legacy_credentials_configured
    assert "REVIEWER_ISSUER_TOKEN" in names
    assert "INSTITUTIONAL_API_KEY_BETA" not in names
