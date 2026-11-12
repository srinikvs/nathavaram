"""CSRF protection for cookie-authenticated unsafe requests (synthetic data only)."""

from __future__ import annotations

from tests.conftest import csrf_from_page, make_user, voter_payload

ADMIN = {"username": "admin", "password": "admin-pass-123"}


def _cookie_login(ctx, credentials: dict | None = None) -> str:
    """Sign in with a cookie only and return the CSRF token the server issued."""
    ctx.client.cookies.clear()
    response = ctx.client.post("/api/auth/login", json=credentials or ADMIN)
    assert response.status_code == 200, response.text
    token = response.json()["csrf_token"]
    assert token
    assert ctx.client.cookies.get("access_token")
    return token


def _active_total(ctx) -> int:
    return ctx.client.post("/api/voters/search", headers=ctx.auth, json={}).json()["total"]


def test_cookie_only_post_without_token_is_rejected(ctx):
    _cookie_login(ctx)

    response = ctx.client.post("/api/voters", json=voter_payload())

    assert response.status_code == 403
    assert "CSRF" in response.json()["detail"]
    assert _active_total(ctx) == 0


def test_cookie_post_with_valid_token_succeeds(ctx):
    csrf = _cookie_login(ctx)

    response = ctx.client.post("/api/voters", json=voter_payload(), headers={"X-CSRF-Token": csrf})

    assert response.status_code == 201, response.text
    assert _active_total(ctx) == 1


def test_cookie_post_with_wrong_token_is_rejected(ctx):
    _cookie_login(ctx)

    for bad in ("0" * 64, "not-a-token", ""):
        response = ctx.client.post("/api/voters", json=voter_payload(), headers={"X-CSRF-Token": bad})
        assert response.status_code == 403, bad
    assert _active_total(ctx) == 0


def test_patch_and_delete_with_cookie_need_the_token(ctx):
    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload()).json()
    csrf = _cookie_login(ctx)
    url = f"/api/voters/{created['id']}"

    assert ctx.client.patch(url, json={"profession": "Farmer"}).status_code == 403
    assert ctx.client.delete(url).status_code == 403
    assert ctx.client.get(url).json()["profession"] == "Teacher"

    patched = ctx.client.patch(url, json={"profession": "Farmer"}, headers={"X-CSRF-Token": csrf})
    assert patched.status_code == 200
    assert ctx.client.delete(url, headers={"X-CSRF-Token": csrf}).status_code == 200


def test_search_by_post_with_cookie_needs_the_token(ctx):
    csrf = _cookie_login(ctx)

    assert ctx.client.post("/api/voters/search", json={"q": "x"}).status_code == 403
    allowed = ctx.client.post("/api/voters/search", json={"q": "x"}, headers={"X-CSRF-Token": csrf})
    assert allowed.status_code == 200


def test_bearer_requests_do_not_need_the_token(ctx):
    # The jar still holds a session cookie here, as a browser would.
    _cookie_login(ctx)
    with_cookie_present = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload())
    assert with_cookie_present.status_code == 201, with_cookie_present.text

    ctx.client.cookies.clear()
    bearer_only = ctx.client.post(
        "/api/voters", headers=ctx.auth, json=voter_payload(voter_id="ZZZ1000002", mobile="9000000022")
    )
    assert bearer_only.status_code == 201, bearer_only.text
    synced = ctx.client.post(
        "/api/sync/push", headers=ctx.auth, json={"device_id": "lab-phone-1", "changes": []}
    )
    assert synced.status_code == 200


def test_token_is_bound_to_the_session(ctx):
    admin_csrf = _cookie_login(ctx)
    make_user(ctx)
    user_csrf = _cookie_login(ctx, {"username": "field.user", "password": "field-pass-123"})
    assert user_csrf != admin_csrf

    crossed = ctx.client.post("/api/voters", json=voter_payload(), headers={"X-CSRF-Token": admin_csrf})
    assert crossed.status_code == 403
    own = ctx.client.post("/api/voters", json=voter_payload(), headers={"X-CSRF-Token": user_csrf})
    assert own.status_code == 201


def test_login_is_exempt_even_with_a_stale_cookie(ctx):
    ctx.client.cookies.set("access_token", "stale-or-garbage")

    response = ctx.client.post("/api/auth/login", json=ADMIN)

    assert response.status_code == 200


def test_html_forms_include_the_token_and_enforce_it(ctx):
    ctx.client.cookies.clear()
    signed = ctx.client.post(
        "/login", data={**ADMIN, "next": "/voters"}, follow_redirects=False
    )
    assert signed.status_code == 303

    form_page = ctx.client.get("/voters/new")
    assert form_page.status_code == 200
    token = csrf_from_page(form_page.text)
    fields = {
        "voter_id": "ZZZ2000010",
        "first_name": "Webperson",
        "gender": "Male",
        "mobile": "9000000034",
    }

    missing = ctx.client.post("/voters/new", data=fields)
    assert missing.status_code == 403
    wrong = ctx.client.post("/voters/new", data={**fields, "csrf_token": "0" * 64})
    assert wrong.status_code == 403
    assert _active_total(ctx) == 0

    saved = ctx.client.post("/voters/new", data={**fields, "csrf_token": token}, follow_redirects=False)
    assert saved.status_code == 303
    assert _active_total(ctx) == 1

    # Every other state-changing form carries the token too.
    assert 'name="csrf_token"' in ctx.client.get("/users").text
    assert 'name="csrf_token"' in ctx.client.get("/import").text
    assert 'name="csrf_token"' in ctx.client.get("/voters").text
    assert ctx.client.post("/logout", follow_redirects=False).status_code == 403
    out = ctx.client.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    assert out.status_code == 303


def test_multipart_import_form_is_checked(ctx):
    ctx.client.cookies.clear()
    ctx.client.post("/login", data=ADMIN, follow_redirects=False)
    token = csrf_from_page(ctx.client.get("/import").text)
    header = "Booth,2024 B.S.No,Ward,Ward S No,VoterId,FirstName,SurName,RelativeName,HouseNo,Gender,Age,Caste,Mobile,C_City,Profession,Party"
    row = "1,10,1,1,ZZZ0000400,Fake,Row,Rel,H-1,Male,40,SampleGroupA,9000000092,Nathavaram,Farmer,"
    files = {"file": ("fake.csv", f"{header}\n{row}\n".encode("utf-8"), "text/csv")}

    assert ctx.client.post("/import", files=files).status_code == 403
    ok = ctx.client.post("/import", data={"csrf_token": token}, files=files)
    assert ok.status_code == 200
    assert "1 imported" in ok.text
