"""Login throttling (R1), uniform login errors (M2), forced password change (P1), owner flag (O1).

Synthetic usernames and passwords only.
"""

from __future__ import annotations

import pytest

from app.config import ROOT
from app.services.throttle import LoginThrottle, normalize_username
from tests.conftest import csrf_from_page, make_user, set_must_change, voter_payload

SAMPLE = ROOT / "sample" / "voters_sample.csv"
GOOD = "rate-pass-12345"
BAD = "definitely-wrong-1"


@pytest.fixture
def peer(monkeypatch):
    """Control the client IP the login limiter sees (TestClient always reports one peer)."""
    state = {"ip": "10.0.0.1"}
    monkeypatch.setattr("app.services.login.client_ip", lambda request: state["ip"])

    def set_ip(value: str) -> None:
        state["ip"] = value

    return set_ip


def _api_login(ctx, username: str, password: str):
    return ctx.client.post("/api/auth/login", json={"username": username, "password": password})


def _user_row(username: str):
    from app import database
    from app.models import User

    db = database.SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).one()
        db.expunge(user)
        return user
    finally:
        db.close()


# ---------------------------------------------------------------- M2: uniform errors


def test_unknown_user_and_wrong_password_return_the_same_error(ctx, peer):
    make_user(ctx, "rateuser1", GOOD)

    unknown = _api_login(ctx, "nobody123", BAD)
    known = _api_login(ctx, "rateuser1", BAD)

    assert unknown.status_code == known.status_code == 401
    assert unknown.json() == known.json() == {"detail": "Invalid username or password"}


def test_inactive_user_gets_the_same_error_as_unknown(ctx, peer):
    make_user(ctx, "rateuser1", GOOD)
    user_id = _user_row("rateuser1").id
    off = ctx.client.patch(f"/api/users/{user_id}", headers=ctx.auth, json={"is_active": False})
    assert off.status_code == 200

    inactive = _api_login(ctx, "rateuser1", GOOD)
    unknown = _api_login(ctx, "nobody123", GOOD)

    assert inactive.status_code == unknown.status_code == 401
    assert inactive.json() == unknown.json()


def test_unknown_username_still_spends_a_password_verify(ctx, peer, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr("app.services.users.dummy_verify", lambda password, rounds: calls.append(password))

    _api_login(ctx, "nobody123", BAD)

    assert calls == [BAD]


def test_web_login_error_is_identical_for_unknown_and_wrong_password(ctx, peer):
    make_user(ctx, "rateuser1", GOOD)
    ctx.client.cookies.clear()

    unknown = ctx.client.post("/login", data={"username": "nobody123", "password": BAD})
    known = ctx.client.post("/login", data={"username": "rateuser1", "password": BAD})

    assert unknown.status_code == known.status_code == 401
    assert "Invalid username or password" in unknown.text
    assert "Invalid username or password" in known.text


# ---------------------------------------------------------------- R1: throttling


def test_sixth_failure_for_a_nonexistent_username_is_429_with_retry_after(ctx, peer):
    statuses = [_api_login(ctx, "nobody123", BAD).status_code for _ in range(5)]
    blocked = _api_login(ctx, "nobody123", BAD)

    assert statuses == [401] * 5
    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["retry-after"]) <= 300
    assert "try again later" in blocked.json()["detail"].lower()


def test_sixth_attempt_for_a_real_user_is_429_even_with_the_right_password(ctx, peer):
    make_user(ctx, "rateuser1", GOOD)
    peer("10.0.0.9")

    statuses = [_api_login(ctx, "rateuser1", BAD).status_code for _ in range(5)]
    blocked = _api_login(ctx, "rateuser1", GOOD)

    assert statuses == [401] * 5
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) >= 1


def test_repeated_failures_from_one_ip_block_that_ip_only(ctx, peer):
    peer("10.0.0.2")
    statuses = [_api_login(ctx, f"nobody{n}", BAD).status_code for n in range(5)]
    blocked = _api_login(ctx, "admin", "admin-pass-123")

    assert statuses == [401] * 5
    assert blocked.status_code == 429
    assert "retry-after" in blocked.headers

    peer("10.0.0.3")
    assert _api_login(ctx, "admin", "admin-pass-123").status_code == 200


