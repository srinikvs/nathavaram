"""Default location of issue photos when ISSUE_PHOTO_DIR is not set.

All paths are synthetic: temporary directories or obviously fake absolute paths. The pure
helper never touches the disk; the startup test writes only under pytest's tmp_path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import ROOT, load_settings, normalize_database_url, resolve_issue_photo_dir

DEFAULT_RELATIVE_PHOTO_DIR = Path("data/issue-photos")


def _env(monkeypatch, database_url: str, issue_photo_dir: str = "") -> None:
    # An empty ISSUE_PHOTO_DIR means "not set by the operator" and also overrides any local .env.
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("ISSUE_PHOTO_DIR", issue_photo_dir)
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-0123456789")
    monkeypatch.setenv("ADMIN_USERNAME", "")
    monkeypatch.setenv("ADMIN_PASSWORD", "")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("COOKIE_SECURE", "false")


def test_file_sqlite_url_puts_photos_next_to_the_database(tmp_path):
    # Arrange
    database_url = "sqlite:///" + (tmp_path / "nathavaram.db").as_posix()

    # Act
    photo_dir = resolve_issue_photo_dir(None, database_url)

    # Assert
    assert photo_dir == tmp_path / "issue-photos"


def test_production_style_absolute_sqlite_url_derives_var_lib_photo_dir():
    # Arrange
    database_url = "sqlite:////var/lib/nathavaram/nathavaram.db"

    # Act
    photo_dir = resolve_issue_photo_dir(None, database_url)

    # Assert
    assert photo_dir == Path("/var/lib/nathavaram/issue-photos")


def test_explicit_photo_dir_wins_over_database_derivation(tmp_path):
    # Arrange
    database_url = "sqlite:///" + (tmp_path / "nathavaram.db").as_posix()
    explicit = tmp_path / "elsewhere"

    # Act
    photo_dir = resolve_issue_photo_dir(str(explicit), database_url)

    # Assert
    assert photo_dir == explicit


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite:///:memory:",
        "sqlite:///file:shared?mode=memory&cache=shared",
        "sqlite:///",
        "postgresql://user:placeholder@localhost/nathavaram",
    ],
)
def test_non_file_database_urls_keep_the_relative_default(database_url):
    # Arrange / Act
    photo_dir = resolve_issue_photo_dir(None, database_url)

    # Assert
    assert photo_dir == DEFAULT_RELATIVE_PHOTO_DIR


def test_blank_explicit_value_is_treated_as_unset(tmp_path):
    # Arrange
    database_url = "sqlite:///" + (tmp_path / "nathavaram.db").as_posix()

    # Act
    photo_dir = resolve_issue_photo_dir("   ", database_url)

    # Assert
    assert photo_dir == tmp_path / "issue-photos"


def test_normalized_default_local_url_keeps_todays_location(monkeypatch):
    # Arrange
    monkeypatch.delenv("ISSUE_PHOTO_DIR", raising=False)
    database_url = normalize_database_url("sqlite:///./data/nathavaram.db")

    # Act
    photo_dir = resolve_issue_photo_dir(None, database_url)

    # Assert
    assert photo_dir == ROOT / "data" / "issue-photos"


def test_load_settings_env_override_beats_database_derivation(monkeypatch, tmp_path):
    # Arrange
    explicit = tmp_path / "explicit-photos"
    _env(
        monkeypatch,
        database_url="sqlite:///" + (tmp_path / "db" / "nathavaram.db").as_posix(),
        issue_photo_dir=str(explicit),
    )

    # Act
    settings = load_settings()

    # Assert
    assert settings.issue_photo_dir == explicit.as_posix()


def test_load_settings_derives_photo_dir_from_sqlite_file(monkeypatch, tmp_path):
    # Arrange
    db_dir = tmp_path / "service-data"
    _env(monkeypatch, database_url="sqlite:///" + (db_dir / "nathavaram.db").as_posix())

    # Act
    settings = load_settings()

    # Assert
    assert settings.issue_photo_dir == (db_dir / "issue-photos").as_posix()


def test_load_settings_relative_explicit_path_resolves_against_root(monkeypatch):
    # Arrange
    _env(monkeypatch, database_url="sqlite:///:memory:", issue_photo_dir="custom/photos")

    # Act
    settings = load_settings()

    # Assert
    assert settings.issue_photo_dir == (ROOT / "custom" / "photos").resolve().as_posix()


@pytest.mark.parametrize(
    "database_url",
    ["postgresql://user:placeholder@localhost/nathavaram", "sqlite:///:memory:"],
)
def test_load_settings_without_file_database_uses_relative_default(monkeypatch, database_url):
    # Arrange
    _env(monkeypatch, database_url=database_url)

    # Act
    settings = load_settings()

    # Assert
    assert settings.issue_photo_dir == (ROOT / DEFAULT_RELATIVE_PHOTO_DIR).resolve().as_posix()


def test_app_startup_creates_the_configured_photo_dir(monkeypatch, tmp_path):
    # Arrange
    from fastapi.testclient import TestClient

    from app.main import create_app

    photo_dir = tmp_path / "fresh" / "issue-photos"
    _env(
        monkeypatch,
        database_url="sqlite:///" + (tmp_path / "fresh" / "nathavaram.db").as_posix(),
        issue_photo_dir=str(photo_dir),
    )
    app = create_app()
    assert not photo_dir.exists()

    # Act
    with TestClient(app):
        pass

    # Assert
    assert photo_dir.is_dir()
