"""
Startup schema check.

Base.metadata.create_all only creates missing TABLES; it does not add new columns
or indexes to existing tables. So that columns (e.g. assets.issuer_id, added in
Stage 9B-1) and named indexes (e.g. the Stage 9B-2 uniqueness indexes) that exist
in the models but not in the database cannot cause silent errors / unprotected
operation, the application explicitly refuses to start. This check never modifies data.
"""

from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from app.db.base import Base


class SchemaOutOfDateError(RuntimeError):
    """The database schema is behind the models."""


def verify_schema_columns(sync_conn: Connection) -> None:
    inspector = inspect(sync_conn)
    problems: list[str] = []
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {column["name"] for column in inspector.get_columns(table.name)}
        missing = [column.name for column in table.columns if column.name not in existing]
        if missing:
            problems.append(f"{table.name}({', '.join(missing)})")
        existing_indexes = {ix["name"] for ix in inspector.get_indexes(table.name)}
        missing_indexes = sorted(
            ix.name for ix in table.indexes if ix.name and ix.name not in existing_indexes
        )
        if missing_indexes:
            problems.append(f"{table.name} index({', '.join(missing_indexes)})")
    if problems:
        raise SchemaOutOfDateError(
            "Database schema is out of date; missing columns/indexes: "
            + "; ".join(problems)
            + ". Data is never modified automatically. Migrate the existing data with a migration "
            "script, or back up and recreate an empty development database."
        )