def test_failures_for_one_username_from_many_ips_block_that_username_only(ctx, peer):
    make_user(ctx, "rateuser1", GOOD)
    for n in range(5):
        peer(f"10.1.0.{n}")
        assert _api_login(ctx, "rateuser1", BAD).status_code == 401

    peer("10.1.0.99")
    blocked = _api_login(ctx, "rateuser1", GOOD)
    other = _api_login(ctx, "admin", "admin-pass-123")

    assert blocked.status_code == 429
    assert other.status_code == 200


def test_username_key_ignores_case_and_surrounding_spaces(ctx, peer):
    spellings = ["rateuser1", "RATEUSER1", " rateuser1 ", "RateUser1", "rateUSER1"]
    for n, name in enumerate(spellings):
        peer(f"10.2.0.{n}")
        assert _api_login(ctx, name, BAD).status_code == 401

    peer("10.2.0.99")
    assert _api_login(ctx, "  RATEUSER1", BAD).status_code == 429


def test_success_under_the_limit_works_and_clears_the_username_count(ctx, peer):
    make_user(ctx, "rateuser1", GOOD)
    for n in range(4):
        peer(f"10.3.0.{n}")
        assert _api_login(ctx, "rateuser1", BAD).status_code == 401

    peer("10.3.0.50")
    ok = _api_login(ctx, "rateuser1", GOOD)
    assert ok.status_code == 200

    # Count was cleared: five more failures are allowed before the next block.
    statuses = []
    for n in range(5):
        peer(f"10.4.0.{n}")
        statuses.append(_api_login(ctx, "rateuser1", BAD).status_code)
    peer("10.4.0.99")
    assert statuses == [401] * 5
    assert _api_login(ctx, "rateuser1", GOOD).status_code == 429


def test_four_failures_then_success_from_one_ip_is_allowed(ctx, peer):
    peer("10.5.0.1")
    for _ in range(4):
        assert _api_login(ctx, "admin", BAD).status_code == 401

    assert _api_login(ctx, "admin", "admin-pass-123").status_code == 200


def test_web_login_is_throttled_with_retry_after(ctx, peer):
    ctx.client.cookies.clear()
    peer("10.6.0.1")
    statuses = [
        ctx.client.post("/login", data={"username": "nobody123", "password": BAD}).status_code
        for _ in range(5)
    ]
    blocked = ctx.client.post("/login", data={"username": "nobody123", "password": BAD})

    assert statuses == [401] * 5
    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["retry-after"]) <= 300


def test_failed_current_password_on_change_password_is_throttled(ctx, peer):
    headers = make_user(ctx, "rateuser1", GOOD)
    peer("10.7.0.1")
    body = {"current_password": BAD, "new_password": "another-pass-678"}

    statuses = [
        ctx.client.post("/api/auth/change-password", headers=headers, json=body).status_code
        for _ in range(5)
    ]
    blocked = ctx.client.post("/api/auth/change-password", headers=headers, json=body)

    assert statuses == [400] * 5
    assert blocked.status_code == 429
    assert "retry-after" in blocked.headers


def test_throttle_window_expires_and_reports_seconds_left():
    class Clock:
        now = 1000.0

        def __call__(self) -> float:
            return self.now

    clock = Clock()
    throttle = LoginThrottle(clock=clock)
    for _ in range(5):
        assert throttle.begin("1.1.1.1", "rateuser1")[0] is not None

    clock.now = 1100.0
    ticket, retry_after = throttle.begin("1.1.1.1", "rateuser1")
    assert ticket is None
    assert retry_after == 200

    clock.now = 1301.0
    assert throttle.begin("1.1.1.1", "rateuser1")[0] is not None


def test_throttle_success_keeps_other_failures_from_the_same_ip():
    throttle = LoginThrottle()
    tickets = [throttle.begin("1.1.1.1", "rateuser1")[0] for _ in range(4)]
    assert all(ticket is not None for ticket in tickets)

    throttle.succeed("1.1.1.1", "rateuser1", tickets[-1])

    # Three failures from the IP remain, so only two more fit.
    assert throttle.begin("1.1.1.1", "rateuser2")[0] is not None
    assert throttle.begin("1.1.1.1", "rateuser3")[0] is not None
    assert throttle.begin("1.1.1.1", "rateuser4")[0] is None


def test_normalize_username_trims_and_casefolds():
    assert normalize_username("  RateUser1 ") == "rateuser1"


# ---------------------------------------------------------------- P1: forced password change


