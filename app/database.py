"""SQLAlchemy engine. SQLite by default; Postgres via DATABASE_URL."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

engine: Engine | None = None
SessionLocal: sessionmaker[Session] | None = None


def ensure_sqlite_parent(url: str) -> None:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return
    path = url[len(prefix) :]
    if path in {":memory:", ""} or path.startswith("file:"):
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def init_db(url: str) -> sessionmaker[Session]:
    global engine, SessionLocal
    if engine is not None:
        engine.dispose()
    ensure_sqlite_parent(url)
    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True, future=True)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
            # Let SQLAlchemy own BEGIN so SAVEPOINT (used per sync change) is reliable.
            dbapi_connection.isolation_level = None
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        @event.listens_for(engine, "begin")
        def _sqlite_begin(connection) -> None:  # noqa: ANN001
            connection.exec_driver_sql("BEGIN")

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    return SessionLocal


def dispose_db() -> None:
    global engine, SessionLocal
    if engine is not None:
        engine.dispose()
    engine = None
    SessionLocal = None


def get_db() -> Generator[Session, None, None]:
    if SessionLocal is None:
        raise RuntimeError("Database is not initialised")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
