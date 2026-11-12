"""Password hashing and JWT sessions."""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from jwt import InvalidTokenError

from fastapi import Response

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


_dummy_hashes: dict[int, str] = {}


def dummy_verify(password: str, rounds: int) -> None:
    """Spend one bcrypt verify against a throwaway hash (unknown-user timing parity)."""
    if rounds not in _dummy_hashes:
        _dummy_hashes[rounds] = hash_password("dummy-password-not-a-real-credential", rounds)
    verify_password(password, _dummy_hashes[rounds])


def create_access_token(user: User, settings: Settings) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,
        "username": user.username,
        "role": user.role,
        "tv": user.token_version,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_expire_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_access_token(token: str, settings: Settings) -> dict:
    return jwt.decode(token, settings.secret_key, algorithms=["HS256"])


def csrf_token_for(session_token: str, settings: Settings) -> str:
    """Token bound to one session cookie. A cross-site page cannot read or derive it."""
    digest = hmac.new(
        settings.secret_key.encode("utf-8"),
        b"csrf:" + session_token.encode("utf-8"),
        hashlib.sha256,
    )
    return digest.hexdigest()


def csrf_matches(session_token: str, supplied: str | None, settings: Settings) -> bool:
    if not supplied:
        return False
    expected = csrf_token_for(session_token, settings).encode("utf-8")
    return hmac.compare_digest(expected, supplied.encode("utf-8"))


__all__ = [
    "csrf_matches",
    "csrf_token_for",
    "InvalidTokenError",
    "create_access_token",
    "decode_access_token",
    "dummy_verify",
    "set_session_cookie",
    "hash_password",
    "verify_password",
]
