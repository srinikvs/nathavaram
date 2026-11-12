"""Login and the current session."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_session_user, require_user
from app.errors import ServiceError
from app.models import User
from app.schemas import LoginRequest, PasswordChange, TokenResponse, UserPublic
from app.security import create_access_token, csrf_token_for
from app.services.login import (
    INVALID_LOGIN_MESSAGE,
    LoginThrottled,
    attempt_login,
    guarded_change_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _throttled(exc: LoginThrottled) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail="Too many failed attempts. Try again later.",
        headers={"Retry-After": str(exc.retry_after)},
    )


def _issue(response: Response, user: User, settings) -> TokenResponse:
    token = create_access_token(user, settings)
    response.set_cookie(
        settings.cookie_name,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.jwt_expire_minutes * 60,
        path="/",
    )
    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_expire_minutes * 60,
        role=user.role,
        username=user.username,
        csrf_token=csrf_token_for(token, settings),
        must_change_password=user.must_change_password,
    )


@router.post("/login", response_model=TokenResponse)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> TokenResponse:
    try:
        user = attempt_login(db, request, body.username, body.password)
    except LoginThrottled as exc:
        raise _throttled(exc) from exc
    if user is None:
        raise HTTPException(status_code=401, detail=INVALID_LOGIN_MESSAGE)
    return _issue(response, user, request.app.state.settings)


@router.post("/change-password", response_model=UserPublic)
def change_password_endpoint(
    body: PasswordChange,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_session_user),
) -> User:
    try:
        return guarded_change_password(db, request, user, body.current_password, body.new_password)
    except LoginThrottled as exc:
        raise _throttled(exc) from exc
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    response.delete_cookie(request.app.state.settings.cookie_name, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserPublic)
def me(user: User = Depends(require_user)) -> User:
    return user
