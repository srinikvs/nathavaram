"""Owner-only user management (see docs/SECURITY.md)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_owner
from app.errors import ServiceError
from app.models import User
from app.schemas import ResetPassword, UserCreate, UserPublic, UserUpdate
from app.services.users import (
    create_user,
    delete_user,
    list_users,
    reset_user_password,
    update_user,
)

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("", response_model=list[UserPublic])
def get_users(db: Session = Depends(get_db), _: User = Depends(require_owner)) -> list[User]:
    return list_users(db)


@router.post("", response_model=UserPublic, status_code=201)
def post_user(
    body: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_owner),
) -> User:
    try:
        return create_user(db, body, request.app.state.settings)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/{user_id}/reset-password", response_model=UserPublic)
def reset_password(
    user_id: str,
    body: ResetPassword,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_owner),
) -> User:
    try:
        return reset_user_password(
            db,
            actor,
            user_id,
            body.new_password,
            request.app.state.settings,
            request.app.state.login_throttle,
        )
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.delete("/{user_id}", response_model=UserPublic)
def remove_user(
    user_id: str,
    db: Session = Depends(get_db),
    actor: User = Depends(require_owner),
) -> User:
    try:
        return delete_user(db, actor, user_id)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.patch("/{user_id}", response_model=UserPublic)
def patch_user(
    user_id: str,
    body: UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_owner),
) -> User:
    try:
        return update_user(db, user_id, body, request.app.state.settings, actor)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
