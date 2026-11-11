"""Auth, roles, and the rule that secrets are not committed defaults."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import load_settings
from tests.conftest import set_must_change, voter_payload


def test_health_and_voters_require_auth_except_health(ctx):
    bare = ctx.client
    bare.cookies.clear()
    assert bare.get("/healthz").status_code == 200
    assert bare.get("/healthz").json() == {"status": "ok"}
    assert "voter_id" not in bare.get("/healthz").text
    assert bare.get("/api/voters").status_code == 401
    assert bare.get("/api/reports/summary").status_code == 401
    assert bare.get("/api/sync/pull").status_code == 401
    assert bare.get("/api/users").status_code == 401


def test_bad_password_and_hash_storage(ctx):
    bad = ctx.client.post("/api/auth/login", json={"username": "admin", "password": "wrong-password-1"})
    assert bad.status_code == 401
    from app import database
    from app.models import User

    db = database.SessionLocal()
    try:
        user = db.query(User).one()
        assert user.password_hash.startswith("$2")
        assert "admin-pass-123" not in user.password_hash
    finally:
        db.close()
    me = ctx.client.get("/api/auth/me", headers=ctx.auth)
    assert me.status_code == 200
    assert "password" not in me.json()


def test_user_role_cannot_manage_users_but_can_add_voters(ctx):
    created = ctx.client.post(
        "/api/users",
        headers=ctx.auth,
        json={"username": "field.user", "password": "field-pass-123", "role": "user"},
    )
    assert created.status_code == 201, created.text
    assert "password" not in created.json()
    set_must_change("field.user", False)
    login = ctx.client.post(
        "/api/auth/login",
        json={"username": "field.user", "password": "field-pass-123"},
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert ctx.client.get("/api/users", headers=headers).status_code == 403
    admin_id = ctx.client.get("/api/auth/me", headers=ctx.auth).json()["id"]
    assert ctx.client.patch(f"/api/users/{admin_id}", headers=headers, json={"role": "user"}).status_code == 403
    added =ctx.client.post("/api/voters", headers=headers, json=voter_payload())
    assert added.status_code == 201, added.text


def test_cannot_demote_the_only_admin(ctx):
    me = ctx.client.get("/api/auth/me", headers=ctx.auth).json()
    denied = ctx.client.patch(f"/api/users/{me['id']}", headers=ctx.auth, json={"role": "user"})
    assert denied.status_code == 400


def test_placeholder_secret_is_rejected(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "change-me")
    monkeypatch.setenv("ADMIN_USERNAME", "")
    monkeypatch.setenv("ADMIN_PASSWORD", "")
    with pytest.raises(RuntimeError):
        load_settings()


def _env(monkeypatch, **values: str) -> None:
    base = {
        "SECRET_KEY": "test-secret-key-0123456789",
        "ADMIN_USERNAME": "",
        "ADMIN_PASSWORD": "",
        "APP_ENV": "test",
        "COOKIE_SECURE": "false",
    }
    base.update(values)
    for key, value in base.items():
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize("env_name", ["production", "PRODUCTION", " prod "])
def test_production_rejects_insecure_cookies(monkeypatch, env_name):
    _env(monkeypatch, APP_ENV=env_name, COOKIE_SECURE="false")
    with pytest.raises(RuntimeError, match="COOKIE_SECURE"):
        load_settings()


def test_production_accepts_secure_cookies(monkeypatch):
    _env(monkeypatch, APP_ENV="production", COOKIE_SECURE="true")
    settings = load_settings()
    assert settings.cookie_secure is True


def test_non_production_may_use_insecure_cookies(monkeypatch):
    for env_name in ("development", "test"):
        _env(monkeypatch, APP_ENV=env_name, COOKIE_SECURE="false")
        assert load_settings().cookie_secure is False


def test_create_app_refuses_to_start_in_production_without_secure_cookies(monkeypatch, tmp_path):
    _env(
        monkeypatch,
        APP_ENV="production",
        COOKIE_SECURE="false",
        DATABASE_URL="sqlite:///" + (tmp_path / "prod.db").as_posix(),
    )
    from app.main import create_app

    with pytest.raises(RuntimeError, match="COOKIE_SECURE"):
        create_app()


def test_session_cookie_is_secure_when_configured(monkeypatch, tmp_path):
    _env(
        monkeypatch,
        APP_ENV="production",
        COOKIE_SECURE="true",
        DATABASE_URL="sqlite:///" + (tmp_path / "secure.db").as_posix(),
        ADMIN_USERNAME="admin",
        ADMIN_PASSWORD="admin-pass-123",
        BCRYPT_ROUNDS="4",
    )
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass-123"})
        assert login.status_code == 200, login.text
        cookie = login.headers["set-cookie"].lower()
        assert "secure" in cookie
        assert "httponly" in cookie


def test_gitignore_covers_secrets_and_databases():
    text = Path(".gitignore").read_text(encoding="utf-8")
    assert ".env" in text
    assert "data/" in text
    assert "*.db" in text
