"""
Stage 9B-3 migration — scripts/migrate_9b3_approval_integrity.py.

The starting point is the real pre-9B-3 schema of the development database
(tests/fixtures/schema_pre_9b3.sql), optionally filled with a legacy (unbound) approval.
"""

import hashlib
import importlib.util
import sqlite3
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.schema_check import SchemaOutOfDateError, verify_schema_columns
from app.db.session import get_db
from app.main import app
from app.services.pdf import extract_pages
from app.source_integrity import _pdf_limits
from tests.conftest import _MERIDIAN_ASSET, MERIDIAN_ISSUER
from tests.pdf_fixtures import make_text_pdf

_FIXTURE = Path(__file__).parent / "fixtures" / "schema_pre_9b3.sql"
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_9b3_approval_integrity.py"
_spec = importlib.util.spec_from_file_location("migrate_9b3", _SCRIPT)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)
# The next migration of the chain (Stage 9B-4 step 7)
_spec_9b4s7 = importlib.util.spec_from_file_location(
    "migrate_9b4s7", _SCRIPT.parent / "migrate_9b4_correction_evidence.py")
migration_9b4s7 = importlib.util.module_from_spec(_spec_9b4s7)
_spec_9b4s7.loader.exec_module(migration_9b4s7)

_TS = "2026-10-01 12:00:00.000000"
_NEW_APPROVAL_COLUMNS = (
    "document_id, document_version_id, document_version_number, document_sha256, evidence_snapshot, "
    "evidence_digest, asset_material_revision, asset_snapshot, asset_snapshot_digest, binding_digest"
)


def _legacy_db(path: Path, with_data: bool) -> Path:
    con = sqlite3.connect(path)
    con.executescript(_FIXTURE.read_text(encoding="utf-8"))
    if with_data:
        a = _MERIDIAN_ASSET
        con.execute(
            "INSERT INTO assets VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("asset-1", a["blockchain_network"], a["stellar_asset_code"], a["stellar_issuer_public_key"],
             MERIDIAN_ISSUER, a["issuer_organization_name"], a["asset_type"], a["jurisdiction"], "active", _TS, _TS),
        )
        con.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)",
                    ("doc-1", "asset-1", "prospectus", "Legacy Prospectus", "active", 1, _TS, _TS))
        con.execute("INSERT INTO document_versions VALUES (?,?,?,?,?,?,?,?)",
                    ("ver-1", "doc-1", 1, "legacy.pdf", "doc-1/v1/legacy.pdf", 10, "a" * 64, _TS))
        con.execute("INSERT INTO legal_extractions VALUES (?,?,?,?,?,?,?,?)",
                    ("ext-1", "doc-1", "ver-1", "approved", "legacy-model", "Legacy disclaimer.", _TS, _TS))
        con.execute("INSERT INTO legal_claims VALUES (?,?,?,?,?,?,?,?)",
                    ("claim-1", "ext-1", "ownership_nature", "Fee simple", 0, 1, "Fee simple ownership.", _TS))
        con.execute("INSERT INTO review_approvals VALUES (?,?,?,?,?,?,?,?,?)",
                    ("approval-1", "ext-1", "alice-chen-01", "issuer_reviewer", "issuer_attestation",
                     "Legacy approval disclaimer.", None, _TS, _TS))
    con.commit()
    con.close()
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _master(path: Path) -> list:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return con.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
    finally:
        con.close()


async def test_pre_9b3_database_is_refused_at_startup_without_changes(tmp_path: Path):
    db = _legacy_db(tmp_path / "legacy.db", with_data=True)
    before = _master(db)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db.as_posix()}")
    try:
        async with engine.begin() as conn:
            # Same sequence as the application startup (app.main.lifespan)
            await conn.run_sync(Base.metadata.create_all)
            with pytest.raises(SchemaOutOfDateError) as exc:
                await conn.run_sync(verify_schema_columns)
    finally:
        await engine.dispose()
    message = str(exc.value)
    assert "assets(material_revision)" in message
    assert f"review_approvals({_NEW_APPROVAL_COLUMNS})" in message
    assert _master(db) == before, "startup must not create or alter anything in an old database"


def test_dry_run_reports_the_plan_and_changes_nothing(tmp_path: Path):
    db = _legacy_db(tmp_path / "legacy.db", with_data=True)
    digest = _sha(db)
    report = migration.run(db, apply=False)
    assert report["ok"] and report["mode"] == "dry-run" and report["target_modified"] is False
    assert len(report["statements"]) == 11
    assert report["statements"][0] == "ALTER TABLE assets ADD COLUMN material_revision INTEGER DEFAULT '1' NOT NULL"
    assert report["legacy_approvals_becoming_unbound"] == 1
    assert _sha(db) == digest
    assert not (tmp_path / "backups").exists()


