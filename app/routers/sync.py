"""Offline sync pull and push. See docs/SYNC.md."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_user
from app.errors import ServiceError
from app.models import User
from app.schemas import SyncPullResponse, SyncPushRequest, SyncPushResponse
from app.services.sync import pull_changes, push_changes

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.get("/pull", response_model=SyncPullResponse)
def pull(
    since: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> SyncPullResponse:
    return pull_changes(db, since=since, limit=limit, user=user)


@router.post("/push", response_model=SyncPushResponse)
def push(
    body: SyncPushRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> SyncPushResponse:
    try:
        return push_changes(db, body, user)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
