"""Forced first-login change, change-password nav, owner reset/delete, session revocation.

Synthetic usernames and passwords only.
"""

from __future__ import annotations

from tests.conftest import csrf_from_page, make_user, set_must_change

TEMP_PW = "temp-pass-0001"
NEW_PW = "brand-new-pass-1"
OTHER_PW = "another-pass-22"


def _create(ctx, username="qa_user1", password=TEMP_PW, role="user"):
    resp = ctx.client.post(
        "/api/users", headers=ctx.auth, json={"username": username, "password": password, "role": role}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _api_login(ctx, username, password):
    ctx.client.cookies.clear()
    resp = ctx.client.post("/api/auth/login", json={"username": username, "password": password})
    ctx.client.cookies.clear()
    return resp


def _bearer(ctx, username, password):
    resp = _api_login(ctx, username, password)
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _web_login(ctx, username, password):
    ctx.client.cookies.clear()
    return ctx.client.post(
        "/login", data={"username": username, "password": password}, follow_redirects=False
    )


def _web_csrf(ctx, path="/change-password"):
    page = ctx.client.get(path)
    assert page.status_code == 200, page.status_code
    return csrf_from_page(page.text)


def _owner_web(ctx):
    resp = _web_login(ctx, "admin", "admin-pass-123")
    assert resp.status_code == 303
    return _web_csrf(ctx, "/users")


# ---- 1. new users are forced to change password ---------------------------------


def test_api_created_user_is_flagged_and_client_false_is_not_honored(ctx):
    created = _create(ctx, "qa_user1")
    assert created["must_change_password"] is True
    forged = ctx.client.post(
        "/api/users",
        headers=ctx.auth,
        json={"username": "qa_user2", "password": TEMP_PW, "must_change_password": False},
    )
    assert forged.status_code == 422


def test_api_create_rejects_short_password(ctx):
    resp = ctx.client.post(
        "/api/users", headers=ctx.auth, json={"username": "qa_short1", "password": "123456789"}
    )
    assert resp.status_code in (400, 422)


def test_create_user_service_enforces_min_length(ctx):
    import pytest

    from app import database
    from app.config import load_settings
    from app.errors import ServiceError
    from app.schemas import UserCreate
    from app.models import User
    from app.services.users import create_user

    data = UserCreate.model_construct(username="qa_short2", password="short", role="user")
    db = database.SessionLocal()
    try:
        owner = db.query(User).filter(User.username == "admin").one()
        with pytest.raises(ServiceError):
            create_user(db, data, load_settings(), owner)
    finally:
        db.close()


def test_api_new_user_blocked_from_data_until_password_changed(ctx):
    _create(ctx, "qa_user3")
    headers = _bearer(ctx, "qa_user3", TEMP_PW)
    for path in ("/api/voters", "/api/sync/pull", "/api/auth/me"):
        resp = ctx.client.get(path, headers=headers)
        assert resp.status_code == 403, path
        assert resp.json()["code"] == "password_change_required"
    push = ctx.client.post("/api/sync/push", headers=headers, json={"changes": []})
    assert push.status_code == 403
    assert push.json()["code"] == "password_change_required"

    changed = ctx.client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"current_password": TEMP_PW, "new_password": NEW_PW},
    )
    assert changed.status_code == 200
    fresh = {"Authorization": f"Bearer {changed.json()['access_token']}"}
    assert ctx.client.get("/api/voters", headers=fresh).status_code == 200
    assert ctx.client.get("/api/sync/pull", headers=fresh).status_code == 200


