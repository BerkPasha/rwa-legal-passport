"""
Stage 10C-3B, T4 — SQLite integrity and contention (app/db/session.py, the lifespan and the
database error handler in app/main.py, DEPLOYMENT.md §9).

Coverage:
 1. Foreign keys are enforced on every connection: the factory's file and in-memory engines, the
    suite's own session, the startup check (which refuses a connection without them)
 2. Invalid references are refused for every declared foreign key; a parent with children cannot be
    deleted; nothing is repaired or deleted
 3. The complete workflow (asset, document versions with pages, extraction, approvals, anchor) on
    a file database with enforcement, without one violation; tenants stay isolated
 4. A second, independent connection holding the write lock: every write route answers a bounded,
    sanitized 503, stores nothing (no row, no orphaned PDF, no Stellar submission), and succeeds
    once on retry. Holding every lock, reads answer 503 too, while authentication, authorization
    and validation answers are unchanged; an IntegrityError is never turned into 503
 5. Concurrent writes on one file database: serialized by the busy timeout, never a duplicate
 6. WAL: opt-in, only for a writable file database (never in memory, read-only or READ_ONLY_MODE),
    readers no longer wait for the writer, a single writer remains; the default changes nothing
 7. A read-only database file still serves a READ_ONLY_MODE server
 8. /health and /ready under contention; repeated startups change neither schema nor data
 9. A real uvicorn process reports its SQLite settings; the development database and its
    sidecar files are never touched by this module
"""

import asyncio
import json
from datetime import datetime
import os
import sqlite3
import stat
import subprocess
import time
from contextlib import asynccontextmanager, closing, contextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Boolean, DateTime, Integer, String, Text, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import main
from app.config import Settings, settings
from app.db import session as db_session
from app.db.base import Base
from app.db.session import (
    DatabaseConfigurationError,
    check_sqlite_state,
    create_database_engine,
    get_db,
    sqlite_wal_applies,
)
from app.main import app
from tests.conftest import (
    _HARBOR_ASSET,
    _MERIDIAN_ASSET,
    ADMIN_TOKEN,
    INST_KEY_ALPHA,
    ISSUER_TOKEN,
    LEGAL_TOKEN,
    OTHER_ISSUER_TOKEN,
    bearer,
    build_test_access_config,
)
from tests.pdf_fixtures import make_source_pdf
from tests.test_approval_evidence_gate import JURISDICTION_QUOTE, _approve, _extract, _upload
from tests.test_deployment_checks import _server
from tests.test_logging_config import _free_port
from tests.test_stellar_anchoring import TESTNET_ASSET, anchor_account, anchors, horizon  # noqa: F401 (fixtures)

BUSY = 0.3                                       # the busy timeout of the contended databases, in seconds
BOUND = BUSY + 3.0                               # every refusal must arrive within this
DATABASE_BUSY = {"detail": "The database is busy or unavailable; please retry."}
TABLES = ("assets", "documents", "document_versions", "document_pages", "legal_extractions", "legal_claims",
          "review_corrections", "review_approvals", "stellar_anchors")


# ── The development database is never touched ─────────────────────────────────


