"""Password-check login flow shared by the JSON API and the HTML form.

Throttle first, then authenticate. A blocked caller is refused before any
password is checked, so a correct guess after the limit is still a 429.
"""

from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import ServiceError
from app.models import User
from app.security import verify_password
from app.services.throttle import LoginThrottle
from app.services.users import authenticate, change_password

INVALID_LOGIN_MESSAGE = "Invalid username or password"


class LoginThrottled(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("Too many failed attempts")
        self.retry_after = retry_after


def client_ip(request: Request) -> str:
    """Socket peer address. Forwarded headers are ignored: a client could forge them."""
    return request.client.host if request.client else "unknown"


def throttle_of(request: Request) -> LoginThrottle:
    return request.app.state.login_throttle


def attempt_login(db: Session, request: Request, username: str, password: str) -> User | None:
    """Return the user on success, None on bad credentials; raise LoginThrottled when blocked."""
    settings: Settings = request.app.state.settings
    throttle = throttle_of(request)
    ip = client_ip(request)
    ticket, retry_after = throttle.begin(ip, username)
    if ticket is None:
        raise LoginThrottled(retry_after)
    user = authenticate(db, username, password, settings)
    if user is None:
        return None
    throttle.succeed(ip, username, ticket)
    return user


def guarded_change_password(
    db: Session, request: Request, user: User, current_password: str, new_password: str
) -> User:
    """change_password with the current-password guess counted against the same limits."""
    settings: Settings = request.app.state.settings
    throttle = throttle_of(request)
    ip = client_ip(request)
    ticket, retry_after = throttle.begin(ip, user.username)
    if ticket is None:
        raise LoginThrottled(retry_after)
    if not verify_password(current_password, user.password_hash):
        raise ServiceError("Current password is incorrect", 400, "current_password")
    throttle.succeed(ip, user.username, ticket)
    return change_password(db, user, current_password, new_password, settings)
