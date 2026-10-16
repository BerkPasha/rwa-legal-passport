"""
Stage 9B-4 step 7 database migration — source page history of reviewer corrections.

Adds, without changing or deleting any existing data:
  review_corrections.original_source_page    INTEGER NULL
  review_corrections.corrected_source_page   INTEGER NULL
  review_corrections.evidence_audit_version  INTEGER NULL

Effect on existing data: corrections recorded before this migration keep every value; their new
columns are NULL, which the application reports as page_history_recorded = false (their pages were
never recorded). Requires a database already migrated to Stage 9B-3.

Safety (the same procedure as scripts/migrate_9b3_approval_integrity.py):
- Default is a DRY RUN: the target is opened read-only, the migration runs on an in-memory copy,
  and the result is verified. Nothing is written.
- --apply first writes a verified backup (SQLite online-backup API, integrity_check, identical row
  counts). The ALTER statements then run in ONE transaction (BEGIN IMMEDIATE), committed only if:
  integrity_check is ok, foreign_key_check is empty, row counts are unchanged, every existing
  correction row is unchanged, and the schema is identical to a fresh create_all of the models.
- --rollback reverses the migration (DROP COLUMN, SQLite >= 3.35) after a verified backup, in one
  transaction, verified against the pre-migration schema. Page history recorded since the
  migration would be lost, so it is refused unless --discard-page-history is given.
- Idempotent: an already migrated (or already rolled back) database is only verified.

Usage:
  python scripts/migrate_9b4_correction_evidence.py --db rwa_legal_passport.db              # dry run
  python scripts/migrate_9b4_correction_evidence.py --db rwa_legal_passport.db --apply      # migrate
  python scripts/migrate_9b4_correction_evidence.py --db rwa_legal_passport.db --rollback   # reverse
"""

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.dialects import sqlite as sqlite_dialect  # noqa: E402
from sqlalchemy.schema import CreateColumn  # noqa: E402

import app.models.asset  # noqa: E402,F401
import app.models.document  # noqa: E402,F401
import app.models.extraction  # noqa: E402,F401
import app.models.review  # noqa: E402,F401
from app.db.base import Base  # noqa: E402

TABLE = "review_corrections"
NEW_COLUMNS = ("original_source_page", "corrected_source_page", "evidence_audit_version")
# Columns that must exist before this migration (Stage 9B-3)
REQUIRED_BEFORE = {"assets": ("material_revision",), "review_approvals": ("binding_digest",)}


def _column_ddl(column) -> str:
    return str(CreateColumn(column).compile(dialect=sqlite_dialect.dialect())).strip()


def _columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in con.execute(f"PRAGMA table_info({table})")]


def _check_preconditions(con: sqlite3.Connection) -> None:
    if not _columns(con, TABLE):
        raise SystemExit(f"table {TABLE!r} does not exist; this is not an RWA Legal Passport database")
    for table, columns in REQUIRED_BEFORE.items():
        if not set(columns) <= set(_columns(con, table)):
            raise SystemExit("the database is not migrated to Stage 9B-3; run scripts/migrate_9b3_approval_integrity.py first")


def planned_statements(con: sqlite3.Connection) -> list[str]:
    existing = set(_columns(con, TABLE))
    model = Base.metadata.tables[TABLE].columns
    return [f"ALTER TABLE {TABLE} ADD COLUMN {_column_ddl(model[name])}" for name in NEW_COLUMNS if name not in existing]


def rollback_statements(con: sqlite3.Connection) -> list[str]:
    existing = set(_columns(con, TABLE))
    return [f"ALTER TABLE {TABLE} DROP COLUMN {name}" for name in reversed(NEW_COLUMNS) if name in existing]


def schema_signature(con: sqlite3.Connection) -> dict:
    """Structure of every model table, independent of how the DDL text was written."""
    signature = {}
    for table in sorted(Base.metadata.tables):
        columns = [row[1:6] for row in con.execute(f"PRAGMA table_xinfo({table})")]
        fks = sorted(row[2:5] for row in con.execute(f"PRAGMA foreign_key_list({table})"))
        indexes = []
        for row in con.execute(f"PRAGMA index_list({table})"):
            cols = [r[2] for r in con.execute(f"PRAGMA index_info({row[1]!r})")]
            name = row[1] if not row[1].startswith("sqlite_autoindex") else "<auto>"
            indexes.append((name, row[2], row[3], tuple(cols)))
        signature[table] = {"columns": columns, "foreign_keys": fks, "indexes": sorted(indexes)}
    return signature


def fresh_signature() -> dict:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    raw = engine.raw_connection()
    try:
        return schema_signature(raw.driver_connection)
    finally:
        raw.close()
        engine.dispose()


def pre_migration_signature() -> dict:
    """The models as they were before this migration: the fresh schema without the new columns."""
    signature = fresh_signature()
    table = signature[TABLE]
    table["columns"] = [column for column in table["columns"] if column[0] not in NEW_COLUMNS]
    return signature


def row_counts(con: sqlite3.Connection) -> dict:
    return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in sorted(Base.metadata.tables)}


def _existing_rows(con: sqlite3.Connection, columns: list[str]) -> list[tuple]:
    return con.execute(f"SELECT {', '.join(columns)} FROM {TABLE} ORDER BY id").fetchall()


