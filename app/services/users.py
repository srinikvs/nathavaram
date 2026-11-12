"""User accounts. Passwords are stored only as bcrypt hashes."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import ServiceError
from app.models import User
from app.schemas import UserCreate, UserUpdate
from app.security import dummy_verify, hash_password, verify_password
from app.validation import dump_dt


MIN_PASSWORD_LENGTH = 10
audit_logger = logging.getLogger("nathavaram.audit")


def authenticate(db: Session, username: str, password: str, settings: Settings) -> User | None:
    """Return the user, or None for unknown, inactive, or wrong-password alike.

    A bcrypt verify always runs, against a dummy hash when no usable account
    exists, so response time does not reveal whether the username is real.
    """
    user = db.scalar(select(User).where(User.username == username.strip()))
    if user is None or not user.is_active:
        dummy_verify(password, settings.bcrypt_rounds)
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def change_password(
    db: Session, user: User, current_password: str, new_password: str, settings: Settings
) -> User:
    """Set a new password after checking the current one; clears the forced-change flag."""
    if not verify_password(current_password, user.password_hash):
        raise ServiceError("Current password is incorrect", 400, "current_password")
    if new_password == current_password:
        raise ServiceError("New password must differ from the current password", 400, "new_password")
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise ServiceError(
            f"New password must be at least {MIN_PASSWORD_LENGTH} characters", 400, "new_password"
        )
    user.password_hash = hash_password(new_password, settings.bcrypt_rounds)
    user.must_change_password = False
    user.token_version += 1  # every token issued before this change stops working
    user.updated_at = dump_dt(datetime.now(timezone.utc))
    db.commit()
    db.refresh(user)
    audit("password_changed", user, user)
    return user


def audit(action: str, actor: User, target: User) -> None:
    """Structured log line for account actions. Ids and usernames only, never secrets."""
    audit_logger.info(
        "user_audit action=%s actor_id=%s actor=%s target_id=%s target=%s",
        action,
        actor.id,
        actor.username,
        target.id,
        target.username,
    )


def list_users(db: Session, include_deleted: bool = False) -> list[User]:
    query = select(User).order_by(User.username)
    if not include_deleted:
        query = query.where(User.deleted_at.is_(None))
    return list(db.scalars(query).all())


def _require_password_length(password: str, field: str = "password") -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ServiceError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters", 400, field
        )


def reset_user_password(
    db: Session, actor: User, user_id: str, new_password: str, settings: Settings, throttle=None
) -> User:
    """Owner sets a temporary password for another user and forces a change at next use.

    Owners cannot reset themselves here (they use Change password, which checks
    the current password).
    """
    target = db.get(User, user_id)
    if target is None or target.deleted_at is not None:
        raise ServiceError("User not found", 404)
    if target.id == actor.id:
        raise ServiceError("Use Change password to change your own password", 400)
    _require_password_length(new_password, "new_password")
    target.password_hash = hash_password(new_password, settings.bcrypt_rounds)
    target.must_change_password = True
    target.token_version += 1
    target.updated_at = dump_dt(datetime.now(timezone.utc))
    db.commit()
    db.refresh(target)
    if throttle is not None:
        throttle.clear_user(target.username)
    audit("password_reset", actor, target)
    return target


def delete_user(db: Session, actor: User, user_id: str) -> User:
    """Soft delete: deactivate, stamp deleted_at, revoke sessions. The row and username stay."""
    target = db.get(User, user_id)
    if target is None or target.deleted_at is not None:
        raise ServiceError("User not found", 404)
    if target.id == actor.id:
        raise ServiceError("You cannot delete your own account", 400)
    if target.is_owner:
        raise ServiceError("An owner account cannot be deleted", 400)
    now = dump_dt(datetime.now(timezone.utc))
    target.is_active = False
    target.deleted_at = now
    target.token_version += 1
    target.updated_at = now
    db.commit()
    db.refresh(target)
    audit("user_deleted", actor, target)
    return target


def create_user(db: Session, data: UserCreate, settings: Settings) -> User:
    _require_password_length(data.password)
    existing = db.scalar(select(User).where(User.username == data.username))
    if existing is not None:
        raise ServiceError("Username already exists", 409, "username")
    now = dump_dt(datetime.now(timezone.utc))
    user = User(
        username=data.username,
        password_hash=hash_password(data.password, settings.bcrypt_rounds),
        role=data.role,
        is_active=True,
        must_change_password=True,
        is_owner=False,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def update_user(db: Session, user_id: str, data: UserUpdate, settings: Settings, actor: User) -> User:
    user = db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise ServiceError("User not found", 404)
    changes = data.model_dump(exclude_unset=True)
    if "role" in changes and changes["role"] is not None:
        user.role = changes["role"]
    if "is_active" in changes and changes["is_active"] is not None:
        if user.is_active and not changes["is_active"]:
            user.token_version += 1
        user.is_active = changes["is_active"]
    if changes.get("password"):
        if user.id == actor.id:
            db.rollback()
            raise ServiceError("Use Change password to change your own password", 400)
        user.password_hash = hash_password(changes["password"], settings.bcrypt_rounds)
        user.must_change_password = True
        user.token_version += 1
    db.flush()
    if user.role != "admin" or not user.is_active:
        admins = db.scalar(
            select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True))
        )
        if not admins:
            db.rollback()
            raise ServiceError("At least one active admin is required", 400)
    if actor.id == user.id and not user.is_active:
        db.rollback()
        raise ServiceError("You cannot deactivate your own account", 400)
    user.updated_at = dump_dt(datetime.now(timezone.utc))
    db.commit()
    db.refresh(user)
    return user
