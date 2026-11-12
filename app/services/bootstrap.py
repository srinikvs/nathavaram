"""Create the first admin from the environment. Never inserts a built-in password."""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import User
from app.security import hash_password
from app.validation import canonical_username, dump_dt
from datetime import datetime, timezone

logger = logging.getLogger("nathavaram")


def seed_admin(db: Session, settings: Settings) -> None:
    username = (settings.admin_username or "").strip()
    password = settings.admin_password
    user_count = db.scalar(select(func.count()).select_from(User)) or 0
    if not username or not password:
        if user_count == 0:
            logger.warning(
                "No users exist. Set ADMIN_USERNAME and ADMIN_PASSWORD and restart to seed an admin."
            )
        return
    try:
        username = canonical_username(username)
    except ValueError as exc:
        raise RuntimeError(f"ADMIN_USERNAME is invalid: {exc}") from exc
    existing = db.scalar(select(User).where(User.username == username))
    if existing is not None:
        if not existing.is_owner:
            # Databases created before the owner flag: the env-named admin becomes the owner.
            existing.is_owner = True
            db.commit()
            logger.info("Marked env admin %s as owner", username)
        logger.info("Admin user %s already exists; leaving the password unchanged", username)
        return
    now = dump_dt(datetime.now(timezone.utc))
    db.add(
        User(
            username=username,
            password_hash=hash_password(password, settings.bcrypt_rounds),
            role="admin",
            is_active=True,
            must_change_password=True,
            is_owner=True,
            created_at=now,
            updated_at=now,
        )
    )
    db.commit()
    logger.info("Seeded admin user %s from the environment", username)
