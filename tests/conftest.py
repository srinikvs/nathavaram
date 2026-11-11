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
