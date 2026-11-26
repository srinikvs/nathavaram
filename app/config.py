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
    campaign_photo_dir: str = "data/campaign-photos"
    # WhatsApp Business Cloud API. All three must be set for live sends; otherwise dry-run.
    whatsapp_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_api_version: str = "v21.0"
    # SMS HTTP/DLT provider. URL + API key required for live sends; otherwise dry-run.
    sms_api_url: str | None = None
    sms_api_key: str | None = None
    sms_sender_id: str | None = None
    # Optional public base URL used when SMS bodies include photo links.
    public_base_url: str | None = None
    # Background scheduler poll interval in seconds (in-process due-slot runner).
    campaign_scheduler_seconds: int = 30


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
DEFAULT_CAMPAIGN_PHOTO_DIR = Path("data/campaign-photos")
_ISSUE_PHOTO_SUBDIR = "issue-photos"
_CAMPAIGN_PHOTO_SUBDIR = "campaign-photos"
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


def resolve_campaign_photo_dir(explicit: str | None, database_url: str) -> Path:
    """Pick the campaign photo directory. Same rules as resolve_issue_photo_dir."""
    if explicit is not None and explicit.strip():
        return Path(explicit.strip())
    db_file = _sqlite_file_path(database_url)
    if db_file is not None:
        return db_file.parent / _CAMPAIGN_PHOTO_SUBDIR
    return DEFAULT_CAMPAIGN_PHOTO_DIR


def _resolve_path_setting(path: Path) -> str:
    if path.is_absolute():
        return path.as_posix()
    return (ROOT / path).resolve().as_posix()


def whatsapp_configured(settings: Settings) -> bool:
    token = (settings.whatsapp_token or "").strip()
    phone_id = (settings.whatsapp_phone_number_id or "").strip()
    return bool(token and phone_id)


def sms_configured(settings: Settings) -> bool:
    url = (settings.sms_api_url or "").strip()
    key = (settings.sms_api_key or "").strip()
    return bool(url and key)


def load_settings() -> Settings:
    settings = Settings()
    # Read before normalize_database_url assigns a field: only values the operator set count.
    explicit_photo_dir = (
        settings.issue_photo_dir if "issue_photo_dir" in settings.model_fields_set else None
    )
    explicit_campaign_dir = (
        settings.campaign_photo_dir
        if "campaign_photo_dir" in settings.model_fields_set
        else None
    )
    settings.database_url = normalize_database_url(settings.database_url)
    settings.issue_photo_dir = _resolve_path_setting(
        resolve_issue_photo_dir(explicit_photo_dir, settings.database_url)
    )
    settings.campaign_photo_dir = _resolve_path_setting(
        resolve_campaign_photo_dir(explicit_campaign_dir, settings.database_url)
    )
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
    if settings.campaign_scheduler_seconds < 5 or settings.campaign_scheduler_seconds > 3600:
        raise RuntimeError("CAMPAIGN_SCHEDULER_SECONDS must be between 5 and 3600")