def test_web_created_user_gated_everywhere_until_change(ctx):
    csrf = _owner_web(ctx)
    created = ctx.client.post(
        "/users",
        data={"username": "fielduser2", "password": TEMP_PW, "role": "user", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert created.status_code == 303

    login = _web_login(ctx, "fielduser2", TEMP_PW)
    assert login.status_code == 303
    assert login.headers["location"] == "/change-password"

    for path in ("/voters", "/reports", "/voters/new"):
        resp = ctx.client.get(path, follow_redirects=False)
        assert resp.status_code == 303 and resp.headers["location"] == "/change-password", path
    assert ctx.client.get("/api/voters").status_code == 403
    for path in ("/", "/login", "/login?next=/voters"):
        resp = ctx.client.get(path, follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/change-password", path

    csrf = _web_csrf(ctx)
    done = ctx.client.post(
        "/change-password",
        data={"current_password": TEMP_PW, "new_password": NEW_PW, "csrf_token": csrf},
        follow_redirects=False,
    )
    assert done.status_code == 303
    assert ctx.client.get("/voters").status_code == 200
    assert ctx.client.get("/", follow_redirects=False).headers["location"] == "/voters"


def test_new_user_created_through_form_has_flag_set(ctx):
    csrf = _owner_web(ctx)
    resp = ctx.client.post(
        "/users",
        data={"username": "qa_user4", "password": TEMP_PW, "role": "user", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    listing = ctx.client.get("/api/users", headers=ctx.auth).json()
    row = next(u for u in listing if u["username"] == "qa_user4")
    assert row["must_change_password"] is True


# ---- 2. change password for everyone --------------------------------------------


def test_nav_has_change_password_link_for_user_and_owner(ctx):
    make_user(ctx, "qa_user5", TEMP_PW)
    assert _web_login(ctx, "qa_user5", TEMP_PW).status_code == 303
    assert 'href="/change-password"' in ctx.client.get("/voters").text
    assert _web_login(ctx, "admin", "admin-pass-123").status_code == 303
    assert 'href="/change-password"' in ctx.client.get("/voters").text


def test_voluntary_web_change_works_and_keeps_session(ctx):
    make_user(ctx, "qa_user6", TEMP_PW)
    _web_login(ctx, "qa_user6", TEMP_PW)
    csrf = _web_csrf(ctx)
    resp = ctx.client.post(
        "/change-password",
        data={"current_password": TEMP_PW, "new_password": NEW_PW, "csrf_token": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert ctx.client.get("/voters").status_code == 200  # re-issued cookie
    assert _api_login(ctx, "qa_user6", TEMP_PW).status_code == 401
    assert _api_login(ctx, "qa_user6", NEW_PW).status_code == 200


def test_web_change_password_errors_are_visible(ctx):
    make_user(ctx, "qa_user7", TEMP_PW)
    _web_login(ctx, "qa_user7", TEMP_PW)
    csrf = _web_csrf(ctx)
    cases = (
        {"current_password": "wrong-pass-0000", "new_password": NEW_PW},
        {"current_password": TEMP_PW, "new_password": "short"},
        {"current_password": TEMP_PW, "new_password": TEMP_PW},
    )
    for fields in cases:
        resp = ctx.client.post("/change-password", data={**fields, "csrf_token": csrf})
        assert resp.status_code == 400
        assert 'role="alert"' in resp.text


def test_api_change_password_rejections(ctx):
    headers = make_user(ctx, "qa_user8", TEMP_PW)
    url = "/api/auth/change-password"
    wrong = ctx.client.post(url, headers=headers, json={"current_password": "nope-nope-00", "new_password": NEW_PW})
    assert wrong.status_code == 400
    same = ctx.client.post(url, headers=headers, json={"current_password": TEMP_PW, "new_password": TEMP_PW})
    assert same.status_code == 400
    short = ctx.client.post(url, headers=headers, json={"current_password": TEMP_PW, "new_password": "tiny"})
    assert short.status_code in (400, 422)


def test_change_password_cookie_post_requires_csrf(ctx):
    make_user(ctx, "qa_user9", TEMP_PW)
    _web_login(ctx, "qa_user9", TEMP_PW)
    resp = ctx.client.post(
        "/change-password", data={"current_password": TEMP_PW, "new_password": NEW_PW}
    )
    assert resp.status_code == 403


# ---- 3. owner reset / delete ----------------------------------------------------


def test_owner_reset_forces_change_and_login_works_with_temp(ctx):
    user = _create(ctx, "qa_user10")
    set_must_change("qa_user10", False)
    resp = ctx.client.post(
        f"/api/users/{user['id']}/reset-password", headers=ctx.auth, json={"new_password": OTHER_PW}
    )
    assert resp.status_code == 200
    assert resp.json()["must_change_password"] is True
    assert _api_login(ctx, "qa_user10", TEMP_PW).status_code == 401
    headers = _bearer(ctx, "qa_user10", OTHER_PW)
    blocked = ctx.client.get("/api/voters", headers=headers)
    assert blocked.status_code == 403 and blocked.json()["code"] == "password_change_required"


def test_reset_short_password_rejected(ctx):
    user = _create(ctx, "qa_user11")
    resp = ctx.client.post(
        f"/api/users/{user['id']}/reset-password", headers=ctx.auth, json={"new_password": "short"}
    )
    assert resp.status_code in (400, 422)


def test_owner_cannot_reset_self(ctx):
    me = next(u for u in ctx.client.get("/api/users", headers=ctx.auth).json() if u["username"] == "admin")
    resp = ctx.client.post(
        f"/api/users/{me['id']}/reset-password", headers=ctx.auth, json={"new_password": OTHER_PW}
    )
    assert resp.status_code == 400


def test_reset_clears_username_login_throttle(ctx):
    from app.services.throttle import normalize_username

    user = _create(ctx, "qa_user12")
    for _ in range(3):
        assert _api_login(ctx, "qa_user12", "wrong-pass-0000").status_code == 401
    throttle = ctx.client.app.state.login_throttle
    assert normalize_username("qa_user12") in throttle._by_user
    ctx.client.post(
        f"/api/users/{user['id']}/reset-password", headers=ctx.auth, json={"new_password": OTHER_PW}
    )
    assert normalize_username("qa_user12") not in throttle._by_user


def test_owner_delete_blocks_login_and_hides_user(ctx):
    user = _create(ctx, "qa_user13")
    resp = ctx.client.delete(f"/api/users/{user['id']}", headers=ctx.auth)
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False and resp.json()["deleted_at"]
    assert _api_login(ctx, "qa_user13", TEMP_PW).status_code == 401
    names = [u["username"] for u in ctx.client.get("/api/users", headers=ctx.auth).json()]
    assert "qa_user13" not in names
    again = ctx.client.post(
        "/api/users", headers=ctx.auth, json={"username": "qa_user13", "password": TEMP_PW}
    )
    assert again.status_code == 409  # username stays reserved


def test_owner_cannot_delete_self_or_other_owner(ctx):
    me = next(u for u in ctx.client.get("/api/users", headers=ctx.auth).json() if u["username"] == "admin")
    assert ctx.client.delete(f"/api/users/{me['id']}", headers=ctx.auth).status_code == 400
    other = _create(ctx, "qa_owner2", role="admin")
    from app import database
    from app.models import User

    db = database.SessionLocal()
    try:
        db.query(User).filter(User.username == "qa_owner2").one().is_owner = True
        db.commit()
    finally:
        db.close()
    assert ctx.client.delete(f"/api/users/{other['id']}", headers=ctx.auth).status_code == 400


def test_user_and_manager_get_403_and_a_non_owner_admin_manages_accounts(ctx):
    target = _create(ctx, "qa_user14")
    denied = (
        make_user(ctx, "qa_user15", TEMP_PW),
        make_user(ctx, "qa_manager1", TEMP_PW, role="manager"),
    )
    for headers in denied:
        reset = ctx.client.post(
            f"/api/users/{target['id']}/reset-password", headers=headers, json={"new_password": OTHER_PW}
        )
        assert reset.status_code == 403
        assert ctx.client.delete(f"/api/users/{target['id']}", headers=headers).status_code == 403
        assert ctx.client.get("/api/users", headers=headers).status_code == 403
    admin2 = make_user(ctx, "qa_admin2", TEMP_PW, role="admin")

    reset = ctx.client.post(
        f"/api/users/{target['id']}/reset-password", headers=admin2, json={"new_password": OTHER_PW}
    )

    assert reset.status_code == 200, reset.text
    assert ctx.client.delete(f"/api/users/{target['id']}", headers=admin2).status_code == 200


def test_cookie_post_and_delete_require_csrf(ctx):
    user = _create(ctx, "qa_user16")
    _owner_web(ctx)
    assert ctx.client.delete(f"/api/users/{user['id']}").status_code == 403
    assert (
        ctx.client.post(f"/api/users/{user['id']}/reset-password", json={"new_password": OTHER_PW}).status_code
        == 403
    )
    assert ctx.client.post(f"/users/{user['id']}/delete", data={"confirm": "yes"}).status_code == 403
    assert (
        ctx.client.post(f"/users/{user['id']}/reset-password", data={"new_password": OTHER_PW}).status_code
        == 403
    )


def test_web_reset_and_delete_forms(ctx):
    user = _create(ctx, "qa_user17")
    csrf = _owner_web(ctx)
    page = ctx.client.get("/users").text
    assert "Reset password" in page and "data-confirm" in page

    short = ctx.client.post(
        f"/users/{user['id']}/reset-password", data={"new_password": "short", "csrf_token": csrf}
    )
    assert short.status_code == 400 and 'role="alert"' in short.text

    ok = ctx.client.post(
        f"/users/{user['id']}/reset-password",
        data={"new_password": OTHER_PW, "csrf_token": csrf},
        follow_redirects=False,
    )
    assert ok.status_code == 303
    unconfirmed = ctx.client.post(f"/users/{user['id']}/delete", data={"csrf_token": csrf})
    assert unconfirmed.status_code == 400
    deleted = ctx.client.post(
        f"/users/{user['id']}/delete", data={"confirm": "yes", "csrf_token": csrf}, follow_redirects=False
    )
    assert deleted.status_code == 303
    assert _api_login(ctx, "qa_user17", OTHER_PW).status_code == 401


def test_web_user_management_forbidden_for_manager_and_user(ctx):
    target = _create(ctx, "qa_user18")
    make_user(ctx, "qa_manager2", TEMP_PW, role="manager")
    _web_login(ctx, "qa_manager2", TEMP_PW)
    csrf = _web_csrf(ctx, "/voters")
    assert ctx.client.get("/users").status_code == 403
    for path, data in (
        (f"/users/{target['id']}/reset-password", {"new_password": OTHER_PW}),
        (f"/users/{target['id']}/delete", {"confirm": "yes"}),
    ):
        assert ctx.client.post(path, data={**data, "csrf_token": csrf}).status_code == 403


# ---- 4. session revocation ------------------------------------------------------


def _cookie_and_bearer(ctx, username, password):
    """Return (cookie_value, bearer_headers) from two separate logins."""
    resp = _web_login(ctx, username, password)
    cookie = resp.cookies.get(ctx.client.app.state.settings.cookie_name)
    assert cookie
    ctx.client.cookies.clear()
    return cookie, _bearer(ctx, username, password)


def _cookie_status(ctx, cookie, path="/api/auth/me"):
    ctx.client.cookies.clear()
    ctx.client.cookies.set(ctx.client.app.state.settings.cookie_name, cookie)
    status = ctx.client.get(path).status_code
    ctx.client.cookies.clear()
    return status


def test_tokens_die_after_owner_reset(ctx):
    user = _create(ctx, "qa_user19")
    set_must_change("qa_user19", False)
    cookie, bearer = _cookie_and_bearer(ctx, "qa_user19", TEMP_PW)
    assert _cookie_status(ctx, cookie) == 200
    assert ctx.client.get("/api/auth/me", headers=bearer).status_code == 200
    ctx.client.post(
        f"/api/users/{user['id']}/reset-password", headers=ctx.auth, json={"new_password": OTHER_PW}
    )
    assert _cookie_status(ctx, cookie) == 401
    assert ctx.client.get("/api/auth/me", headers=bearer).status_code == 401


def test_tokens_die_after_owner_delete(ctx):
    user = _create(ctx, "qa_user20")
    set_must_change("qa_user20", False)
    cookie, bearer = _cookie_and_bearer(ctx, "qa_user20", TEMP_PW)
    ctx.client.delete(f"/api/users/{user['id']}", headers=ctx.auth)
    assert _cookie_status(ctx, cookie) == 401
    assert ctx.client.get("/api/auth/me", headers=bearer).status_code == 401


def test_tokens_die_after_owner_deactivate(ctx):
    user = _create(ctx, "qa_user21")
    set_must_change("qa_user21", False)
    _, bearer = _cookie_and_bearer(ctx, "qa_user21", TEMP_PW)
    resp = ctx.client.patch(f"/api/users/{user['id']}", headers=ctx.auth, json={"is_active": False})
    assert resp.status_code == 200
    assert ctx.client.get("/api/auth/me", headers=bearer).status_code == 401


def test_other_sessions_die_after_own_password_change(ctx):
    _create(ctx, "qa_user22")
    set_must_change("qa_user22", False)
    old_cookie, old_bearer = _cookie_and_bearer(ctx, "qa_user22", TEMP_PW)
    acting = _bearer(ctx, "qa_user22", TEMP_PW)
    changed = ctx.client.post(
        "/api/auth/change-password",
        headers=acting,
        json={"current_password": TEMP_PW, "new_password": NEW_PW},
    )
    assert changed.status_code == 200
    assert _cookie_status(ctx, old_cookie) == 401
    assert ctx.client.get("/api/auth/me", headers=old_bearer).status_code == 401
    assert ctx.client.get("/api/auth/me", headers=acting).status_code == 401
    fresh = {"Authorization": f"Bearer {changed.json()['access_token']}"}
    assert ctx.client.get("/api/auth/me", headers=fresh).status_code == 200


def test_token_without_version_claim_or_wrong_version_is_rejected(ctx):
    import jwt as pyjwt

    from app import database
    from app.models import User

    settings = ctx.client.app.state.settings
    db = database.SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "admin").one()
        base = {"sub": admin.id, "username": "admin", "role": "admin", "exp": 4102444800}
        missing = pyjwt.encode(base, settings.secret_key, algorithm="HS256")
        wrong = pyjwt.encode({**base, "tv": admin.token_version + 5}, settings.secret_key, algorithm="HS256")
    finally:
        db.close()
    for token in (missing, wrong):
        resp = ctx.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401


# ---- 5. add-user form errors ----------------------------------------------------


def test_web_add_user_short_password_shows_error_and_creates_nothing(ctx):
    csrf = _owner_web(ctx)
    resp = ctx.client.post(
        "/users",
        data={"username": "qa_user23", "password": "123456789", "role": "user", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert 400 <= resp.status_code < 500
    assert "at least 10 characters" in resp.text
    names = [u["username"] for u in ctx.client.get("/api/users", headers=ctx.auth).json()]
    assert "qa_user23" not in names


def test_web_add_user_bad_and_duplicate_username_show_errors(ctx):
    csrf = _owner_web(ctx)
    bad = ctx.client.post(
        "/users", data={"username": "a b", "password": TEMP_PW, "role": "user", "csrf_token": csrf}
    )
    assert bad.status_code == 400 and 'role="alert"' in bad.text and "Username" in bad.text
    _create(ctx, "qa_user24")
    dup = ctx.client.post(
        "/users", data={"username": "qa_user24", "password": TEMP_PW, "role": "user", "csrf_token": csrf}
    )
    assert dup.status_code == 409 and "already exists" in dup.text


def test_users_page_shows_password_hint(ctx):
    _owner_web(ctx)
    page = ctx.client.get("/users").text
    assert "At least 10 characters" in page and 'minlength="10"' in page
