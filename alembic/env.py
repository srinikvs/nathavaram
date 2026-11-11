"""Alembic environment. The database URL comes from DATABASE_URL or alembic.ini."""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import normalize_database_url  # noqa: E402
from app.models import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    import os

    raw = os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not raw:
        raise RuntimeError("Set DATABASE_URL or sqlalchemy.url before running Alembic")
    return normalize_database_url(raw)


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _url()
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    connectable = create_engine(url, poolclass=pool.NullPool, connect_args=connect_args)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