def test_new_users_and_resets_and_the_seeded_admin_must_change_password(ctx, peer):
    created = ctx.client.post(
        "/api/users",
        headers=ctx.auth,
        json={"username": "rateuser1", "password": GOOD, "role": "user"},
    )
    assert created.status_code == 201
    assert created.json()["must_change_password"] is True

    set_must_change("rateuser1", False)
    reset = ctx.client.patch(
        f"/api/users/{created.json()['id']}", headers=ctx.auth, json={"password": "reset-pass-12345"}
    )
    assert reset.status_code == 200
    assert reset.json()["must_change_password"] is True


def test_seeded_env_admin_starts_with_must_change_and_owner(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + (tmp_path / "seed.db").as_posix())
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-0123456789")
    monkeypatch.setenv("ADMIN_USERNAME", "seedowner")
    monkeypatch.setenv("ADMIN_PASSWORD", "seed-pass-123456")
    monkeypatch.setenv("BCRYPT_ROUNDS", "4")
    monkeypatch.setenv("APP_ENV", "test")
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        row = _user_row("seedowner")
        login = client.post(
            "/api/auth/login", json={"username": "seedowner", "password": "seed-pass-123456"}
        )

    assert row.must_change_password is True
    assert row.is_owner is True
    assert login.status_code == 200
    assert login.json()["must_change_password"] is True


def test_pending_change_blocks_every_api_route_except_change_password_and_logout(ctx, peer):
    headers = make_user(ctx, "rateuser1", GOOD, must_change=True)

    blocked = [
        ctx.client.get("/api/auth/me", headers=headers),
        ctx.client.get("/api/voters", headers=headers),
        ctx.client.post("/api/voters", headers=headers, json=voter_payload()),
        ctx.client.post("/api/voters/search", headers=headers, json={}),
        ctx.client.get("/api/reports/summary", headers=headers),
        ctx.client.get("/api/sync/pull", headers=headers),
        ctx.client.post("/api/sync/push", headers=headers, json={"device_id": "lab-1", "changes": []}),
    ]

    for response in blocked:
        assert response.status_code == 403, response.request.url
        assert response.json()["code"] == "password_change_required"
    assert ctx.client.get("/healthz").status_code == 200
    assert ctx.client.post("/api/auth/logout", headers=headers).status_code == 200


def test_login_succeeds_and_reports_the_pending_change(ctx, peer):
    make_user(ctx, "rateuser1", GOOD, must_change=True)

    login = _api_login(ctx, "rateuser1", GOOD)

    assert login.status_code == 200
    assert login.json()["must_change_password"] is True


def test_change_password_unlocks_the_session(ctx, peer):
    headers = make_user(ctx, "rateuser1", GOOD, must_change=True)

    changed = ctx.client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"current_password": GOOD, "new_password": "brand-new-pass-1"},
    )

    assert changed.status_code == 200
    assert changed.json()["must_change_password"] is False
    assert ctx.client.get("/api/voters", headers=headers).status_code == 200
    assert _api_login(ctx, "rateuser1", GOOD).status_code == 401
    assert _api_login(ctx, "rateuser1", "brand-new-pass-1").status_code == 200


def test_change_password_rejects_same_as_old(ctx, peer):
    headers = make_user(ctx, "rateuser1", GOOD, must_change=True)

    same = ctx.client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"current_password": GOOD, "new_password": GOOD},
    )

    assert same.status_code == 400
    assert ctx.client.get("/api/voters", headers=headers).status_code == 403


def test_change_password_rejects_short_new_password(ctx, peer):
    headers = make_user(ctx, "rateuser1", GOOD, must_change=True)

    short = ctx.client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"current_password": GOOD, "new_password": "short1"},
    )

    assert short.status_code == 422
    assert ctx.client.get("/api/voters", headers=headers).status_code == 403


def test_change_password_rejects_wrong_current_password(ctx, peer):
    headers = make_user(ctx, "rateuser1", GOOD, must_change=True)

    wrong = ctx.client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"current_password": BAD, "new_password": "brand-new-pass-1"},
    )

    assert wrong.status_code == 400
    assert _user_row("rateuser1").must_change_password is True


