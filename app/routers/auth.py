"""Login and the current session."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_user
from app.models import User
from app.schemas import LoginRequest, TokenResponse, UserPublic
from app.security import create_access_token
from app.services.users import authenticate

router = APIRouter(prefix="/api/auth", tags=["auth"])


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
    )


@router.post("/login", response_model=TokenResponse)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> TokenResponse:
    user = authenticate(db, body.username, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return _issue(response, user, request.app.state.settings)


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    response.delete_cookie(request.app.state.settings.cookie_name, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserPublic)
def me(user: User = Depends(require_user)) -> User:
    return user
