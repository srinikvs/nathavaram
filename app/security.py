"""Password hashing and JWT sessions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from jwt import InvalidTokenError

from app.config import Settings
from app.models import User


def hash_password(password: str, rounds: int) -> str:
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=rounds))
    return hashed.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(user: User, settings: Settings) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,
        "username": user.username,
        "role": user.role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_expire_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_access_token(token: str, settings: Settings) -> dict:
    return jwt.decode(token, settings.secret_key, algorithms=["HS256"])


__all__ = [
    "InvalidTokenError",
    "create_access_token",
    "decode_access_token",
    "hash_password",
    "verify_password",
]
