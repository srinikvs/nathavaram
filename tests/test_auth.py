"""Auth, roles, and the rule that secrets are not committed defaults."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import load_settings
from tests.conftest import voter_payload


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
    login = ctx.client.post(
        "/api/auth/login",
        json={"username": "field.user", "password": "field-pass-123"},
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert ctx.client.get("/api/users", headers=headers).status_code == 403
    added = ctx.client.post("/api/voters", headers=headers, json=voter_payload())
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


def test_gitignore_covers_secrets_and_databases():
    text = Path(".gitignore").read_text(encoding="utf-8")
    assert ".env" in text
    assert "data/" in text
    assert "*.db" in text
