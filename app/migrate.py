"""Apply Alembic migrations to the configured database."""

from __future__ import annotations

from alembic import command
from alembic.config import Config

from app.config import ROOT, normalize_database_url


def run_migrations(database_url: str) -> None:
    url = normalize_database_url(database_url)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    # Config interpolates % as ConfigParser tokens.
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(cfg, "head")