def _development_files() -> dict:
    url = make_url(settings.database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return {}
    database = Path(url.database).resolve()
    paths = [database] + [database.with_name(database.name + suffix) for suffix in ("-wal", "-shm", "-journal")]
    return {str(p): (p.stat().st_size, p.stat().st_mtime_ns) if p.exists() else None for p in paths}


@pytest.fixture(scope="module", autouse=True)
def development_database_untouched():
    """Metadata only (size, modification time, sidecar files): the file's content is never read."""
    before = _development_files()
    yield
    assert _development_files() == before


# ── Fixtures and helpers ──────────────────────────────────────────────────────


@asynccontextmanager
async def file_database(path: Path, busy: float = BUSY, **options):
    engine = create_database_engine(f"sqlite+aiosqlite:///{path.as_posix()}", busy_timeout_seconds=busy, **options)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield SimpleNamespace(path=path, engine=engine, sessions=async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()


def serve_from(database: SimpleNamespace) -> None:
    """Every request of the `client` fixture's application gets its own session of `database`."""
    async def _session():
        async with database.sessions() as session:
            yield session
    app.dependency_overrides[get_db] = _session


@pytest.fixture
async def filedb(client: AsyncClient, tmp_path: Path):
    """The `client` fixture's application, on a file database with a short busy timeout."""
    async with file_database(tmp_path / "t4.db") as database:
        serve_from(database)
        yield database


@contextmanager
def competing_connection(path: Path, exclusive: bool = False):
    """
    A second, independent connection holding SQLite's write lock (BEGIN IMMEDIATE: readers go on,
    writers wait), or every lock (BEGIN EXCLUSIVE in rollback-journal mode: readers wait too).
    """
    with closing(sqlite3.connect(path, timeout=0, isolation_level=None)) as other:
        other.execute("BEGIN EXCLUSIVE" if exclusive else "BEGIN IMMEDIATE")
        try:
            yield other
        finally:
            other.execute("ROLLBACK")


def counts(path: Path) -> dict[str, int]:
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as db:
        return {table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in TABLES}


def header_journal(path: Path) -> str:
    """The journal mode stored in the database file header (bytes 18-19: 1 rollback journal, 2 WAL)."""
    with open(path, "rb") as fh:
        header = fh.read(20)
    return {b"\x01\x01": "rollback", b"\x02\x02": "wal"}[header[18:20]]


def violations(path: Path) -> list:
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as db:
        return db.execute("PRAGMA foreign_key_check").fetchall()


async def timed(request) -> tuple[httpx.Response, float]:
    started = time.monotonic()
    response = await request
    return response, time.monotonic() - started


async def approved_asset(client: AsyncClient, asset: dict = _MERIDIAN_ASSET) -> SimpleNamespace:
    created = await client.post("/assets", headers=bearer(ADMIN_TOKEN), json=asset)
    assert created.status_code == 201, created.text
    document = await _upload(client, created.json()["id"], make_source_pdf(), bearer(ISSUER_TOKEN))
    extraction = await _extract(client, document["id"], bearer(ISSUER_TOKEN))
    return SimpleNamespace(asset=created.json(), document=document, extraction=extraction)


# ── 1. Enforcement on every connection ────────────────────────────────────────


async def test_every_connection_of_a_file_engine_enforces_foreign_keys(tmp_path: Path):
    async with file_database(tmp_path / "fk.db") as database:
        for _ in range(3):                       # no pool: each connection is a new one
            async with database.engine.connect() as conn:
                assert (await conn.exec_driver_sql("PRAGMA foreign_keys")).scalar() == 1
                assert (await conn.exec_driver_sql("PRAGMA busy_timeout")).scalar() == int(BUSY * 1000)


async def test_an_in_memory_engine_enforces_foreign_keys():
    engine = create_database_engine("sqlite+aiosqlite:///:memory:", busy_timeout_seconds=5)
    try:
        async with engine.connect() as conn:
            assert (await conn.exec_driver_sql("PRAGMA foreign_keys")).scalar() == 1
            assert (await conn.exec_driver_sql("PRAGMA journal_mode")).scalar() == "memory"
    finally:
        await engine.dispose()


async def test_the_suites_own_session_enforces_foreign_keys(async_db: AsyncSession):
    assert (await async_db.execute(text("PRAGMA foreign_keys"))).scalar() == 1


def test_the_application_engine_is_made_by_the_factory():
    """Without connecting to it (it is the development database): same URL and its connect hook."""
    assert db_session.engine.url == make_url(settings.database_url)
    hooks = (db_session.configure_sqlite_connection, db_session.configure_sqlite_connection_with_wal)
    assert [event.contains(db_session.engine.sync_engine, "connect", hook) for hook in hooks].count(True) == 1


def test_the_startup_check_refuses_a_connection_without_foreign_keys():
    with pytest.raises(DatabaseConfigurationError):
        check_sqlite_state({"foreign_keys": 0, "journal_mode": "delete", "busy_timeout_ms": 5000}, wal_expected=False)
    with pytest.raises(DatabaseConfigurationError):
        check_sqlite_state({"foreign_keys": 1, "journal_mode": "delete", "busy_timeout_ms": 5000}, wal_expected=True)
    check_sqlite_state({"foreign_keys": 1, "journal_mode": "wal", "busy_timeout_ms": 5000}, wal_expected=True)
    check_sqlite_state({"foreign_keys": 1, "journal_mode": "delete", "busy_timeout_ms": 5000}, wal_expected=False)


# ── 2. Invalid references ─────────────────────────────────────────────────────

_MISSING = "00000000-0000-0000-0000-000000000000"
FOREIGN_KEYS = [(fk.parent.table, fk.parent.name) for table in Base.metadata.sorted_tables
                for fk in sorted(table.foreign_keys, key=lambda fk: fk.parent.name)]
CHILD_TABLES = sorted({table for table, _ in FOREIGN_KEYS}, key=lambda table: table.name)


def _orphan_row(table) -> dict:
    """Every required column filled, every foreign key pointing at a row that does not exist."""
    dummies = {String: "x", Text: "x", Integer: 1, Boolean: False, DateTime: datetime(2026, 1, 1)}
    return {column.name: _MISSING if column.foreign_keys else dummies[type(column.type)]
            for column in table.columns if column.foreign_keys or not column.nullable}


def test_twelve_foreign_keys_are_declared():
    assert len(FOREIGN_KEYS) == 12 and len(CHILD_TABLES) == 8


@pytest.mark.parametrize("table", CHILD_TABLES, ids=lambda table: table.name)
async def test_a_row_referencing_a_missing_parent_is_refused(async_db: AsyncSession, table):
    with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
        await async_db.execute(table.insert().values(**_orphan_row(table)))
    await async_db.rollback()
    assert (await async_db.execute(text(f"SELECT count(*) FROM {table.name}"))).scalar() == 0


async def _every_kind_of_row(client: AsyncClient) -> SimpleNamespace:
    """Asset, document, version, pages, extraction, claims, correction, approval and anchor."""
    made = await approved_asset(client, TESTNET_ASSET)
    claim = next(c for c in made.extraction["claims"] if c["field_name"] == "jurisdiction")
    corrected = await client.patch(
        f"/extractions/{made.extraction['id']}/claims/{claim['id']}", headers=bearer(LEGAL_TOKEN),
        json={"corrected_value": claim["value"], "is_unknown": claim["is_unknown"],
              "correction_note": "Fictional: value confirmed.",
              "expected_claim": {k: claim[k] for k in ("value", "is_unknown", "source_page", "supporting_passage")}})
    assert corrected.status_code == 200, corrected.text
    assert (await _approve(client, made.extraction["id"], bearer(ISSUER_TOKEN))).status_code == 201
    anchored = await client.post(f"/assets/{made.asset['id']}/stellar-anchors", headers=bearer(ISSUER_TOKEN))
    assert anchored.status_code == 201, anchored.text
    return made


async def test_every_foreign_key_refuses_a_change_to_a_missing_parent(client, filedb, anchors, horizon):
    await _every_kind_of_row(client)
    before = counts(filedb.path)
    assert all(before[table.name] > 0 for table in CHILD_TABLES)
    with closing(sqlite3.connect(filedb.path)) as other:      # another connection, with enforcement
        other.execute("PRAGMA foreign_keys=ON")
        for table, column in FOREIGN_KEYS:
            with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
                other.execute(f"UPDATE {table.name} SET {column} = ? WHERE rowid = (SELECT min(rowid) FROM {table.name})",
                              (_MISSING,))
            other.rollback()
    assert counts(filedb.path) == before and violations(filedb.path) == []


async def test_through_the_application_too(client, filedb):
    made = await approved_asset(client)
    async with filedb.sessions() as session:
        with pytest.raises(IntegrityError):
            await session.execute(text("UPDATE legal_extractions SET version_id = :m WHERE id = :e"),
                                  {"m": _MISSING, "e": made.extraction["id"]})
        await session.rollback()
    assert violations(filedb.path) == []


async def test_a_parent_with_children_cannot_be_deleted_and_nothing_is_repaired(client: AsyncClient, filedb):
    made = await approved_asset(client)
    before = counts(filedb.path)
    with closing(sqlite3.connect(filedb.path)) as other:
        other.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            other.execute("DELETE FROM assets WHERE id = ?", (made.asset["id"],))
        other.rollback()
    assert counts(filedb.path) == before


# ── 3. The complete workflow with enforcement ────────────────────────────────


async def test_the_complete_workflow_on_an_enforcing_file_database(client: AsyncClient, filedb):
    made = await approved_asset(client)
    second = await client.post(f"/documents/{made.document['id']}/versions", headers=bearer(ISSUER_TOKEN),
                               files={"file": ("v2.pdf", make_source_pdf(), "application/pdf")})
    assert second.status_code == 201, second.text
    extraction = await _extract(client, made.document["id"], bearer(ISSUER_TOKEN))
    assert (await _approve(client, extraction["id"], bearer(ISSUER_TOKEN))).status_code == 201
    assert (await _approve(client, extraction["id"], bearer(LEGAL_TOKEN))).status_code == 201
    manual = await client.post(f"/documents/{made.document['id']}/manual-extractions", headers=bearer(ISSUER_TOKEN),
                               json={"asset_id": made.asset["id"], "version_id": second.json()["id"],
                                     "claims": [{"field_name": "jurisdiction", "value": "New York law.",
                                                 "source_page": 1, "supporting_passage": JURISDICTION_QUOTE}]})
    assert manual.status_code == 201, manual.text
    rows = counts(filedb.path)
    assert rows["document_versions"] == 2 and rows["document_pages"] == 4 and rows["review_approvals"] == 2
    assert violations(filedb.path) == []
    # Tenant isolation is unchanged
    assert (await client.get(f"/assets/{made.asset['id']}", headers=bearer(OTHER_ISSUER_TOKEN))).status_code == 404
    passport = await client.get(
        f"/passport/{made.asset['blockchain_network']}/{made.asset['stellar_asset_code']}/"
        f"{made.asset['stellar_issuer_public_key']}", headers={"X-API-Key": INST_KEY_ALPHA})
    assert passport.status_code == 200, passport.text


# ── 4. A competing connection holding the lock ────────────────────────────────


def _pdfs(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "storage").rglob("*.pdf"))


async def _refused(request, caplog, route: str) -> None:
    with caplog.at_level("WARNING", logger="rwa_security"):
        response, elapsed = await timed(request)
    assert response.status_code == 503, response.text
    assert elapsed < BOUND
    for leak in ("sqlite", "SELECT", "INSERT", "UPDATE", "locked", "Traceback", ".db"):
        assert leak not in response.text
    caplog.clear()
    return response


async def test_asset_writes_are_refused_while_another_connection_writes(client, filedb, caplog):
    with competing_connection(filedb.path):
        r = await _refused(client.post("/assets", headers=bearer(ADMIN_TOKEN), json=_MERIDIAN_ASSET), caplog, "/assets")
        assert r.json() == DATABASE_BUSY
        assert (await client.get("/assets", headers=bearer(ADMIN_TOKEN))).status_code == 200   # readers go on
    assert counts(filedb.path)["assets"] == 0
    created = await client.post("/assets", headers=bearer(ADMIN_TOKEN), json=_MERIDIAN_ASSET)
    assert created.status_code == 201 and counts(filedb.path)["assets"] == 1
    asset = created.json()
    with competing_connection(filedb.path):
        await _refused(client.patch(f"/assets/{asset['id']}", headers=bearer(ADMIN_TOKEN), json={"jurisdiction": "GB"}),
                       caplog, "/assets/{asset_id}")
    unchanged = (await client.get(f"/assets/{asset['id']}", headers=bearer(ADMIN_TOKEN))).json()
    assert (unchanged["jurisdiction"], unchanged["material_revision"]) == (asset["jurisdiction"], asset["material_revision"])


async def test_the_refusal_is_logged_with_the_route_only(client, filedb, caplog):
    with competing_connection(filedb.path), caplog.at_level("WARNING", logger="rwa_security"):
        await client.post("/assets", headers=bearer(ADMIN_TOKEN), json=_MERIDIAN_ASSET)
    assert "database.unavailable route=/assets reason=locked" in caplog.text
    assert str(filedb.path) not in caplog.text and "INSERT" not in caplog.text


async def test_an_upload_is_refused_without_leaving_a_file_or_a_row(client, filedb, tmp_path, caplog):
    asset = (await client.post("/assets", headers=bearer(ADMIN_TOKEN), json=_MERIDIAN_ASSET)).json()
    upload = lambda: client.post(f"/assets/{asset['id']}/documents", headers=bearer(ISSUER_TOKEN),  # noqa: E731
                                 data={"document_type": "prospectus", "title": "Prospectus"},
                                 files={"file": ("doc.pdf", make_source_pdf(), "application/pdf")})
    with competing_connection(filedb.path):
        await _refused(upload(), caplog, "/assets/{asset_id}/documents")
    assert _pdfs(tmp_path) == []
    assert {k: v for k, v in counts(filedb.path).items() if k != "assets"} == dict.fromkeys(TABLES[1:], 0)
    assert (await upload()).status_code == 201
    assert len(_pdfs(tmp_path)) == 1 and counts(filedb.path)["documents"] == 1
    document_id = (await client.get(f"/assets/{asset['id']}/documents", headers=bearer(ISSUER_TOKEN))).json()[0]["id"]
    version = lambda: client.post(f"/documents/{document_id}/versions", headers=bearer(ISSUER_TOKEN),  # noqa: E731
                                  files={"file": ("v2.pdf", make_source_pdf(), "application/pdf")})
    with competing_connection(filedb.path):
        await _refused(version(), caplog, "/documents/{document_id}/versions")
    assert len(_pdfs(tmp_path)) == 1 and counts(filedb.path)["document_versions"] == 1
    assert (await version()).status_code == 201 and len(_pdfs(tmp_path)) == 2


async def test_extractions_corrections_and_approvals_are_refused_and_leave_nothing(client, filedb, caplog):
    made = await approved_asset(client)
    before = counts(filedb.path)
    extraction = made.extraction
    claim = next(c for c in extraction["claims"] if c["field_name"] == "jurisdiction")
    with competing_connection(filedb.path):
        await _refused(client.post(f"/documents/{made.document['id']}/extractions", headers=bearer(ISSUER_TOKEN)),
                       caplog, "extractions")
        await _refused(client.post(f"/documents/{made.document['id']}/manual-extractions", headers=bearer(ISSUER_TOKEN),
                                   json={"asset_id": made.asset["id"], "version_id": made.document["current_version"]["id"],
                                         "claims": [{"field_name": "jurisdiction", "value": "New York law.",
                                                     "source_page": 1, "supporting_passage": JURISDICTION_QUOTE}]}),
                       caplog, "manual-extractions")
        await _refused(_approve(client, extraction["id"], bearer(ISSUER_TOKEN)), caplog, "approvals")
        await _refused(client.post(f"/extractions/{extraction['id']}/review", headers=bearer(LEGAL_TOKEN)),
                       caplog, "review")
        await _refused(client.patch(f"/extractions/{extraction['id']}/claims/{claim['id']}", headers=bearer(LEGAL_TOKEN),
                                    json={"corrected_value": claim["value"], "is_unknown": claim["is_unknown"],
                                          "correction_note": "Fictional: value confirmed.",
                                          "expected_claim": {k: claim[k] for k in ("value", "is_unknown", "source_page",
                                                                                   "supporting_passage")}}),
                       caplog, "corrections")
    assert counts(filedb.path) == before
    status = (await client.get(f"/extractions/{extraction['id']}", headers=bearer(ISSUER_TOKEN))).json()["status"]
    assert status == extraction["status"]
    assert (await _approve(client, extraction["id"], bearer(ISSUER_TOKEN))).status_code == 201
    assert counts(filedb.path)["review_approvals"] == 1


async def test_an_anchor_is_never_submitted_without_its_record(client, filedb, anchors, horizon, caplog):
    made = await approved_asset(client, TESTNET_ASSET)
    assert (await _approve(client, made.extraction["id"], bearer(ISSUER_TOKEN))).status_code == 201
    url = f"/assets/{made.asset['id']}/stellar-anchors"
    with competing_connection(filedb.path):
        await _refused(client.post(url, headers=bearer(ISSUER_TOKEN)), caplog, "stellar-anchors")
    assert horizon.submissions == [] and counts(filedb.path)["stellar_anchors"] == 0
    first = await client.post(url, headers=bearer(ISSUER_TOKEN))
    assert first.status_code == 201, first.text
    again = await client.post(url, headers=bearer(ISSUER_TOKEN))
    assert again.status_code in (200, 201) and again.json()["transaction_hash"] == first.json()["transaction_hash"]
    assert len(horizon.submissions) == 1 and counts(filedb.path)["stellar_anchors"] == 1


async def test_holding_every_lock_reads_answer_503_and_other_answers_are_unchanged(client, filedb, caplog):
    asset = (await client.post("/assets", headers=bearer(ADMIN_TOKEN), json=_MERIDIAN_ASSET)).json()
    with competing_connection(filedb.path, exclusive=True):
        r = await _refused(client.get("/assets", headers=bearer(ISSUER_TOKEN)), caplog, "/assets")
        assert r.json() == DATABASE_BUSY
        await _refused(client.get(f"/assets/{asset['id']}", headers=bearer(ISSUER_TOKEN)), caplog, "/assets/{asset_id}")
        # Decided before the database is used: unchanged by the lock
        assert (await client.get("/assets")).status_code == 403
        assert (await client.get("/assets", headers=bearer("not-a-token"))).status_code == 401
        assert (await client.post("/assets", headers=bearer(LEGAL_TOKEN), json=_MERIDIAN_ASSET)).status_code == 403
        assert (await client.post("/assets", headers=bearer(ADMIN_TOKEN), json={"asset_type": 1})).status_code == 422
        assert (await client.get("/health")).status_code == 200
    assert (await client.get("/assets", headers=bearer(ISSUER_TOKEN))).status_code == 200


async def test_a_constraint_violation_is_not_reported_as_unavailable(client, filedb):
    assert (await client.post("/assets", headers=bearer(ADMIN_TOKEN), json=_MERIDIAN_ASSET)).status_code == 201
    duplicate = await client.post("/assets", headers=bearer(ADMIN_TOKEN), json=_MERIDIAN_ASSET)
    assert duplicate.status_code == 409


async def test_an_unexpected_integrity_error_stays_a_500(client):
    async def broken_db():
        raise IntegrityError("INSERT INTO assets ...", {}, Exception("internal detail"))
        yield  # pragma: no cover

    working = app.dependency_overrides[get_db]
    app.dependency_overrides[get_db] = broken_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as ac:
            r = await ac.get("/assets", headers=bearer(ISSUER_TOKEN))
    finally:
        app.dependency_overrides[get_db] = working
    assert r.status_code == 500 and "internal detail" not in r.text and "INSERT" not in r.text


async def test_an_unusable_database_answers_503(client, filedb, caplog):
    with closing(sqlite3.connect(filedb.path)) as other:
        other.execute("DROP TABLE stellar_anchors")             # a damaged, disposable database
    with caplog.at_level("WARNING", logger="rwa_security"):
        asset = (await client.post("/assets", headers=bearer(ADMIN_TOKEN), json=TESTNET_ASSET)).json()
        r = await client.get(f"/assets/{asset['id']}/stellar-anchors", headers=bearer(ISSUER_TOKEN))
    assert (r.status_code, r.json()) == (503, DATABASE_BUSY)
    assert "reason=unavailable" in caplog.text and "stellar_anchors" not in r.text


# ── 5. Concurrent writes ──────────────────────────────────────────────────────


async def test_concurrent_writes_are_serialized_without_duplicates(client, tmp_path):
    async with file_database(tmp_path / "concurrent.db", busy=5.0) as database:
        serve_from(database)
        made = await approved_asset(client)
        issuer, legal = bearer(ISSUER_TOKEN), bearer(LEGAL_TOKEN)
        results = await asyncio.gather(_approve(client, made.extraction["id"], issuer),
                                       _approve(client, made.extraction["id"], issuer),
                                       _approve(client, made.extraction["id"], legal))
        codes = sorted(r.status_code for r in results)
        assert codes == [201, 201, 409], [r.text for r in results]
        assert counts(database.path)["review_approvals"] == 2
        created = await asyncio.gather(*(client.post("/assets", headers=bearer(ADMIN_TOKEN),
                                                     json={**_HARBOR_ASSET, "stellar_asset_code": f"HB{n}"})
                                         for n in range(5)))
        assert [r.status_code for r in created] == [201] * 5
        assert counts(database.path)["assets"] == 6 and violations(database.path) == []


# ── 6. WAL ────────────────────────────────────────────────────────────────────


async def _journal(engine) -> str:
    async with engine.connect() as conn:
        return (await conn.exec_driver_sql("PRAGMA journal_mode")).scalar()


async def test_the_default_leaves_the_journal_mode_unchanged(client, filedb):
    assert await _journal(filedb.engine) == "delete"
    await approved_asset(client)
    assert header_journal(filedb.path) == "rollback"


async def test_wal_is_opt_in_and_keeps_a_single_writer(client, tmp_path, caplog):
    async with file_database(tmp_path / "wal.db", journal_mode="wal") as database:
        serve_from(database)
        assert await _journal(database.engine) == "wal"
        asset = (await client.post("/assets", headers=bearer(ADMIN_TOKEN), json=_MERIDIAN_ASSET)).json()
        assert header_journal(database.path) == "wal"
        with competing_connection(database.path, exclusive=True):
            # Readers no longer wait for the writer ...
            assert (await client.get(f"/assets/{asset['id']}", headers=bearer(ADMIN_TOKEN))).status_code == 200
            # ... but there is still one writer at a time
            await _refused(client.post("/assets", headers=bearer(ADMIN_TOKEN),
                                       json={**_HARBOR_ASSET, "stellar_asset_code": "HBW"}), caplog, "/assets")
        assert counts(database.path)["assets"] == 1
    # Stored in the file: a later engine with the default keeps WAL
    async with file_database(tmp_path / "wal.db") as later:
        assert await _journal(later.engine) == "wal"


@pytest.mark.parametrize("url, read_only, applies", [
    ("sqlite+aiosqlite:///./x.db", False, True),
    ("sqlite+aiosqlite:///./x.db", True, False),                       # READ_ONLY_MODE
    ("sqlite+aiosqlite:///:memory:", False, False),
    ("sqlite+aiosqlite://", False, False),
    ("sqlite+aiosqlite:///file:x.db?mode=ro&uri=true", False, False),
    ("sqlite+aiosqlite:///file:x?mode=memory&cache=shared&uri=true", False, False),
])
def test_wal_applies_only_to_a_writable_file_database(url, read_only, applies):
    assert sqlite_wal_applies(url, "wal", read_only) is applies
    assert sqlite_wal_applies(url, "unchanged", read_only) is False


async def test_wal_is_never_applied_in_memory_or_in_read_only_mode(tmp_path):
    memory = create_database_engine("sqlite+aiosqlite:///:memory:", busy_timeout_seconds=1, journal_mode="wal")
    try:
        assert await _journal(memory) == "memory"
    finally:
        await memory.dispose()
    async with file_database(tmp_path / "ro-mode.db", journal_mode="wal", read_only=True) as database:
        assert await _journal(database.engine) == "delete"
    assert header_journal(tmp_path / "ro-mode.db") == "rollback"


# ── 7. A read-only database file ──────────────────────────────────────────────


def _lifespan_settings(path: Path, tmp_path: Path, **values) -> Settings:
    registry = tmp_path / "access_control.json"
    registry.write_text(json.dumps(build_test_access_config()), encoding="utf-8")
    return Settings(_env_file=None, app_env="test-t4", database_url=f"sqlite+aiosqlite:///{path.as_posix()}",
                    access_control_file=str(registry), storage_path=str(tmp_path / "storage"),
                    stellar_anchor_secret_file=str(tmp_path / "none.json"), ai_provider="disabled",
                    sqlite_busy_timeout_seconds=BUSY, **values)


async def _start(monkeypatch, subject: Settings, engine=None) -> None:
    engine = engine or create_database_engine(subject.database_url, busy_timeout_seconds=subject.sqlite_busy_timeout_seconds,
                                              journal_mode=subject.sqlite_journal_mode, read_only=subject.read_only_mode)
    monkeypatch.setattr(main, "settings", subject)
    monkeypatch.setattr(main, "engine", engine)
    try:
        async with main.lifespan(main.app):
            pass
    finally:
        await engine.dispose()


async def test_a_read_only_database_file_serves_a_read_only_server(client, tmp_path, monkeypatch, caplog):
    path = tmp_path / "showcase.db"
    async with file_database(path) as database:
        serve_from(database)
        made = await approved_asset(client)
    os.chmod(path, stat.S_IREAD)                               # as on a read-only file system
    try:
        subject = _lifespan_settings(path, tmp_path, read_only_mode=True, sqlite_journal_mode="wal")
        with caplog.at_level("INFO", logger="rwa_security"):
            await _start(monkeypatch, subject)
        assert "database.sqlite foreign_keys=on journal_mode=delete" in caplog.text
        monkeypatch.setattr(settings, "read_only_mode", True)
        async with file_database(path, read_only=True) as database:
            serve_from(database)
            assert (await client.get(f"/assets/{made.asset['id']}", headers=bearer(ISSUER_TOKEN))).status_code == 200
            assert (await client.post("/assets", headers=bearer(ADMIN_TOKEN), json=_HARBOR_ASSET)).status_code == 403
    finally:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    assert header_journal(path) == "rollback"


# ── 8. /health, /ready and repeated startups ──────────────────────────────────


async def test_health_and_ready_under_contention(client, filedb, monkeypatch):
    monkeypatch.setattr(db_session, "engine", filedb.engine)
    registry = filedb.path.with_name("ready-registry.json")
    registry.write_text(json.dumps(build_test_access_config()), encoding="utf-8")
    (filedb.path.parent / "ready-storage").mkdir()
    monkeypatch.setattr(settings, "access_control_file", str(registry))
    monkeypatch.setattr(settings, "storage_path", str(filedb.path.parent / "ready-storage"))
    with competing_connection(filedb.path):                    # a writer: readiness reads on
        assert (await client.get("/ready")).json() == {"status": "ready"}
    with competing_connection(filedb.path, exclusive=True):    # every lock: not ready, still alive
        ready = await client.get("/ready")
        assert (ready.status_code, ready.json()) == (503, {"status": "not_ready"})
        assert (await client.get("/health")).json()["status"] == "ok"
    assert (await client.get("/ready")).status_code == 200


def _schema(path: Path) -> list:
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as db:
        return db.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()


async def test_repeated_startups_change_neither_schema_nor_data(client, tmp_path, monkeypatch, caplog):
    path = tmp_path / "restart.db"
    async with file_database(path) as database:
        serve_from(database)
        await approved_asset(client)
    schema, rows = _schema(path), counts(path)
    for _ in range(3):
        with caplog.at_level("INFO", logger="rwa_security"):
            await _start(monkeypatch, _lifespan_settings(path, tmp_path))
    assert (_schema(path), counts(path)) == (schema, rows)
    assert caplog.text.count("database.sqlite foreign_keys=on journal_mode=delete busy_timeout_ms=300 wal_setting=unchanged") == 3
    assert violations(path) == []


async def test_a_startup_without_foreign_keys_is_refused_before_any_table_is_created(tmp_path, monkeypatch):
    path = tmp_path / "unenforced.db"
    subject = _lifespan_settings(path, tmp_path)
    with pytest.raises(DatabaseConfigurationError):
        await _start(monkeypatch, subject, engine=create_async_engine(subject.database_url))
    assert _schema(path) == []


async def test_a_wal_startup_reports_wal(tmp_path, monkeypatch, caplog):
    path = tmp_path / "wal-start.db"
    with caplog.at_level("INFO", logger="rwa_security"):
        await _start(monkeypatch, _lifespan_settings(path, tmp_path, sqlite_journal_mode="wal"))
    assert "database.sqlite foreign_keys=on journal_mode=wal busy_timeout_ms=300 wal_setting=wal" in caplog.text


# ── 9. A real uvicorn process ─────────────────────────────────────────────────


@pytest.mark.parametrize("variables, journal", [
    ({"APP_ENV": "test-t4"}, "delete"),
    ({"APP_ENV": "test-t4-wal", "SQLITE_JOURNAL_MODE": "wal", "SQLITE_BUSY_TIMEOUT_SECONDS": "2"}, "wal"),
], ids=["default", "wal"])
def test_a_real_server_reports_its_sqlite_settings(tmp_path, variables, journal):
    port = _free_port()
    command, env = _server(tmp_path, port, **variables)
    log_path = tmp_path / "server.log"
    with open(log_path, "wb") as log:
        server = subprocess.Popen(command, cwd=tmp_path, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 30
            while True:
                try:
                    ready = httpx.get(f"http://127.0.0.1:{port}/ready", timeout=2)
                    break
                except httpx.HTTPError:
                    if server.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("the disposable server did not start") from None
                    time.sleep(0.2)
            created = httpx.post(f"http://127.0.0.1:{port}/assets", headers=bearer(ADMIN_TOKEN), json=_MERIDIAN_ASSET,
                                 timeout=10)
        finally:
            server.terminate()
            server.wait(timeout=30)
    assert ready.json() == {"status": "ready"} and created.status_code == 201
    busy_ms = int(float(variables.get("SQLITE_BUSY_TIMEOUT_SECONDS", "5")) * 1000)
    text_ = log_path.read_text(encoding="utf-8", errors="replace")
    assert f"database.sqlite foreign_keys=on journal_mode={journal} busy_timeout_ms={busy_ms}" in text_
    assert header_journal(tmp_path / "server.db") == ("wal" if journal == "wal" else "rollback")
