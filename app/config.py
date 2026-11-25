"""Runtime settings. Secrets come from the environment, never from the repo."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]

_PLACEHOLDER_SECRETS = {
    "replace-with-a-long-random-string",
    "change-me",
    "changeme",
    "secret",
    "test",
}
_PLACEHOLDER_PASSWORDS = {
    "replace-with-10-or-more-characters",
    "changeme1234",
    "password123",
    "admin12345",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Nathavaram Voter List"
    app_env: str = "development"
    database_url: str = "sqlite:///./data/nathavaram.db"
    secret_key: str = Field(min_length=1)
    jwt_expire_minutes: int = 720
    admin_username: str | None = None
    admin_password: str | None = None
    bcrypt_rounds: int = 12
    cookie_name: str = "access_token"
    cookie_secure: bool = False
    issue_photo_dir: str = "data/issue-photos"


def normalize_database_url(url: str) -> str:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return url
    path = url[len(prefix) :]
    if path in {":memory:", ""} or path.startswith("file:"):
        return url
    if path.startswith("/"):
        return url
    absolute = (ROOT / path).resolve()
    return prefix + absolute.as_posix()


DEFAULT_ISSUE_PHOTO_DIR = Path("data/issue-photos")
_ISSUE_PHOTO_SUBDIR = "issue-photos"
_SQLITE_FILE_PREFIX = "sqlite:///"


def _sqlite_file_path(database_url: str) -> Path | None:
    """Path of a file-based SQLite database, or None for memory, empty, URI and non-SQLite URLs."""
    if not database_url.startswith(_SQLITE_FILE_PREFIX):
        return None
    path = database_url[len(_SQLITE_FILE_PREFIX) :]
    if path in {":memory:", ""} or path.startswith("file:"):
        return None
    return Path(path)


def resolve_issue_photo_dir(explicit: str | None, database_url: str) -> Path:
    """Pick the photo directory. Pure: never touches the disk.

    1. An explicit ISSUE_PHOTO_DIR (blank counts as unset) wins.
    2. Otherwise, a file-based SQLite database puts photos in ``issue-photos`` beside the file.
    3. Otherwise the relative default ``data/issue-photos`` is used.
    Relative results are resolved against ROOT by ``load_settings``.
    """
    if explicit is not None and explicit.strip():
        return Path(explicit.strip())
    db_file = _sqlite_file_path(database_url)
    if db_file is not None:
        return db_file.parent / _ISSUE_PHOTO_SUBDIR
    return DEFAULT_ISSUE_PHOTO_DIR


def load_settings() -> Settings:
    settings = Settings()
    # Read before normalize_database_url assigns a field: only values the operator set count.
    explicit_photo_dir = (
        settings.issue_photo_dir if "issue_photo_dir" in settings.model_fields_set else None
    )
    settings.database_url = normalize_database_url(settings.database_url)
    photo_path = resolve_issue_photo_dir(explicit_photo_dir, settings.database_url)
    if photo_path.is_absolute():
        settings.issue_photo_dir = photo_path.as_posix()
    else:
        settings.issue_photo_dir = (ROOT / photo_path).resolve().as_posix()
    _validate(settings)
    return settings


PRODUCTION_ENVS = {"production", "prod"}


def is_production(settings: Settings) -> bool:
    return settings.app_env.strip().casefold() in PRODUCTION_ENVS


def _validate(settings: Settings) -> None:
    if is_production(settings) and not settings.cookie_secure:
        raise RuntimeError(
            "COOKIE_SECURE must be true when APP_ENV is production. "
            "Serve the site over HTTPS and set COOKIE_SECURE=true."
        )
    secret = settings.secret_key.strip()
    if len(secret) < 16 or secret.casefold() in _PLACEHOLDER_SECRETS:
        raise RuntimeError(
            "SECRET_KEY must be a unique string of at least 16 characters. "
            "Generate one locally; do not commit it."
        )
    has_user = bool(settings.admin_username and settings.admin_username.strip())
    has_password = bool(settings.admin_password)
    if has_user != has_password:
        raise RuntimeError("Set both ADMIN_USERNAME and ADMIN_PASSWORD, or leave both empty")
    if has_password and settings.admin_password is not None and len(settings.admin_password) < 10:
        raise RuntimeError("ADMIN_PASSWORD must be at least 10 characters")
    if (
        settings.admin_password
        and settings.admin_password.strip().casefold() in _PLACEHOLDER_PASSWORDS
    ):
        raise RuntimeError(
            "ADMIN_PASSWORD is still the example placeholder. Choose a password and keep it out of git."
        )
    if settings.bcrypt_rounds < 4 or settings.bcrypt_rounds > 16:
        raise RuntimeError("BCRYPT_ROUNDS must be between 4 and 16")
    if settings.jwt_expire_minutes < 5:
        raise RuntimeError("JWT_EXPIRE_MINUTES must be at least 5")
