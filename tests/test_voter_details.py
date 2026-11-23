"""VoterId links to the details page, the Edit hand-off, and 404s. Synthetic fixtures only."""

from __future__ import annotations

import re
from pathlib import Path

from tests.conftest import csrf_from_page, make_user, voter_payload

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
USER_NAME = "field.user"
USER_PASSWORD = "field-pass-123"
VOTER_ID = "ZZZ50000001"
DETAIL_LABELS = (
    "Booth", "Booth S.No", "Ward", "Ward S.No", "VoterId", "FirstName", "LastName",
    "RelativeName", "HouseNo", "Gender", "Age", "Street", "Mobile", "Caste", "Village",
    "Panchayath", "Mandal", "Current City", "Local/Non-Local", "Party", "Constituency",
)


def _create(ctx, voter_id: str = VOTER_ID, **overrides) -> dict:
    response = ctx.client.post(
        "/api/voters",
        headers=ctx.auth,
        json=voter_payload(voter_id=voter_id, mobile="9000000051", **overrides),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _get(ctx, voter_id: str = VOTER_ID) -> dict:
    response = ctx.client.get(f"/api/voters/{voter_id}", headers=ctx.auth)
    assert response.status_code == 200, response.text
    return response.json()


def _web_login(ctx, username: str, password: str) -> None:
    ctx.client.cookies.clear()
    signed = ctx.client.post(
        "/login",
        data={"username": username, "password": password, "next": "/voters"},
        follow_redirects=False,
    )
    assert signed.status_code == 303, signed.text


def _edit_post(ctx, voter_id: str, **fields):
    token = csrf_from_page(ctx.client.get(f"/voters/{voter_id}/edit").text)
    return ctx.client.post(
        f"/voters/{voter_id}/edit",
        data={"csrf_token": token, **fields},
        follow_redirects=False,
    )


# --- list: VoterId is a link in the shared table and card layout ----------------------


def test_admin_list_links_voter_id_to_details_in_the_voter_id_cell(ctx):
    _create(ctx)
    _web_login(ctx, "admin", "admin-pass-123")

    html = ctx.client.get("/voters").text

    link = (
        f'<td data-label="VoterId" translate="no"><a href="/voters/{VOTER_ID}" '
        f'data-voter-link>{VOTER_ID}</a></td>'
    )
    assert link in html
    # The checkbox cell holds only the checkbox, never the link.
    select_cell = re.search(r'<td data-label="Select" class="select-cell">.*?</td>', html, re.S)
    assert select_cell and "<a " not in select_cell.group(0)


def test_regular_user_list_also_links_voter_id(ctx):
    _create(ctx)
    make_user(ctx, USER_NAME, USER_PASSWORD)
    _web_login(ctx, USER_NAME, USER_PASSWORD)

    html = ctx.client.get("/voters").text

    assert f'<a href="/voters/{VOTER_ID}" data-voter-link>{VOTER_ID}</a>' in html
    assert "data-row-select" not in html


def test_link_click_is_stopped_before_the_selection_handlers(ctx):
    script = (STATIC / "app.js").read_text(encoding="utf-8")

    assert 'querySelectorAll("[data-voter-link]")' in script
    assert "event.stopPropagation()" in script
    # Row and select-all handlers are unchanged and still bound to their own controls.
    assert 'querySelectorAll("[data-row-select]")' in script
    assert "bar.hidden = picked === 0" in script


# --- details page -----------------------------------------------------------------------


def test_details_page_shows_every_voter_field(ctx):
    _create(ctx)
    _web_login(ctx, "admin", "admin-pass-123")

    response = ctx.client.get(f"/voters/{VOTER_ID}")

    assert response.status_code == 200
    for label in DETAIL_LABELS:
        assert f"<dt>{label}</dt>" in response.text, label
    for value in ("Testperson", "Alpha", "9000000051", "Samplevillage", "Local"):
        assert value in response.text, value


def test_details_page_is_display_only_with_no_edit_form(ctx):
    _create(ctx)
    _web_login(ctx, "admin", "admin-pass-123")

    html = ctx.client.get(f"/voters/{VOTER_ID}").text

    assert 'name="first_name"' not in html
    assert 'name="is_local"' not in html
    assert 'action="/voters/' not in html


def test_details_edit_button_opens_the_existing_edit_form(ctx):
    _create(ctx)
    _web_login(ctx, "admin", "admin-pass-123")

    html = ctx.client.get(f"/voters/{VOTER_ID}").text
    edit = ctx.client.get(f"/voters/{VOTER_ID}/edit")

    assert f'href="/voters/{VOTER_ID}/edit"' in html
    assert edit.status_code == 200
    assert 'id="is_local"' in edit.text


def test_regular_user_can_open_details_and_edit_stays_locked(ctx):
    _create(ctx)
    make_user(ctx, USER_NAME, USER_PASSWORD)
    _web_login(ctx, USER_NAME, USER_PASSWORD)

    details = ctx.client.get(f"/voters/{VOTER_ID}")
    edit = ctx.client.get(f"/voters/{VOTER_ID}/edit")

    assert details.status_code == 200
    assert f'href="/voters/{VOTER_ID}/edit"' in details.text
    assert edit.status_code == 200
    assert re.search(r'<select id="is_local"[^>]*disabled', edit.text)


def test_regular_user_changing_local_from_edit_is_refused_with_403(ctx):
    voter = _create(ctx)
    make_user(ctx, USER_NAME, USER_PASSWORD)
    _web_login(ctx, USER_NAME, USER_PASSWORD)

    refused = _edit_post(ctx, VOTER_ID, is_local="Non-Local", first_name=voter["first_name"])

    assert refused.status_code == 403, refused.text
    assert _get(ctx)["is_local"] is True


def test_admin_can_change_local_from_the_edit_form_opened_from_details(ctx):
    voter = _create(ctx)
    _web_login(ctx, "admin", "admin-pass-123")
    assert ctx.client.get(f"/voters/{VOTER_ID}").status_code == 200

    saved = _edit_post(ctx, VOTER_ID, is_local="Non-Local", first_name=voter["first_name"])

    assert saved.status_code == 303, saved.text
    assert _get(ctx)["is_local"] is False


# --- 404s -------------------------------------------------------------------------------


def test_missing_voter_details_and_edit_are_clean_404s(ctx):
    _web_login(ctx, "admin", "admin-pass-123")

    for path in ("/voters/ZZZ99999999", "/voters/ZZZ99999999/edit"):
        response = ctx.client.get(path)
        assert response.status_code == 404, path
        assert "Not found" in response.text
        assert "not on the active list" in response.text
        assert "Traceback" not in response.text


def test_soft_deleted_voter_details_and_edit_are_clean_404s(ctx):
    _create(ctx)
    _web_login(ctx, "admin", "admin-pass-123")
    removed = ctx.client.delete(f"/api/voters/{VOTER_ID}", headers=ctx.auth)
    assert removed.status_code == 200, removed.text

    for path in (f"/voters/{VOTER_ID}", f"/voters/{VOTER_ID}/edit"):
        response = ctx.client.get(path)
        assert response.status_code == 404, path
        assert "not on the active list" in response.text
        assert "Traceback" not in response.text


def test_details_route_does_not_swallow_new_voter_or_search(ctx):
    _create(ctx)
    _web_login(ctx, "admin", "admin-pass-123")

    assert ctx.client.get("/voters/new").status_code == 200
    search = ctx.client.post(
        "/voters/search",
        data={"csrf_token": csrf_from_page(ctx.client.get("/voters").text), "q": "Testperson"},
    )
    assert search.status_code == 200
    assert VOTER_ID in search.text


# --- personal filters never go into a URL ----------------------------------------------


def test_search_filters_never_appear_in_list_links_or_details_urls(ctx):
    _create(ctx)
    _web_login(ctx, "admin", "admin-pass-123")
    token = csrf_from_page(ctx.client.get("/voters").text)

    result = ctx.client.post(
        "/voters/search",
        data={"csrf_token": token, "mobile": "9000000051", "voter_id": VOTER_ID, "q": "Testperson"},
    )

    assert result.status_code == 200
    hrefs = re.findall(r'href="([^"]*)"', result.text)
    assert f"/voters/{VOTER_ID}" in hrefs
    assert all("?" not in href and "9000000051" not in href for href in hrefs)
    assert "?" not in str(ctx.client.get(f"/voters/{VOTER_ID}").url)


def test_get_voters_ignores_filters_in_the_url(ctx):
    _create(ctx)
    _web_login(ctx, "admin", "admin-pass-123")

    html = ctx.client.get("/voters?q=Testperson&mobile=9000000051").text

    assert 'name="q" value=""' in html  # a fresh visit is unfiltered; filters are POST-only


def test_list_and_details_carry_the_back_context_hooks(ctx):
    _create(ctx)
    _web_login(ctx, "admin", "admin-pass-123")

    listing = ctx.client.get("/voters").text
    details = ctx.client.get(f"/voters/{VOTER_ID}").text
    script = ctx.client.get("/static/voter-list-context.js")

    assert "data-voter-filters" in listing and 'data-page="1"' in listing
    assert 'href="/voters" data-clear-filters>Clear' in listing
    assert '<script src="/static/voter-list-context.js"></script>' in listing
    assert "data-voter-list-return" in details
    assert '<script src="/static/voter-list-context.js"></script>' in details
    assert script.status_code == 200


def test_context_script_uses_session_storage_and_never_the_url(ctx):
    script = (STATIC / "voter-list-context.js").read_text(encoding="utf-8")

    assert "sessionStorage" in script
    assert "history.replaceState" in script
    assert "form.submit()" in script
    assert "Mark the restore as done before posting" in script  # one-shot, no Back loop
    assert "location.search" not in script
    assert "URLSearchParams" not in script
    assert "history.pushState" not in script
