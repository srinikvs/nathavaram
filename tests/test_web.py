"""The signed-in pages a clerk actually uses."""

from __future__ import annotations

from tests.conftest import csrf_from_page, set_must_change


def _form(**overrides) -> dict:
    data = {
        "booth_2024": "4",
        "bs_no_2024": "20",
        "ward_2026": "2",
        "ward_s_no": "8",
        "voter_id": "ZZZ2000001",
        "first_name": "Webperson",
        "surname": "Sample",
        "relative_name": "Relative Web",
        "house_no": "H-20",
        "gender": "Male",
        "age": "28",
        "caste": "SampleGroupA",
        "mobile": "9000000033",
        "c_city": "Nathavaram",
        "profession": "Clerk",
        "party": "",
    }
    data.update(overrides)
    return data


def test_pages_require_login_and_round_trip_a_voter(ctx):
    ctx.client.cookies.clear()
    anonymous = ctx.client.get("/voters", follow_redirects=False)
    assert anonymous.status_code == 303
    assert anonymous.headers["location"].startswith("/login")
    login_page = ctx.client.get("/login")
    assert login_page.status_code == 200
    assert "Sign in" in login_page.text
    assert "admin-pass-123" not in login_page.text

    signed = ctx.client.post(
        "/login",
        data={"username": "admin", "password": "admin-pass-123", "next": "/voters"},
        follow_redirects=False,
    )
    assert signed.status_code == 303
    listing = ctx.client.get("/voters")
    assert listing.status_code == 200
    assert "VoterId" in listing.text
    assert "Add voter" in listing.text

    token = csrf_from_page(listing.text)
    invalid = ctx.client.post("/voters/new", data=_form(voter_id="", first_name="", csrf_token=token))
    assert invalid.status_code == 400
    assert "VoterId" in invalid.text

    saved = ctx.client.post("/voters/new", data=_form(csrf_token=token), follow_redirects=False)
    assert saved.status_code == 303
    edit = ctx.client.get(saved.headers["location"])
    assert edit.status_code == 200
    assert "Jana Sena" in edit.text
    assert "Prefer not to say" in edit.text
    assert "Webperson" in edit.text
    found = ctx.client.post("/voters/search", data={"csrf_token": token, "q": "webperson"})
    assert found.text.count("Webperson") >= 1

    reports = ctx.client.get("/reports")
    assert reports.status_code == 200
    assert "2024 Booth" in reports.text
    assert "Age band" in reports.text
    assert "Profession" in reports.text

    created = ctx.client.post(
        "/api/users",
        headers=ctx.auth,
        json={"username": "field.user", "password": "field-pass-123", "role": "user"},
    )
    assert created.status_code == 201
    set_must_change("field.user", False)
    ctx.client.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    ctx.client.post(
        "/login",
        data={"username": "field.user", "password": "field-pass-123"},
        follow_redirects=False,
    )
    forbidden = ctx.client.get("/users")
    assert forbidden.status_code == 403