def _verify(con: sqlite3.Connection, counts_before: dict, expected: dict, columns_before: list[str],
            rows_before: list[tuple]) -> list[str]:
    problems = []
    if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        problems.append("integrity_check failed")
    if con.execute("PRAGMA foreign_key_check").fetchall():
        problems.append("foreign_key_check reported violations")
    if row_counts(con) != counts_before:
        problems.append("row counts changed")
    kept = [c for c in columns_before if c not in NEW_COLUMNS]
    if _existing_rows(con, kept) != [tuple(r[columns_before.index(c)] for c in kept) for r in rows_before]:
        problems.append("existing correction records changed")
    actual = schema_signature(con)
    for table in sorted(expected):
        if actual.get(table) != expected[table]:
            problems.append(f"schema of {table} differs from the expected schema")
    return problems


def _run_connection(con: sqlite3.Connection, rollback: bool, discard_page_history: bool) -> dict:
    """Runs the migration or its rollback in one transaction on an open connection (isolation_level=None)."""
    _check_preconditions(con)
    counts = row_counts(con)
    columns_before = _columns(con, TABLE)
    rows_before = _existing_rows(con, columns_before)
    legacy_corrections = con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    recorded = 0
    if rollback:
        statements = rollback_statements(con)
        if "evidence_audit_version" in columns_before:
            recorded = con.execute(
                f"SELECT COUNT(*) FROM {TABLE} WHERE evidence_audit_version IS NOT NULL").fetchone()[0]
        if recorded and not discard_page_history:
            return {"statements": statements, "row_counts": counts, "page_history_records": recorded,
                    "legacy_corrections": legacy_corrections, "ok": False,
                    "problems": [f"{recorded} correction records carry page history; it would be lost "
                                 "(pass --discard-page-history to roll back anyway)"]}
        expected = pre_migration_signature()
    else:
        statements = planned_statements(con)
        expected = fresh_signature()
    con.execute("BEGIN IMMEDIATE")
    try:
        for statement in statements:
            con.execute(statement)
        problems = _verify(con, counts, expected, columns_before, rows_before)
        con.execute("ROLLBACK" if problems else "COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return {"statements": statements, "row_counts": counts, "page_history_records": recorded,
            "legacy_corrections": legacy_corrections, "problems": problems, "ok": not problems}


def _backup(target: Path, backup_dir: Path, label: str) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest = backup_dir / f"{target.stem}.{label}.{stamp}.db"
    if dest.exists():
        raise SystemExit(f"backup {dest} already exists")
    src = sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
    out = sqlite3.connect(dest)
    try:
        src.backup(out)
        if out.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or row_counts(out) != row_counts(src):
            raise SystemExit("backup verification failed; nothing was changed")
    finally:
        out.close()
        src.close()
    return dest


def run(target: Path, apply: bool = False, rollback: bool = False, backup_dir: Path | None = None,
        discard_page_history: bool = False) -> dict:
    if not target.is_file():
        raise SystemExit(f"database {target} not found")
    if sqlite3.sqlite_version_info < (3, 35, 0) and rollback:
        raise SystemExit("rollback needs SQLite 3.35 or later (DROP COLUMN)")
    mode = "rollback" if rollback else "apply" if apply else "dry-run"
    if mode == "dry-run":
        source = sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
        memory = sqlite3.connect(":memory:", isolation_level=None)
        try:
            source.backup(memory)
            report = _run_connection(memory, rollback=False, discard_page_history=False)
        finally:
            memory.close()
            source.close()
        return {**report, "mode": mode, "target_modified": False}

    backup = _backup(target, backup_dir or target.parent / "backups", "pre-9b4s7" if apply else "pre-9b4s7-rollback")
    con = sqlite3.connect(target, isolation_level=None)
    try:
        report = _run_connection(con, rollback=rollback, discard_page_history=discard_page_history)
    finally:
        con.close()
    return {**report, "mode": mode, "backup": str(backup),
            "target_modified": report["ok"] and bool(report["statements"])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, type=Path, help="SQLite database file to migrate")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true", help="migrate the database (default: dry run)")
    action.add_argument("--rollback", action="store_true", help="reverse the migration")
    parser.add_argument("--discard-page-history", action="store_true",
                        help="allow a rollback that drops recorded page history")
    parser.add_argument("--backup-dir", type=Path, help="where the backup is written (default: <db dir>/backups)")
    args = parser.parse_args()
    report = run(args.db, args.apply, args.rollback, args.backup_dir, args.discard_page_history)
    print(f"mode: {report['mode']}")
    if report.get("backup"):
        print(f"backup: {report['backup']}")
    print(f"row counts: {report['row_counts']}")
    print("statements:" if report["statements"] else "statements: none (nothing to change)")
    for statement in report["statements"]:
        print(f"  {statement};")
    print(f"existing correction records (page history not recorded): {report['legacy_corrections']}")
    print("verification: " + ("PASS (schema as expected, integrity ok, row counts and existing records unchanged)"
                              if report["ok"] else "FAIL: " + "; ".join(report["problems"])))
    print(f"target modified: {report['target_modified']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
