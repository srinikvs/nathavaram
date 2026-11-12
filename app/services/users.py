"""User accounts. Passwords are stored only as bcrypt hashes."""

from __future__ import annotations

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
    user.updated_at = dump_dt(datetime.now(timezone.utc))
    db.commit()
    db.refresh(user)
    return user


def list_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).order_by(User.username)).all())


def create_user(db: Session, data: UserCreate, settings: Settings) -> User:
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
    if user is None:
        raise ServiceError("User not found", 404)
    changes = data.model_dump(exclude_unset=True)
    if "role" in changes and changes["role"] is not None:
        user.role = changes["role"]
    if "is_active" in changes and changes["is_active"] is not None:
        user.is_active = changes["is_active"]
    if changes.get("password"):
        user.password_hash = hash_password(changes["password"], settings.bcrypt_rounds)
        user.must_change_password = True
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
