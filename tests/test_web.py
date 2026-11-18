"""The signed-in pages a clerk actually uses."""

from __future__ import annotations

import re
from pathlib import Path

from tests.conftest import csrf_from_page, set_must_change


def _form(**overrides) -> dict:
    data = {
        "booth": "4",
        "booth_s_no": "20",
        "ward": "2",
        "ward_s_no": "8",
        "voter_id": "ZZZ2000001",
        "first_name": "Webperson",
        "last_name": "Sample",
        "relative_name": "Relative Web",
        "house_no": "H-20",
        "age": "28",
        "street": "Web Lane 2",
        "mobile": "9000000033",
        "caste": "SampleGroupA",
        "village": "Webville",
        "panchayath": "Webpanchayath",
        "mandal": "Webmandal",
        "current_city": "Nathavaram",
        "is_local": "Local",
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
    assert "Booth" in reports.text
    assert "2024 Booth" not in reports.text
    assert "Age band" in reports.text
    assert "Panchayath" in reports.text
    assert "Local/Non-Local" in reports.text
    assert "Profession" not in reports.text

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


def test_search_results_use_the_same_card_markup_as_the_list(ctx):
    # Arrange
    imported = ctx.client.post(
        "/api/voters/import.csv",
        headers=ctx.auth,
        files={"file": ("voters_sample.csv", Path("sample/voters_sample.csv").read_bytes(), "text/csv")},
    )
    assert imported.status_code == 200
    token = csrf_from_page(ctx.client.get("/voters").text)

    # Act
    found = ctx.client.post("/voters/search", data={"csrf_token": token, "party": "TDP"})

    # Assert
    assert found.status_code == 200
    assert 'table class="register"' in found.text
    assert 'class="table-wrap"' in found.text
    assert 'data-label="VoterId"' in found.text
    assert "ZZZ0000002" in found.text
    assert "ZZZ0000009" in found.text
    assert "ZZZ0000001" not in found.text


def test_narrow_screen_css_collapses_the_register_table():
    # Arrange
    css = Path("app/static/styles.css").read_text(encoding="utf-8")
    narrow = css.split("@media (max-width: 760px)", 1)[1].split("@media print", 1)[0]

    # Assert
    block = re.search(
        r"\.register,\s*\.register tbody,\s*\.register thead\s*\{([^}]*)\}", narrow
    )
    assert block, "register/tbody must be one block rule in the narrow query"
    body = block.group(1)
    assert re.search(r"display:\s*block", body)
    assert re.search(r"(?<!-)width:\s*100%", body)
    assert re.search(r"max-width:\s*100%", body)
    assert re.search(r"min-width:\s*0", body)
    assert not re.search(r"display:\s*table", narrow)
    assert re.search(r"\.register thead\s*\{\s*display:\s*none;\s*\}", narrow)
    assert re.search(r"\.register tr\s*\{[^}]*display:\s*block", narrow)
    assert "minmax(0, 1fr)" in narrow
    assert "td::before" in narrow
    # Desktop keeps the table layout and its wide minimum.
    assert re.search(r"^\.register\s*\{\s*min-width:\s*980px;\s*\}", css, re.M)
    assert "min-width: 980px" not in narrow
    assert re.search(r"\.table-wrap\s*\{[^}]*max-width:\s*100%", css)