def test_web_login_redirects_to_change_password_and_pages_are_gated(ctx, peer):
    make_user(ctx, "rateuser1", GOOD, must_change=True)
    ctx.client.cookies.clear()

    signed = ctx.client.post(
        "/login",
        data={"username": "rateuser1", "password": GOOD, "next": "/reports"},
        follow_redirects=False,
    )
    assert signed.status_code == 303
    assert signed.headers["location"] == "/change-password"

    gated = ctx.client.get("/voters", follow_redirects=False)
    assert gated.status_code == 303
    assert gated.headers["location"] == "/change-password"

    page = ctx.client.get("/change-password")
    assert page.status_code == 200
    token = csrf_from_page(page.text)

    short = ctx.client.post(
        "/change-password",
        data={"csrf_token": token, "current_password": GOOD, "new_password": "short1"},
    )
    assert short.status_code == 400
    same = ctx.client.post(
        "/change-password",
        data={"csrf_token": token, "current_password": GOOD, "new_password": GOOD},
    )
    assert same.status_code == 400

    done = ctx.client.post(
        "/change-password",
        data={"csrf_token": token, "current_password": GOOD, "new_password": "brand-new-pass-1"},
        follow_redirects=False,
    )
    assert done.status_code == 303
    assert done.headers["location"] == "/voters"
    assert ctx.client.get("/voters", follow_redirects=False).status_code == 200


# ---------------------------------------------------------------- O1: owner flag


def test_owner_can_create_a_user_and_the_new_user_is_not_an_owner(ctx):
    created = ctx.client.post(
        "/api/users",
        headers=ctx.auth,
        json={"username": "rateuser1", "password": GOOD, "role": "admin"},
    )

    assert created.status_code == 201
    assert created.json()["is_owner"] is False
    listed = {row["username"]: row["is_owner"] for row in ctx.client.get("/api/users", headers=ctx.auth).json()}
    assert listed == {"admin": True, "rateuser1": False}


def test_regular_admin_cannot_manage_users(ctx):
    plain = make_user(ctx, "plainadmin", GOOD, role="admin")
    other_id = _user_row("plainadmin").id

    assert ctx.client.get("/api/users", headers=plain).status_code == 403
    assert (
        ctx.client.post(
            "/api/users",
            headers=plain,
            json={"username": "rateuser2", "password": GOOD, "role": "user"},
        ).status_code
        == 403
    )
    assert (
        ctx.client.patch(f"/api/users/{other_id}", headers=plain, json={"password": GOOD + "x"}).status_code
        == 403
    )


def test_regular_admin_keeps_export_import_and_delete(ctx):
    plain = make_user(ctx, "plainadmin", GOOD, role="admin")
    created = ctx.client.post("/api/voters", headers=plain, json=voter_payload())
    assert created.status_code == 201

    assert ctx.client.post("/api/voters/export.csv", headers=plain).status_code == 200
    imported = ctx.client.post(
        "/api/voters/import.csv",
        headers=plain,
        files={"file": ("voters_sample.csv", SAMPLE.read_bytes(), "text/csv")},
    )
    assert imported.status_code == 200
    assert ctx.client.delete(f"/api/voters/{created.json()['id']}", headers=plain).status_code == 200


def test_regular_user_cannot_manage_users(ctx):
    user = make_user(ctx, "rateuser1", GOOD)

    assert ctx.client.get("/api/users", headers=user).status_code == 403
    assert (
        ctx.client.post(
            "/api/users",
            headers=user,
            json={"username": "rateuser2", "password": GOOD, "role": "user"},
        ).status_code
        == 403
    )


def test_web_users_page_is_owner_only(ctx, peer):
    make_user(ctx, "plainadmin", GOOD, role="admin")
    ctx.client.cookies.clear()
    ctx.client.post("/login", data={"username": "plainadmin", "password": GOOD}, follow_redirects=False)

    page = ctx.client.get("/voters")
    assert 'href="/users"' not in page.text
    assert ctx.client.get("/users").status_code == 403

    token = csrf_from_page(page.text)
    posted = ctx.client.post(
        "/users", data={"csrf_token": token, "username": "rateuser2", "password": GOOD, "role": "user"}
    )
    assert posted.status_code == 403

    ctx.client.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    ctx.client.post("/login", data={"username": "admin", "password": "admin-pass-123"}, follow_redirects=False)
    assert ctx.client.get("/users").status_code == 200


def test_startup_marks_an_existing_env_admin_as_owner(ctx):
    from app import database
    from app.models import User
    from app.services.bootstrap import seed_admin

    db = database.SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "admin").one()
        admin.is_owner = False
        db.commit()
        seed_admin(db, ctx.client.app.state.settings)
    finally:
        db.close()

    assert _user_row("admin").is_owner is True
