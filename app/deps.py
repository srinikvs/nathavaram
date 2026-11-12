"""Authentication dependencies for the JSON API."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.security import InvalidTokenError, csrf_matches, decode_access_token

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
    claim = payload.get("tv")
    # bool is an int subclass; reject it so `true` cannot match version 1.
    if not isinstance(claim, int) or isinstance(claim, bool) or claim != user.token_version:
        return None
    return user


SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
CSRF_EXEMPT_PATHS = {"/login", "/api/auth/login"}
CSRF_HEADER = "x-csrf-token"
CSRF_FORM_FIELD = "csrf_token"


def _uses_bearer(request: Request) -> bool:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return scheme.lower() == "bearer" and bool(value.strip())


async def csrf_guard(request: Request) -> None:
    """Reject cookie-authenticated unsafe requests that lack the session's CSRF token.

    Bearer-token requests are exempt: a browser never attaches that header on its own.
    The token is read from the X-CSRF-Token header or the csrf_token form field.
    """
    if request.method in SAFE_METHODS or request.url.path in CSRF_EXEMPT_PATHS:
        return
    if _uses_bearer(request):
        return
    settings = request.app.state.settings
    session_token = request.cookies.get(settings.cookie_name)
    if not session_token:
        return
    supplied = request.headers.get(CSRF_HEADER)
    if not supplied:
        content_type = request.headers.get("content-type", "").lower()
        if content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            form = await request.form()
            value = form.get(CSRF_FORM_FIELD)
            supplied = value if isinstance(value, str) else None
    if not csrf_matches(session_token, supplied, settings):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")


PASSWORD_CHANGE_REQUIRED = "password_change_required"


def require_session_user(
    request: Request,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> User:
    """Authenticated user, even when a password change is still pending."""
    user = resolve_user(request, db, credentials)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_user(user: User = Depends(require_session_user)) -> User:
    """Authenticated user whose password is not flagged for a forced change."""
    if user.must_change_password:
        raise HTTPException(
            status_code=403,
            detail={
                "code": PASSWORD_CHANGE_REQUIRED,
                "message": "Password change required. POST /api/auth/change-password first.",
            },
        )
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


def require_owner(user: User = Depends(require_admin)) -> User:
    if not user.is_owner:
        raise HTTPException(status_code=403, detail="Owner role required")
    return user


def source_for(user: User) -> str:
    return f"web:{user.username}"[:64]
