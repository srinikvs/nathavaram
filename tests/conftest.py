"""A fresh database and admin session for each test."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    db_path = tmp_path / "app.db"
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + db_path.as_posix())
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-0123456789")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass-123")
    monkeypatch.setenv("BCRYPT_ROUNDS", "4")
    monkeypatch.setenv("APP_ENV", "test")
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    with TestClient(app) as client:
        set_must_change("admin", False)  # normal session; force-change tests opt back in
        login = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin-pass-123"},
        )
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        yield SimpleNamespace(
            client=client,
            auth={"Authorization": f"Bearer {token}"},
            db_path=db_path,
        )


def search(ctx, **filters):
    """POST search with synthetic filters. Filters never go in the URL."""
    return ctx.client.post("/api/voters/search", headers=ctx.auth, json=filters)


def set_must_change(username: str, value: bool) -> None:
    """Flip must_change_password directly in the test database."""
    from app import database
    from app.models import User

    db = database.SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).one()
        user.must_change_password = value
        db.commit()
    finally:
        db.close()


def make_user(
    ctx,
    username: str = "field.user",
    password: str = "field-pass-123",
    role: str = "user",
    must_change: bool = False,
):
    """Create a user as the owner and return Bearer headers for that user.

    By default the forced-change flag is cleared so the session is usable.
    """
    created = ctx.client.post(
        "/api/users",
        headers=ctx.auth,
        json={"username": username, "password": password, "role": role},
    )
    assert created.status_code == 201, created.text
    set_must_change(username, must_change)
    login = ctx.client.post("/api/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    ctx.client.cookies.clear()  # keep tests on Bearer auth; no stray session cookie
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def csrf_from_page(html: str) -> str:
    import re

    match = re.search(r'name="csrf_token" value="([0-9a-f]+)"', html)
    assert match, "page has no csrf_token field"
    return match.group(1)


def voter_payload(**overrides) -> dict:
    data = {
        "booth_2024": "12A",
        "bs_no_2024": "101",
        "ward_2026": "3",
        "ward_s_no": "15",
        "voter_id": "ZZZ1000001",
        "first_name": "Testperson",
        "surname": "Alpha",
        "relative_name": "Relative Alpha",
        "house_no": "12-A",
        "gender": "Female",
        "age": 34,
        "caste": "SampleGroupA",
        "mobile": "9000000021",
        "c_city": "Nathavaram",
        "profession": "Teacher",
        "party": "TDP",
    }
    data.update(overrides)
    return data
