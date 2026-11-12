"""Monotonic server revision used by sync pull."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SyncState


def allocate_revision(db: Session) -> int:
    dialect = db.get_bind().dialect.name
    stmt = select(SyncState).where(SyncState.id == 1)
    if dialect == "postgresql":
        stmt = stmt.with_for_update()
    state = db.scalar(stmt)
    if state is None:
        raise RuntimeError("sync_state row is missing; run migrations")
    state.current_revision += 1
    db.flush()
    return state.current_revision


def current_revision(db: Session) -> int:
    state = db.get(SyncState, 1)
    if state is None:
        raise RuntimeError("sync_state row is missing; run migrations")
    return state.current_revision
