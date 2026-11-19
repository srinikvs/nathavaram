"""Admin master data lists. Synthetic fixtures only."""

from __future__ import annotations

import pytest

from app.services.master_data import ENTITIES
from tests.conftest import make_user, voter_payload

EXPECTED_TABS = [
    "constituency",
    "mandalam",
    "panchayathi",
    "booth",
    "ward",
    "street",
    "caste",
    "profession",
    "city",
]


def _add(ctx, n: int, **overrides) -> None:
    response = ctx.client.post(
        "/api/voters",
        headers=ctx.auth,
        json=voter_payload(voter_id=f"ZZZ20000{n:02d}", mobile=f"90000000{n:02d}", **overrides),
    )
    assert response.status_code == 201, response.text


def _seed(ctx) -> None:
    _add(ctx, 1)
    _add(ctx, 2, mandal="Othermandal", panchayath="Otherpanchayath", booth="7", is_local=False)
    _add(ctx, 3, current_city="Samplecity")
    _add(ctx, 4, constituency="Otherconstituency", caste="", street="  ")


def _rows(ctx, entity: str) -> list[dict]:
    response = ctx.client.get(f"/api/master-data/{entity}", headers=ctx.auth)
    assert response.status_code == 200, response.text
    return response.json()["rows"]


def test_tab_list_has_exactly_the_requested_tabs_in_order(ctx):
    response = ctx.client.get("/api/master-data", headers=ctx.auth)

    assert response.status_code == 200
    assert [t["entity"] for t in response.json()["tabs"]] == EXPECTED_TABS
    assert list(ENTITIES) == EXPECTED_TABS


def test_constituency_rows_count_mandals_panchayaths_and_voters(ctx):
    _seed(ctx)

    rows = {r["constituency"]: r for r in _rows(ctx, "constituency")}

    assert rows["Sampleconstituency"]["voters"] == 3
    assert rows["Sampleconstituency"]["mandals"] == 2
    assert rows["Sampleconstituency"]["panchayaths"] == 2
    assert rows["Otherconstituency"]["voters"] == 1
    assert rows["Otherconstituency"]["mandals"] == 1


def test_mandalam_and_panchayathi_rows_carry_their_parent(ctx):
    _seed(ctx)

    mandals = {(r["mandal"], r["constituency"]): r for r in _rows(ctx, "mandalam")}
    panchayaths = {(r["panchayath"], r["mandal"]): r for r in _rows(ctx, "panchayathi")}

    assert mandals[("Samplemandal", "Sampleconstituency")]["voters"] == 2
    assert mandals[("Othermandal", "Sampleconstituency")]["panchayaths"] == 1
    assert panchayaths[("Otherpanchayath", "Othermandal")]["booths"] == 1


def test_booth_ward_and_city_counts(ctx):
    _seed(ctx)

    booths = {r["booth"]: r for r in _rows(ctx, "booth")}
    wards = {r["ward"]: r for r in _rows(ctx, "ward")}
    cities = {r["city"]: r for r in _rows(ctx, "city")}

    assert booths["12A"]["voters"] == 3
    assert booths["7"]["voters"] == 1
    assert wards["3"]["booths"] == 2
    assert cities["Samplecity"]["voters"] == 1
    assert cities["Nathavaram"]["voters"] == 3
    assert cities["Nathavaram"]["local"] == 2


def test_blank_values_group_as_blank_and_do_not_count_as_children(ctx):
    _seed(ctx)

    castes = {r["caste"]: r["voters"] for r in _rows(ctx, "caste")}
    streets = {r["street"]: r for r in _rows(ctx, "street")}

    assert castes["(blank)"] == 1
    assert castes["SampleGroupA"] == 3
    assert streets["(blank)"]["voters"] == 1
    assert streets["(blank)"]["booths"] == 1


def test_removed_voters_are_not_counted(ctx):
    _seed(ctx)
    removed = ctx.client.delete("/api/voters/ZZZ2000004", headers=ctx.auth)
    assert removed.status_code in (200, 204), removed.text

    rows = {r["constituency"]: r for r in _rows(ctx, "constituency")}

    assert "Otherconstituency" not in rows
    assert rows["Sampleconstituency"]["voters"] == 3


def test_profession_tab_is_reported_unavailable_not_invented(ctx):
    _seed(ctx)

    body = ctx.client.get("/api/master-data/profession", headers=ctx.auth).json()

    assert body["available"] is False
    assert body["rows"] == []
    assert "not recorded" in body["note"]


def test_empty_database_returns_no_rows(ctx):
    for entity in EXPECTED_TABS:
        assert _rows(ctx, entity) == []


def test_unknown_entity_is_404(ctx):
    response = ctx.client.get("/api/master-data/survey", headers=ctx.auth)

    assert response.status_code == 404


@pytest.mark.parametrize("path", ["/api/master-data", "/api/master-data/caste"])
def test_api_requires_authentication(ctx, path):
    ctx.client.cookies.clear()

    assert ctx.client.get(path).status_code == 401


@pytest.mark.parametrize("path", ["/api/master-data", "/api/master-data/caste"])
def test_api_rejects_non_admin_users(ctx, path):
    headers = make_user(ctx, role="user")

    assert ctx.client.get(path, headers=headers).status_code == 403


def test_api_allows_a_non_owner_admin(ctx):
    headers = make_user(ctx, username="second.admin", role="admin")

    assert ctx.client.get("/api/master-data/caste", headers=headers).status_code == 200


def _web_login(ctx, username: str, password: str) -> None:
    ctx.client.cookies.clear()
    response = ctx.client.post(
        "/login", data={"username": username, "password": password}, follow_redirects=False
    )
    assert response.status_code == 303


def test_page_shows_tabs_sidebar_and_no_survey_or_excel_tabs(ctx):
    _seed(ctx)
    _web_login(ctx, "admin", "admin-pass-123")

    page = ctx.client.get("/master-data?tab=mandalam")

    assert page.status_code == 200
    assert "Master Data" in page.text
    assert "Samplemandal" in page.text
    for label in (
        "Constituency", "Mandalam", "Panchayathi", "Booth", "Ward", "Street",
        "Caste", "Profession", "City",
    ):
        assert f">{label}</a>" in page.text
    assert 'class="sidebar"' in page.text
    assert "Survey" not in page.text
    assert "Excel" not in page.text


def test_page_falls_back_to_the_first_tab_for_an_unknown_tab(ctx):
    _web_login(ctx, "admin", "admin-pass-123")

    page = ctx.client.get("/master-data?tab=nope")

    assert page.status_code == 200
    assert "<h2>Constituency</h2>" in page.text


def test_page_requires_login(ctx):
    ctx.client.cookies.clear()

    response = ctx.client.get("/master-data", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_page_is_forbidden_for_non_admin_and_has_no_sidebar_link(ctx):
    make_user(ctx, username="plain.user", password="plain-pass-123", role="user")
    _web_login(ctx, "plain.user", "plain-pass-123")

    page = ctx.client.get("/master-data")
    voters = ctx.client.get("/voters")

    assert page.status_code == 403
    assert "Samplemandal" not in page.text
    assert 'href="/master-data"' not in voters.text
