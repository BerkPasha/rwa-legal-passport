import logging
from typing import Optional

from sqlalchemy import event
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.engine.url import URL
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings


def _is_file_database(url: URL) -> bool:
    """A writable SQLite file: not in memory, not opened read-only through a URI."""
    database = url.database or ""
    if database in ("", ":memory:") or url.query.get("mode") in ("memory", "ro"):
        return False
    return not (database.startswith("file:") and ("mode=ro" in database or "mode=memory" in database))


def sqlite_wal_applies(url: str | URL, journal_mode: str, read_only: bool) -> bool:
    """Whether journal_mode="wal" switches this database to WAL (see create_database_engine)."""
    url = make_url(url)
    return (url.get_backend_name() == "sqlite" and journal_mode == "wal" and not read_only
            and _is_file_database(url))


def _sqlite_pragmas(dbapi_connection, wal: bool) -> None:
    # Outside any transaction (PRAGMA foreign_keys is ignored inside one)
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        if wal:
            cursor.execute("PRAGMA journal_mode=WAL")
    finally:
        cursor.close()


def configure_sqlite_connection(dbapi_connection, connection_record) -> None:
    _sqlite_pragmas(dbapi_connection, wal=False)


def configure_sqlite_connection_with_wal(dbapi_connection, connection_record) -> None:
    _sqlite_pragmas(dbapi_connection, wal=True)


def create_database_engine(url: str, *, busy_timeout_seconds: float, journal_mode: str = "unchanged",
                           read_only: bool = False) -> AsyncEngine:
    """
    The application's engine (Stage 10C-3B, T4; DEPLOYMENT.md §9). For SQLite, every new connection
    (each session opens one: file databases use no pool) gets:
    - PRAGMA foreign_keys=ON: SQLite enforces declared foreign keys only when a connection asks for
      it. Existing rows are not re-checked, and nothing is repaired or deleted;
    - a busy timeout (sqlite3's `timeout`): how long it waits for another connection's lock before
      failing with "database is locked", which the API answers with 503;
    - with journal_mode="wal", write-ahead logging, for a writable file database only (never in
      memory, never for a read-only URI or a READ_ONLY_MODE server). The mode is stored in the
      database file and stays after the setting is removed. Nothing changes the journal mode otherwise.
    """
    engine_url = make_url(url)
    sqlite = engine_url.get_backend_name() == "sqlite"
    # Statement parameters can hold confidential document text (page texts, quotations): they are
    # kept out of error messages (hide_parameters) and out of the driver's DEBUG log of every
    # statement (Stage 10A-2)
    engine = create_async_engine(engine_url, echo=False, hide_parameters=True,
                                 connect_args={"timeout": busy_timeout_seconds} if sqlite else {})
    if sqlite:
        wal = sqlite_wal_applies(engine_url, journal_mode, read_only)
        event.listen(engine.sync_engine, "connect",
                     configure_sqlite_connection_with_wal if wal else configure_sqlite_connection)
    return engine


class DatabaseConfigurationError(RuntimeError):
    """A SQLite connection does not have the settings the application relies on."""


def check_sqlite_state(state: dict, *, wal_expected: bool) -> None:
    """Refuses to start without foreign-key enforcement, or without WAL when WAL was requested."""
    if state["foreign_keys"] != 1:
        raise DatabaseConfigurationError("SQLite foreign key enforcement is not active on the connection; "
                                         "refusing to start (DEPLOYMENT.md, section 9).")
    if wal_expected and state["journal_mode"] != "wal":
        raise DatabaseConfigurationError("SQLITE_JOURNAL_MODE=wal was requested but the database did not switch "
                                         "to WAL (another process may hold it open); refusing to start.")


def sqlite_connection_state(sync_conn: Connection) -> Optional[dict]:
    """The effective SQLite settings of a connection (None for another database), for startup."""
    if sync_conn.dialect.name != "sqlite":
        return None
    return {
        "foreign_keys": sync_conn.exec_driver_sql("PRAGMA foreign_keys").scalar(),
        "journal_mode": sync_conn.exec_driver_sql("PRAGMA journal_mode").scalar(),
        "busy_timeout_ms": sync_conn.exec_driver_sql("PRAGMA busy_timeout").scalar(),
    }


engine = create_database_engine(
    settings.database_url, busy_timeout_seconds=settings.sqlite_busy_timeout_seconds,
    journal_mode=settings.sqlite_journal_mode, read_only=settings.read_only_mode,
)
logging.getLogger("aiosqlite").setLevel(logging.INFO)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
