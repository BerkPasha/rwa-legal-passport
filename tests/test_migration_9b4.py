"""
Stage 9B-4 step 7 migration — scripts/migrate_9b4_correction_evidence.py.

Only isolated scratch databases are used: the pre-9B-3 schema of the development database
(tests/fixtures/schema_pre_9b3.sql), migrated to Stage 9B-3 with the 9B-3 script, then to step 7.
The real development database is never touched.
"""

import hashlib
import importlib.util
import sqlite3
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.schema_check import SchemaOutOfDateError, verify_schema_columns
from app.db.session import get_db
from app.main import app
from tests.conftest import _MERIDIAN_ASSET, MERIDIAN_ISSUER

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_FIXTURE = Path(__file__).parent / "fixtures" / "schema_pre_9b3.sql"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m9b3 = _load("migrate_9b3_for_9b4_tests", "migrate_9b3_approval_integrity.py")
m9b4 = _load("migrate_9b4s7", "migrate_9b4_correction_evidence.py")

_TS = "2026-10-01 12:00:00.000000"
_LEGACY_CORRECTION = ("correction-1", "ext-1", "claim-1", "bob-martinez-01", "independent_legal_reviewer",
                      "Fee simple", 0, "Fee simple ownership.", "Fee simple, amended", 0, None, "Legacy note.", _TS)


def _pre_9b3_db(path: Path) -> Path:
    con = sqlite3.connect(path)
    con.executescript(_FIXTURE.read_text(encoding="utf-8"))
    a = _MERIDIAN_ASSET
    con.execute("INSERT INTO assets VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("asset-1", a["blockchain_network"], a["stellar_asset_code"], a["stellar_issuer_public_key"],
                 MERIDIAN_ISSUER, a["issuer_organization_name"], a["asset_type"], a["jurisdiction"], "active", _TS, _TS))
    con.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)",
                ("doc-1", "asset-1", "prospectus", "Legacy Prospectus", "active", 1, _TS, _TS))
    con.execute("INSERT INTO document_versions VALUES (?,?,?,?,?,?,?,?)",
                ("ver-1", "doc-1", 1, "legacy.pdf", "doc-1/v1/legacy.pdf", 10, "a" * 64, _TS))
    con.execute("INSERT INTO legal_extractions VALUES (?,?,?,?,?,?,?,?)",
                ("ext-1", "doc-1", "ver-1", "under_review", "legacy-model", "Legacy disclaimer.", _TS, _TS))
    con.execute("INSERT INTO legal_claims VALUES (?,?,?,?,?,?,?,?)",
                ("claim-1", "ext-1", "ownership_nature", "Fee simple, amended", 0, 1, "Fee simple ownership.", _TS))
    con.execute("INSERT INTO review_corrections VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", _LEGACY_CORRECTION)
    con.commit()
    con.close()
    return path


@pytest.fixture
def db_9b3(tmp_path: Path) -> Path:
    """A database in the state the development database is in: migrated to Stage 9B-3."""
    db = _pre_9b3_db(tmp_path / "dev-copy.db")
    assert m9b3.run(db, apply=True, backup_dir=tmp_path / "backups-9b3")["ok"]
    return db


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _columns(db: Path) -> list[str]:
    with sqlite3.connect(db) as con:
        return [row[1] for row in con.execute("PRAGMA table_info(review_corrections)")]


def _legacy_row(db: Path) -> tuple:
    with sqlite3.connect(db) as con:
        return con.execute(
            "SELECT id, extraction_id, claim_id, reviewer_id, reviewer_role, original_value, original_is_unknown, "
            "original_passage, corrected_value, corrected_is_unknown, corrected_passage, correction_note, created_at "
            "FROM review_corrections").fetchone()


def _startup_check(db: Path) -> None:
    """The check the application runs at startup (app.main.lifespan)."""
    engine = create_engine("sqlite://", creator=lambda: sqlite3.connect(db))
    try:
        with engine.connect() as conn:
            verify_schema_columns(conn)
    finally:
        engine.dispose()


def test_a_9b3_database_fails_the_startup_check_until_migrated(db_9b3: Path):
    with pytest.raises(SchemaOutOfDateError, match="review_corrections"):
        _startup_check(db_9b3)


def test_dry_run_reports_the_plan_and_changes_nothing(db_9b3: Path, tmp_path: Path):
    digest = _sha(db_9b3)
    report = m9b4.run(db_9b3)
    assert report["ok"] and report["mode"] == "dry-run" and report["target_modified"] is False
    assert report["statements"] == [
        "ALTER TABLE review_corrections ADD COLUMN original_source_page INTEGER",
        "ALTER TABLE review_corrections ADD COLUMN corrected_source_page INTEGER",
        "ALTER TABLE review_corrections ADD COLUMN evidence_audit_version INTEGER",
    ]
    assert report["legacy_corrections"] == 1
    assert _sha(db_9b3) == digest
    assert not (tmp_path / "backups").exists()


