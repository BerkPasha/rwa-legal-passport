"""
Stage 9B-3 database migration — legal approval version integrity.

Adds, without changing or deleting any existing data:
  assets.material_revision          INTEGER NOT NULL DEFAULT 1   (existing assets start at revision 1)
  review_approvals.<binding columns> nullable                    (existing approvals stay unbound)

Effect on existing data: approvals created before this migration have no binding. The application
treats them as history that is never current (reason 'unbound_legacy_approval'); the passport of
such an asset returns 409 until a reviewer re-reviews and approves again.

Safety:
- Default is a DRY RUN: the target is opened read-only, the migration runs on an in-memory copy,
  and the result is verified. Nothing is written.
- --apply first writes a backup with the SQLite online-backup API and verifies it (integrity_check,
  identical row counts). Then all ALTER statements run in ONE transaction (BEGIN IMMEDIATE), which is
  committed only if: integrity_check is ok, foreign_key_check is empty, row counts are unchanged,
  the migrated tables are identical to a fresh create_all of the models (columns, types,
  NOT NULL, defaults, primary keys, foreign keys, indexes) and every other table is unchanged.
  Otherwise it is rolled back. Tables changed by later stages are brought up to date by their own
  migrations (Stage 9B-4 step 7: scripts/migrate_9b4_correction_evidence.py).
- Idempotent: an already migrated database is only verified.

Usage:
  python scripts/migrate_9b3_approval_integrity.py --db rwa_legal_passport.db             # dry run
  python scripts/migrate_9b3_approval_integrity.py --db rwa_legal_passport.db --apply     # migrate
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

# The tables this migration governs (the models imported above). Tables added by later stages,
# such as stellar_anchors, are additive: the application creates them, empty, at startup.
GOVERNED_TABLES = (
    "assets", "document_pages", "document_versions", "documents",
    "legal_claims", "legal_extractions", "review_approvals", "review_corrections",
)

MIGRATED_TABLES = ("assets", "review_approvals")


def _column_ddl(column) -> str:
    ddl = str(CreateColumn(column).compile(dialect=sqlite_dialect.dialect())).strip()
    for fk in column.foreign_keys:
        ddl += f" REFERENCES {fk.column.table.name} ({fk.column.name})"
    return ddl


def planned_statements(con: sqlite3.Connection) -> list[str]:
    statements = []
    for name in MIGRATED_TABLES:
        existing = {row[1] for row in con.execute(f"PRAGMA table_info({name})")}
        if not existing:
            raise SystemExit(f"table {name!r} does not exist; this is not an RWA Legal Passport database")
        for column in Base.metadata.tables[name].columns:
            if column.name not in existing:
                statements.append(f"ALTER TABLE {name} ADD COLUMN {_column_ddl(column)}")
    return statements


def schema_signature(con: sqlite3.Connection) -> dict:
    """Structure of every model table, independent of how the DDL text was written."""
    signature = {}
    for table in GOVERNED_TABLES:
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


def row_counts(con: sqlite3.Connection) -> dict:
    return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in GOVERNED_TABLES}


def _verify(con: sqlite3.Connection, counts_before: dict, schema_before: dict) -> list[str]:
    problems = []
    if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        problems.append("integrity_check failed")
    if con.execute("PRAGMA foreign_key_check").fetchall():
        problems.append("foreign_key_check reported violations")
    if row_counts(con) != counts_before:
        problems.append("row counts changed")
    actual, expected = schema_signature(con), fresh_signature()
    for table in sorted(expected):
        # Only this migration's tables must match the models; every other table must be untouched
        target = expected[table] if table in MIGRATED_TABLES else schema_before.get(table)
        if actual.get(table) != target:
            problems.append(f"schema of {table} differs from the models" if table in MIGRATED_TABLES
                            else f"schema of {table} was changed")
    return problems


def _migrate_connection(con: sqlite3.Connection) -> dict:
    """Runs the migration in one transaction on an open connection (isolation_level=None)."""
    counts = row_counts(con)
    schema_before = schema_signature(con)
    legacy_approvals = con.execute("SELECT COUNT(*) FROM review_approvals").fetchone()[0]
    statements = planned_statements(con)
    con.execute("BEGIN IMMEDIATE")
    try:
        for statement in statements:
            con.execute(statement)
        problems = _verify(con, counts, schema_before)
        if problems:
            con.execute("ROLLBACK")
        else:
            con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return {
        "statements": statements,
        "row_counts": counts,
        "legacy_approvals_becoming_unbound": legacy_approvals if statements else 0,
        "problems": problems,
        "ok": not problems,
    }


def _backup(target: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"{target.stem}.pre-9b3.{stamp}.db"
    if dest.exists():
        raise SystemExit(f"backup {dest} already exists")
    src = sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
    out = sqlite3.connect(dest)
    try:
        src.backup(out)
        if out.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or row_counts(out) != row_counts(src):
            raise SystemExit("backup verification failed; nothing was migrated")
    finally:
        out.close()
        src.close()
    return dest


def run(target: Path, apply: bool, backup_dir: Path | None = None) -> dict:
    if not target.is_file():
        raise SystemExit(f"database {target} not found")
    if not apply:
        source = sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
        memory = sqlite3.connect(":memory:", isolation_level=None)
        try:
            source.backup(memory)
            report = _migrate_connection(memory)
        finally:
            memory.close()
            source.close()
        return {**report, "mode": "dry-run", "target_modified": False}

    backup = _backup(target, backup_dir or target.parent / "backups")
    con = sqlite3.connect(target, isolation_level=None)
    try:
        report = _migrate_connection(con)
    finally:
        con.close()
    return {**report, "mode": "apply", "backup": str(backup), "target_modified": report["ok"] and bool(report["statements"])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, type=Path, help="SQLite database file to migrate")
    parser.add_argument("--apply", action="store_true", help="migrate the database (default: dry run)")
    parser.add_argument("--backup-dir", type=Path, help="where the pre-migration backup is written (default: <db dir>/backups)")
    args = parser.parse_args()
    report = run(args.db, args.apply, args.backup_dir)
    print(f"mode: {report['mode']}")
    if report.get("backup"):
        print(f"backup: {report['backup']}")
    print(f"row counts: {report['row_counts']}")
    print("statements:" if report["statements"] else "statements: none (already migrated)")
    for statement in report["statements"]:
        print(f"  {statement};")
    print(f"existing approvals that will be unbound (never current until re-review): "
          f"{report['legacy_approvals_becoming_unbound']}")
    print("verification: " + ("PASS (schema identical to the models, integrity ok, row counts unchanged)"
                              if report["ok"] else "FAIL: " + "; ".join(report["problems"])))
    print(f"target modified: {report['target_modified']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