def test_apply_backs_up_migrates_and_preserves_legacy_rows(tmp_path: Path):
    db = _legacy_db(tmp_path / "legacy.db", with_data=True)
    before = sqlite3.connect(db)
    legacy_approval = before.execute("SELECT * FROM review_approvals").fetchall()
    before.close()

    report = migration.run(db, apply=True, backup_dir=tmp_path / "backups")
    assert report["ok"] and report["target_modified"] is True, report["problems"]

    backup = Path(report["backup"])
    assert backup.parent == tmp_path / "backups"
    with sqlite3.connect(backup) as con:
        assert "material_revision" not in {r[1] for r in con.execute("PRAGMA table_info(assets)")}
        assert con.execute("SELECT * FROM review_approvals").fetchall() == legacy_approval

    with sqlite3.connect(db) as con:
        actual, fresh = migration.schema_signature(con), migration.fresh_signature()
        # Every table but review_corrections matches the models; its Stage 9B-4 step 7 columns are
        # added by the next migration in the chain, after which the whole schema matches
        assert {t: s for t, s in actual.items() if t != "review_corrections"} == {
            t: s for t, s in fresh.items() if t != "review_corrections"}
        assert actual["review_corrections"] != fresh["review_corrections"]
        assert migration.row_counts(con) == report["row_counts"]
        assert con.execute("SELECT material_revision FROM assets").fetchall() == [(1,)]
        row = con.execute(f"SELECT id, reviewer_id, {_NEW_APPROVAL_COLUMNS} FROM review_approvals").fetchone()
        assert row[:2] == ("approval-1", "alice-chen-01") and set(row[2:]) == {None}

    # Idempotent: a second run only verifies
    again = migration.run(db, apply=True, backup_dir=tmp_path / "backups2")
    assert again["ok"] and again["statements"] == [] and again["target_modified"] is False


def test_failed_verification_rolls_back_everything(tmp_path: Path, monkeypatch):
    db = _legacy_db(tmp_path / "legacy.db", with_data=True)
    schema_before = _master(db)
    monkeypatch.setattr(migration, "fresh_signature", lambda: {"assets": {"columns": [], "foreign_keys": [], "indexes": []}})
    report = migration.run(db, apply=True, backup_dir=tmp_path / "backups")
    assert report["ok"] is False and report["target_modified"] is False
    assert _master(db) == schema_before


async def test_migrated_legacy_approval_is_never_current_until_re_reviewed(
    client: AsyncClient, tmp_path: Path, issuer_headers: dict, inst_headers: dict
):
    db = _legacy_db(tmp_path / "legacy.db", with_data=True)
    assert migration.run(db, apply=True, backup_dir=tmp_path / "backups")["ok"]
    # The next migration of the chain (Stage 9B-4 step 7), so that the startup check passes
    assert migration_9b4s7.run(db, apply=True, backup_dir=tmp_path / "backups")["ok"]
    # The legacy version's PDF is present in storage, its text contains the claim's quotation and
    # its page record matches the file: the re-approval below must pass the source-evidence gate
    # and the source-PDF check (Stage 9B-4) like any other approval
    pdf = make_text_pdf([["Title: Fee simple ownership. Granted to the Trust."]])
    stored_path = "3f2b8c1e-5d4a-4e6f-9a7b-1c2d3e4f5a6b/v1/" + "0" * 32 + ".pdf"
    target = tmp_path / "storage" / Path(stored_path)  # conftest: StorageService(tmp_path / "storage")
    target.parent.mkdir(parents=True)
    target.write_bytes(pdf)
    (page,) = extract_pages(pdf, _pdf_limits())
    with sqlite3.connect(db) as con:
        con.execute("UPDATE document_versions SET stored_path = ?, sha256_hash = ?, file_size = ? WHERE id = 'ver-1'",
                    (stored_path, hashlib.sha256(pdf).hexdigest(), len(pdf)))
        con.execute("INSERT INTO document_pages VALUES (?,?,?,?,?)",
                    ("page-1", "ver-1", 1, page["text_content"], _TS))

    engine = create_async_engine(f"sqlite+aiosqlite:///{db.as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(verify_schema_columns)  # the migrated database passes the startup check
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def per_request_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = per_request_session
    try:
        a = _MERIDIAN_ASSET
        url = f"/passport/{a['blockchain_network']}/{a['stellar_asset_code']}/{a['stellar_issuer_public_key']}"
        r = await client.get(url, headers=inst_headers)
        assert r.status_code == 409

        state = (await client.get("/extractions/ext-1", headers=issuer_headers)).json()["approval_state"]
        assert state["state"] == "stale" and state["reasons"] == ["unbound_legacy_approval"]

        assert (await client.post("/extractions/ext-1/review", headers=issuer_headers)).status_code == 200
        assert (await client.post("/extractions/ext-1/approvals", headers=issuer_headers, json={})).status_code == 201
        body = (await client.get(url, headers=inst_headers)).json()
        assert body["source_document"]["title"] == "Legacy Prospectus"
        history = (await client.get("/extractions/ext-1/approvals", headers=issuer_headers)).json()
        assert [h["id"] for h in history][0] == "approval-1"
        assert [h["currency"]["is_current"] for h in history] == [False, True]
    finally:
        await engine.dispose()