def test_apply_backs_up_migrates_and_keeps_existing_corrections(db_9b3: Path, tmp_path: Path):
    legacy = _legacy_row(db_9b3)
    report = m9b4.run(db_9b3, apply=True, backup_dir=tmp_path / "backups")
    assert report["ok"] and report["target_modified"] is True, report["problems"]
    backup = Path(report["backup"])
    assert backup.parent == tmp_path / "backups" and "evidence_audit_version" not in _columns(backup)
    assert _legacy_row(backup) == legacy
    with sqlite3.connect(db_9b3) as con:
        assert m9b4.schema_signature(con) == m9b4.fresh_signature()  # the whole chain reaches the models
        assert m9b4.row_counts(con) == report["row_counts"]
        assert con.execute("SELECT original_source_page, corrected_source_page, evidence_audit_version "
                           "FROM review_corrections").fetchone() == (None, None, None)
    assert _legacy_row(db_9b3) == legacy
    _startup_check(db_9b3)  # passes now
    again = m9b4.run(db_9b3, apply=True, backup_dir=tmp_path / "backups2")
    assert again["ok"] and again["statements"] == [] and again["target_modified"] is False


def test_a_pre_9b3_database_is_refused(tmp_path: Path):
    db = _pre_9b3_db(tmp_path / "old.db")
    digest = _sha(db)
    with pytest.raises(SystemExit, match="9B-3"):
        m9b4.run(db, apply=True, backup_dir=tmp_path / "backups")
    assert _sha(db) == digest


def test_failed_verification_rolls_back_everything(db_9b3: Path, tmp_path: Path, monkeypatch):
    digest_columns = _columns(db_9b3)
    monkeypatch.setattr(m9b4, "fresh_signature", lambda: {"review_corrections": {"columns": [], "foreign_keys": [],
                                                                                "indexes": []}})
    report = m9b4.run(db_9b3, apply=True, backup_dir=tmp_path / "backups")
    assert report["ok"] is False and report["target_modified"] is False
    assert _columns(db_9b3) == digest_columns


def test_rollback_restores_the_9b3_schema_and_keeps_the_records(db_9b3: Path, tmp_path: Path):
    before_columns, legacy = _columns(db_9b3), _legacy_row(db_9b3)
    with sqlite3.connect(db_9b3) as con:
        signature_9b3 = m9b4.schema_signature(con)
    assert m9b4.run(db_9b3, apply=True, backup_dir=tmp_path / "b1")["ok"]
    report = m9b4.run(db_9b3, rollback=True, backup_dir=tmp_path / "b2")
    assert report["ok"] and report["target_modified"] is True, report["problems"]
    assert report["statements"] == [
        "ALTER TABLE review_corrections DROP COLUMN evidence_audit_version",
        "ALTER TABLE review_corrections DROP COLUMN corrected_source_page",
        "ALTER TABLE review_corrections DROP COLUMN original_source_page",
    ]
    assert _columns(db_9b3) == before_columns and _legacy_row(db_9b3) == legacy
    with sqlite3.connect(db_9b3) as con:
        assert m9b4.schema_signature(con) == signature_9b3 == m9b4.pre_migration_signature()
    # Migrating again after a rollback works
    assert m9b4.run(db_9b3, apply=True, backup_dir=tmp_path / "b3")["ok"]


def test_rollback_never_silently_discards_recorded_page_history(db_9b3: Path, tmp_path: Path):
    assert m9b4.run(db_9b3, apply=True, backup_dir=tmp_path / "b1")["ok"]
    with sqlite3.connect(db_9b3) as con:
        con.execute("UPDATE review_corrections SET original_source_page = 1, corrected_source_page = 1, "
                    "evidence_audit_version = 1")
    refused = m9b4.run(db_9b3, rollback=True, backup_dir=tmp_path / "b2")
    assert refused["ok"] is False and refused["target_modified"] is False and refused["page_history_records"] == 1
    assert "evidence_audit_version" in _columns(db_9b3)
    forced = m9b4.run(db_9b3, rollback=True, backup_dir=tmp_path / "b3", discard_page_history=True)
    assert forced["ok"] and "evidence_audit_version" not in _columns(db_9b3)
    assert Path(forced["backup"]).exists()  # the page history is still in the backup


async def test_the_application_reads_a_migrated_legacy_correction(
    client: AsyncClient, db_9b3: Path, tmp_path: Path, issuer_headers: dict
):
    assert m9b4.run(db_9b3, apply=True, backup_dir=tmp_path / "backups")["ok"]
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_9b3.as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(verify_schema_columns)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def per_request_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = per_request_session
    try:
        (record,) = (await client.get("/extractions/ext-1/corrections", headers=issuer_headers)).json()
        assert (record["id"], record["corrected_value"], record["correction_note"]) == (
            "correction-1", "Fee simple, amended", "Legacy note.")
        assert (record["page_history_recorded"], record["original_source_page"], record["corrected_source_page"]) == (
            False, None, None)
    finally:
        await engine.dispose()
