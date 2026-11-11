"""Authentication dependencies for the JSON API."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.security import InvalidTokenError, decode_access_token

bearer = HTTPBearer(auto_error=False)


def resolve_user(
    request: Request,
    db: Session,
    credentials: HTTPAuthorizationCredentials | None,
) -> User | None:
    token = None
    if credentials is not None and credentials.scheme.lower() == "bearer":
        token = credentials.credentials
    if not token:
        token = request.cookies.get(request.app.state.settings.cookie_name)
    if not token:
        return None
    try:
        payload = decode_access_token(token, request.app.state.settings)
    except InvalidTokenError:
        return None
    user = db.get(User, str(payload.get("sub", "")))
    if user is None or not user.is_active:
        return None
    return user


def require_user(
    request: Request,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> User:
    user = resolve_user(request, db, credentials)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


def source_for(user: User) -> str:
    return f"web:{user.username}"[:64]
