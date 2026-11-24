"""Role-based edit rules and the Panchayath / Mandal / HouseNo / Street / Local-Non-Local columns.

Synthetic data only.
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.constants import CSV_HEADERS, DATA_FIELDS, USER_EDITABLE_FIELDS
from app.validation import canonical_local
from tests.conftest import csrf_from_page, make_user, search, voter_payload

ROOT = Path(__file__).resolve().parents[1]
USER_NAME, USER_PASSWORD = "field.user", "field-pass-123"

# One different value per field a user or manager must not change (defaults in voter_payload differ).
# Admin-only on edit: every field except VoterId and the USER_EDITABLE_FIELDS (14 fields).
LOCKED_CHANGES = {
    "first_name": "Changedname",
    "last_name": "Changed",
    "gender": "Female",
    "caste": "SampleGroupB",
    "mandal": "Othermandal",
    "constituency": "Otherconstituency",
}
# USER_EDITABLE_FIELDS (the 14-field product list), one changed value each.
ALLOWED_CHANGES = {
    "booth": "99",
    "ward": "9",
    "booth_s_no": "999",
    "ward_s_no": "99",
    "relative_name": "Changed Relative",
    "house_no": "99-Z",
    "age": 50,
    "street": "New Street 5",
    "mobile": "9000000077",
    "village": "Newvillage",
    "panchayath": "Newpanchayath",
    "current_city": "Elsewhere",
    "is_local": False,
    "party": "BJP",
}


def _create(ctx, **overrides) -> dict:
    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload(**overrides))
    assert created.status_code == 201, created.text
    return created.json()


def _get(ctx, voter_id: str) -> dict:
    return ctx.client.get(f"/api/voters/{voter_id}", headers=ctx.auth).json()


def _later(seconds: int = 30) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _snapshot(voter: dict, **overrides) -> dict:
    body = {"updated_at": _later(), "deleted": False}
    for field in DATA_FIELDS:
        body[field] = voter[field]
    body.update(overrides)
    return body


def _push(ctx, headers, changes) -> dict:
    response = ctx.client.post(
        "/api/sync/push", headers=headers, json={"device_id": "lab-phone-1", "changes": changes}
    )
    assert response.status_code == 200, response.text
    return response.json()


# --- Edit API ---------------------------------------------------------------


def test_user_can_update_every_user_editable_field(ctx):
    user = make_user(ctx)
    voter = _create(ctx)

    patched = ctx.client.patch(f"/api/voters/{voter['voter_id']}", headers=user, json=ALLOWED_CHANGES)

    assert patched.status_code == 200, patched.text
    for field, value in ALLOWED_CHANGES.items():
        assert patched.json()[field] == value


def test_user_editable_fields_are_exactly_the_fourteen_listed_fields():
    assert USER_EDITABLE_FIELDS == set(ALLOWED_CHANGES)
    assert USER_EDITABLE_FIELDS == {
        "booth", "ward", "booth_s_no", "ward_s_no", "relative_name", "house_no", "age",
        "street", "mobile", "village", "panchayath", "current_city", "is_local", "party",
    }
    assert "voter_id" not in USER_EDITABLE_FIELDS and "gender" not in USER_EDITABLE_FIELDS
    assert "caste" not in USER_EDITABLE_FIELDS


@pytest.mark.parametrize("field", sorted(LOCKED_CHANGES))
def test_user_is_rejected_when_changing_a_locked_field_via_api(ctx, field):
    user = make_user(ctx)
    voter = _create(ctx)

    denied = ctx.client.patch(
        f"/api/voters/{voter['voter_id']}", headers=user, json={field: LOCKED_CHANGES[field]}
    )

    assert denied.status_code == 403, denied.text
    assert "cannot change" in denied.json()["detail"]
    assert _get(ctx, voter["voter_id"]) == voter  # nothing applied, revision untouched


def test_user_patch_mixing_allowed_and_locked_applies_nothing(ctx):
    user = make_user(ctx)
    voter = _create(ctx)

    denied = ctx.client.patch(
        f"/api/voters/{voter['voter_id']}",
        headers=user,
        json={"ward": "9", "caste": "SampleGroupZ"},
    )

    assert denied.status_code == 403
    assert _get(ctx, voter["voter_id"]) == voter


def test_user_may_echo_unchanged_locked_fields(ctx):
    user = make_user(ctx)
    voter = _create(ctx)
    echo = {field: voter[field] for field in ("first_name", "mandal", "gender", "constituency", "village")}

    patched = ctx.client.patch(f"/api/voters/{voter['voter_id']}", headers=user, json={**echo, "party": "BJP"})

    assert patched.status_code == 200, patched.text
    assert patched.json()["party"] == "BJP"
    assert patched.json()["first_name"] == voter["first_name"]


@pytest.mark.parametrize("field", sorted(LOCKED_CHANGES))
def test_admin_can_edit_every_field_except_voter_id(ctx, field):
    voter = _create(ctx)

    patched = ctx.client.patch(
        f"/api/voters/{voter['voter_id']}", headers=ctx.auth, json={field: LOCKED_CHANGES[field]}
    )

    assert patched.status_code == 200, patched.text
    assert patched.json()[field] == LOCKED_CHANGES[field]


def test_a_second_admin_account_can_edit_locked_fields(ctx):
    other_admin = make_user(ctx, "second.admin", "second-pass-123", role="admin")
    voter = _create(ctx)

    patched = ctx.client.patch(f"/api/voters/{voter['voter_id']}", headers=other_admin, json={"ward": "9"})

    assert patched.status_code == 200
    assert patched.json()["ward"] == "9"


@pytest.mark.parametrize("role", ["admin", "user"])
def test_voter_id_cannot_change_via_edit_api_for_any_role(ctx, role):
    headers = ctx.auth if role == "admin" else make_user(ctx)
    voter = _create(ctx)

    changed = ctx.client.patch(
        f"/api/voters/{voter['voter_id']}", headers=headers, json={"voter_id": "ZZZ9999999"}
    )

    assert changed.status_code in (400, 422)
    assert "cannot be changed" in changed.json()["detail"]
    assert _get(ctx, voter["voter_id"])["voter_id"] == voter["voter_id"]


def test_user_create_behaviour_is_unchanged_and_accepts_the_new_fields(ctx):
    user = make_user(ctx)

    created = ctx.client.post("/api/voters", headers=user, json=voter_payload())

    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["village"], body["panchayath"], body["is_local"]) == (
        "Samplevillage",
        "Samplepanchayath",
        True,
    )


# --- Sync -------------------------------------------------------------------


def test_sync_user_can_change_allowed_fields_with_a_full_snapshot(ctx):
    user = make_user(ctx)
    voter = _create(ctx)

    result = _push(ctx, user, [_snapshot(voter, **ALLOWED_CHANGES)])["results"][0]

    assert result["status"] == "applied", result
    for field, value in ALLOWED_CHANGES.items():
        assert _get(ctx, voter["voter_id"])[field] == value


@pytest.mark.parametrize("field", sorted(LOCKED_CHANGES))
def test_sync_user_is_rejected_for_a_locked_field_and_nothing_is_applied(ctx, field):
    user = make_user(ctx)
    voter = _create(ctx)

    result = _push(ctx, user, [_snapshot(voter, **{field: LOCKED_CHANGES[field]})])["results"][0]

    assert result["status"] == "forbidden"
    assert "cannot change" in result["message"]
    assert result["server_record"]["revision"] == voter["revision"]
    assert _get(ctx, voter["voter_id"]) == voter


def test_sync_batch_rejects_only_the_forbidden_change(ctx):
    user = make_user(ctx)
    first = _create(ctx)
    second = _create(ctx, voter_id="ZZZ1000002", mobile="9000000022")

    results = _push(
        ctx,
        user,
        [_snapshot(first, first_name="Changedname"), _snapshot(second, party="BJP")],
    )["results"]

    assert [item["status"] for item in results] == ["forbidden", "applied"]
    assert _get(ctx, first["voter_id"])["first_name"] == first["first_name"]
    assert _get(ctx, second["voter_id"])["party"] == "BJP"


def test_sync_unchanged_locked_fields_are_not_a_violation(ctx):
    user = make_user(ctx)
    voter = _create(ctx)

    result = _push(ctx, user, [_snapshot(voter, party="INC")])["results"][0]

    assert result["status"] == "applied"
    assert result["server_record"]["first_name"] == voter["first_name"]


def test_sync_admin_can_change_locked_fields(ctx):
    voter = _create(ctx)

    result = _push(ctx, ctx.auth, [_snapshot(voter, **LOCKED_CHANGES)])["results"][0]

    assert result["status"] == "applied", result
    stored = _get(ctx, voter["voter_id"])
    for field, value in LOCKED_CHANGES.items():
        assert stored[field] == value


@pytest.mark.parametrize("role", ["admin", "user"])
def test_sync_with_another_voter_id_creates_a_separate_record_for_any_role(ctx, role):
    headers = ctx.auth if role == "admin" else make_user(ctx)
    voter = _create(ctx)

    result = _push(ctx, headers, [_snapshot(voter, voter_id="ZZZ9999999")])["results"][0]

    assert result["status"] == "applied"
    assert result["voter_id"] == "ZZZ9999999"
    assert _get(ctx, voter["voter_id"]) == voter  # the original is untouched


def test_sync_user_soft_delete_is_applied_and_keeps_every_field(ctx):
    user = make_user(ctx)
    voter = _create(ctx)

    result = _push(ctx, user, [{"voter_id": voter["voter_id"], "updated_at": _later(), "deleted": True}])[
        "results"
    ][0]

    assert result["status"] == "applied", result
    stored = result["server_record"]
    assert stored["deleted_at"] is not None
    for field in DATA_FIELDS:
        assert stored[field] == voter[field], field  # the payload is no longer an empty tombstone


def test_sync_user_can_still_create_a_new_voter(ctx):
    user = make_user(ctx)
    body = voter_payload(voter_id="ZZZ1000003", mobile="9000000023")
    body.update(updated_at=_later(), deleted=False)

    result = _push(ctx, user, [body])["results"][0]

    assert result["status"] == "applied", result
    assert result["server_record"]["village"] == "Samplevillage"


# --- Web edit form -----------------------------------------------------------


def _web_login(ctx, username: str, password: str) -> None:
    ctx.client.cookies.clear()
    signed = ctx.client.post(
        "/login",
        data={"username": username, "password": password, "next": "/voters"},
        follow_redirects=False,
    )
    assert signed.status_code == 303, signed.text


def _tag(html: str, tag: str, element_id: str) -> str:
    match = re.search(rf'<{tag} id="{element_id}"[^>]*>', html)
    assert match, f"{tag}#{element_id} missing"
    return match.group(0)


def test_web_edit_form_shows_locked_fields_read_only_for_a_user(ctx):
    voter = _create(ctx)
    make_user(ctx)
    _web_login(ctx, USER_NAME, USER_PASSWORD)

    page = ctx.client.get(f"/voters/{voter['voter_id']}/edit").text

    for field in ("first_name", "last_name", "mandal", "constituency", "caste"):
        assert "readonly" in _tag(page, "input", field), field
    assert "disabled" in _tag(page, "select", "gender")
    for field in ("booth", "booth_s_no", "ward", "ward_s_no", "relative_name", "house_no", "age",
                  "street", "mobile", "village", "panchayath", "current_city"):
        assert "readonly" not in _tag(page, "input", field), field
    assert "disabled" not in _tag(page, "select", "is_local")
    assert "disabled" not in _tag(page, "select", "party")
    assert 'id="voter_id"' not in page  # VoterId stays plain text


def test_web_edit_form_is_fully_editable_for_an_admin_except_voter_id(ctx):
    voter = _create(ctx)
    _web_login(ctx, "admin", "admin-pass-123")

    page = ctx.client.get(f"/voters/{voter['voter_id']}/edit").text

    assert "readonly" not in _tag(page, "input", "first_name")
    assert "disabled" not in _tag(page, "select", "is_local")
    assert 'id="voter_id"' not in page


def _edit_post(ctx, voter: dict, **fields) -> object:
    token = csrf_from_page(ctx.client.get(f"/voters/{voter['voter_id']}/edit").text)
    return ctx.client.post(
        f"/voters/{voter['voter_id']}/edit",
        data={"csrf_token": token, **fields},
        follow_redirects=False,
    )


def test_web_user_post_changes_every_user_editable_field(ctx):
    voter = _create(ctx)
    make_user(ctx)
    _web_login(ctx, USER_NAME, USER_PASSWORD)
    # What a browser sends: read-only text inputs are posted unchanged, disabled selects are not.
    read_only = {field: voter[field] for field in ("first_name", "last_name", "mandal", "constituency", "caste")}
    # The web form posts is_local as its label, not as a boolean.
    web_changes = {**ALLOWED_CHANGES, "is_local": "Non-Local"}

    saved = _edit_post(ctx, voter, **read_only, **web_changes)

    assert saved.status_code == 303, saved.text
    stored = _get(ctx, voter["voter_id"])
    for field, value in ALLOWED_CHANGES.items():
        assert stored[field] == value, field
    assert stored["first_name"] == voter["first_name"] and stored["caste"] == voter["caste"]


def test_web_user_post_with_a_tampered_locked_field_is_refused(ctx):
    voter = _create(ctx)
    make_user(ctx)
    _web_login(ctx, USER_NAME, USER_PASSWORD)

    refused = _edit_post(ctx, voter, first_name="Hacked", caste="Hacked")

    assert refused.status_code == 403
    assert "cannot change" in refused.text
    assert _get(ctx, voter["voter_id"]) == voter


def test_web_admin_post_can_change_locked_fields(ctx):
    voter = _create(ctx)
    _web_login(ctx, "admin", "admin-pass-123")

    saved = _edit_post(
        ctx,
        voter,
        first_name="Renamed",
        village="Newvillage",
        panchayath="Newpanchayath",
        is_local="Non-Local",
    )

    assert saved.status_code == 303, saved.text
    stored = _get(ctx, voter["voter_id"])
    assert (stored["first_name"], stored["village"], stored["panchayath"], stored["is_local"]) == (
        "Renamed", "Newvillage", "Newpanchayath", False,
    )


# --- New columns: create / list / report / search ------------------------------


def test_new_columns_default_to_blank_and_local(ctx):
    bare = voter_payload()
    for key in ("street", "house_no", "village", "panchayath", "mandal", "is_local"):
        del bare[key]

    created = ctx.client.post("/api/voters", headers=ctx.auth, json=bare).json()

    assert created["street"] is None and created["house_no"] is None and created["village"] is None
    assert created["panchayath"] is None and created["mandal"] is None
    assert created["is_local"] is True


def test_is_local_accepts_local_and_non_local_and_rejects_other_text(ctx):
    bad = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload(is_local="maybe"))
    local = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload(voter_id="ZZZ1000007", is_local="Local"))
    non_local = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload(voter_id="ZZZ1000008", is_local="Non-Local"))

    assert bad.status_code == 422
    assert local.json()["is_local"] is True
    assert non_local.json()["is_local"] is False


def test_search_filters_by_village_and_local(ctx):
    _create(ctx, village="Alpha Village", is_local=True)
    _create(ctx, voter_id="ZZZ1000002", mobile="9000000022", village="Beta Village", is_local=False)

    by_village = search(ctx, village="beta").json()
    local_yes = search(ctx, is_local="Local").json()
    local_no = search(ctx, is_local="Non-Local").json()
    local_any = search(ctx, is_local="Any").json()
    both = search(ctx, village="alpha", is_local="Non-Local").json()

    assert [i["voter_id"] for i in by_village["items"]] == ["ZZZ1000002"]
    assert [i["voter_id"] for i in local_yes["items"]] == ["ZZZ1000001"]
    assert [i["voter_id"] for i in local_no["items"]] == ["ZZZ1000002"]
    assert local_any["total"] == 2
    assert both["total"] == 0


def test_free_text_search_matches_street_house_no_village_panchayath_and_mandal(ctx):
    _create(ctx, street="Unique Alley", house_no="77-Q", village="Hamletia", panchayath="Panchtown", mandal="Mandalville")

    for term in ("unique alley", "77-q", "hamletia", "panchtown", "mandalville"):
        assert search(ctx, q=term).json()["total"] == 1, term


def test_list_page_shows_the_requested_columns(ctx):
    _create(ctx)
    _web_login(ctx, "admin", "admin-pass-123")

    page = ctx.client.get("/voters").text

    for header in (
        "VoterId", "FirstName", "LastName", "RelativeName", "HouseNo", "Gender", "Street", "Village",
        "Panchayath", "Mandal", "Local/Non-Local", "Booth", "Ward", "Age", "Caste", "Party", "Mobile", "Constituency",
    ):
        assert f"<th>{header}</th>" in page, header
    assert 'name="village"' in page and 'name="is_local"' in page
    assert 'name="panchayath"' in page and 'name="mandal"' in page
    for gone in ("Profession", "Permanent address", "<th>Address</th>", "<th>SurName</th>"):
        assert gone not in page, gone


def test_form_pages_show_the_requested_fields_and_not_the_removed_ones(ctx):
    voter = _create(ctx)
    _web_login(ctx, "admin", "admin-pass-123")

    for url in ("/voters/new", f"/voters/{voter['voter_id']}/edit"):
        page = ctx.client.get(url).text
        for label in (
            "Booth", "Booth S.No", "Ward", "Ward S.No", "FirstName", "LastName", "RelativeName",
            "HouseNo", "Gender", "Age", "Street", "Mobile", "Caste", "Village", "Panchayath", "Mandal",
            "Current City", "Local/Non-Local", "Party", "Constituency",
        ):
            assert re.search(rf"<label for=\"[a-z_]+\">{re.escape(label)}( <abbr[^>]*>\*</abbr>)?</label>", page), (url, label)
        for gone in ("Profession", "Permanent address", "C_City", "SurName"):
            assert gone not in page, (url, gone)


def test_report_summary_and_page_count_village_panchayath_and_local(ctx):
    _create(ctx, village="Alpha Village", is_local=True, panchayath="Pan A")
    _create(ctx, voter_id="ZZZ1000002", mobile="9000000022", village="Alpha Village", is_local=False, panchayath="Pan A")
    _create(ctx, voter_id="ZZZ1000003", mobile="9000000023", village=None, is_local=False, panchayath=None)

    summary = ctx.client.get("/api/reports/summary", headers=ctx.auth).json()
    exported = ctx.client.get("/api/reports/export.csv", headers=ctx.auth).content.decode("utf-8-sig")
    _web_login(ctx, "admin", "admin-pass-123")
    page = ctx.client.get("/reports").text

    assert {"label": "Alpha Village", "count": 2} in summary["by_village"]
    assert {"label": "(blank)", "count": 1} in summary["by_village"]
    assert {"label": "Pan A", "count": 2} in summary["by_panchayath"]
    assert summary["by_local"] == [{"label": "Local", "count": 1}, {"label": "Non-Local", "count": 2}]
    assert "Village,Alpha Village,2" in exported and "Local/Non-Local,Local,1" in exported
    assert "<h2>Village</h2>" in page and "<h2>Local/Non-Local</h2>" in page
    assert "<h2>Panchayath</h2>" in page and "<h2>Mandal</h2>" in page
    assert "<h2>Booth</h2>" in page and "<h2>Ward</h2>" in page
    assert "2024 Booth" not in page and "2026 Ward" not in page


# --- CSV ----------------------------------------------------------------------


def _import(ctx, text: str):
    return ctx.client.post(
        "/api/voters/import.csv",
        headers=ctx.auth,
        files={"file": ("voters.csv", text.encode("utf-8"), "text/csv")},
    )


def _legacy_header(*, house: str = "HouseNo", booth: str = "2024 Booth", ward: str = "2026 Ward") -> str:
    return (
        f"{booth},2024 B.S.No,{ward},Ward S No,VoterId,FirstName,SurName,RelativeName,{house},"
        "Gender,Age,Caste,Mobile,C_City,Profession,Party"
    )


def _legacy_row(voter_id: str, house: str) -> str:
    return f"7,10,2,1,{voter_id},Csvperson,Row,Rel,{house},Male,40,SampleGroupA,9000000091,Nathavaram,Farmer,"


def test_csv_export_has_the_requested_headers(ctx):
    _create(ctx)
    _create(ctx, voter_id="ZZZ1000002", mobile="9000000022", is_local=False)

    exported = ctx.client.post("/api/voters/export.csv", headers=ctx.auth, json={})

    rows = list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))
    assert list(rows[0]) == list(CSV_HEADERS)
    assert list(CSV_HEADERS) == [
        "Booth", "Booth S.No", "Ward", "Ward S.No", "VoterId", "FirstName", "LastName",
        "RelativeName", "HouseNo", "Gender", "Age", "Street", "Mobile", "Caste", "Village", "Panchayath",
        "Mandal", "Current City", "Local/Non-Local", "Party", "Constituency",
    ]
    assert not {"Profession", "Permanent address", "Address", "SurName"} & set(rows[0])
    assert {row["VoterId"]: row["Local/Non-Local"] for row in rows} == {
        "ZZZ1000001": "Local",
        "ZZZ1000002": "Non-Local",
    }
    assert rows[0]["Village"] == "Samplevillage" and rows[0]["HouseNo"] == "12-A"


def test_csv_export_import_round_trip_keeps_the_new_columns(ctx):
    voter = _create(ctx, village="Roundtrip Village", panchayath="Round Panchayath", mandal="Round Mandal")
    exported = ctx.client.post("/api/voters/export.csv", headers=ctx.auth, json={}).content
    assert ctx.client.delete(f"/api/voters/{voter['voter_id']}", headers=ctx.auth).status_code == 200

    again = _import(ctx, exported.decode("utf-8-sig"))

    assert again.json()["imported"] == 1, again.text
    restored = search(ctx, voter_id="ZZZ1000001").json()["items"][0]
    assert restored["village"] == "Roundtrip Village"
    assert (restored["panchayath"], restored["mandal"]) == ("Round Panchayath", "Round Mandal")
    assert restored["house_no"] == "12-A" and restored["street"] == "Sample Street"
    assert restored["is_local"] is True


def test_csv_import_accepts_local_and_non_local_values(ctx):
    header = ",".join(CSV_HEADERS)
    row = "1,2,3,4,{vid},Newhdr,Row,Rel,H1,,40,Main St,9000000091,G,Samplevillage,Pan,Man,N,{local},"

    imported = _import(
        ctx,
        "\n".join([header, row.format(vid="ZZZ0000601", local="Local"), row.format(vid="ZZZ0000602", local="non-local"),
                   row.format(vid="ZZZ0000603", local=""), row.format(vid="ZZZ0000609", local="Yes")]),
    )

    assert imported.json()["imported"] == 4, imported.text
    flags = {i["voter_id"]: i["is_local"] for i in search(ctx).json()["items"]}
    assert flags == {"ZZZ0000601": True, "ZZZ0000602": False, "ZZZ0000603": True, "ZZZ0000609": True}


def test_csv_import_reports_a_bad_local_value_for_that_row(ctx):
    header = ",".join(CSV_HEADERS)
    row = "1,2,3,4,ZZZ0000604,Badlocal,Row,Rel,,,40,,9000000091,G,V,P,M,N,perhaps,"

    imported = _import(ctx, "\n".join([header, row]))

    report = imported.json()
    assert report["imported"] == 0
    assert report["errors"][0]["field"] == "Local/Non-Local"


@pytest.mark.parametrize(
    ("house_header", "booth", "ward"),
    [("HouseNo", "2024 Booth", "2026 Ward"), ("house_no", "booth_2024", "ward_2026"), ("House No", "Booth", "Ward")],
)
def test_csv_import_accepts_old_headers_and_keeps_house_no_as_its_own_column(ctx, house_header, booth, ward):
    text = "\n".join([_legacy_header(house=house_header, booth=booth, ward=ward), _legacy_row("ZZZ0000605", "12")])

    imported = _import(ctx, text)

    assert imported.status_code == 200, imported.text
    assert imported.json()["imported"] == 1
    stored = search(ctx, voter_id="ZZZ0000605").json()["items"][0]
    assert stored["house_no"] == "12" and stored["street"] is None
    assert (stored["booth"], stored["ward"], stored["booth_s_no"]) == ("7", "2", "10")
    assert (stored["last_name"], stored["current_city"]) == ("Row", "Nathavaram")
    assert stored["village"] is None and stored["is_local"] is True


def test_csv_import_maps_the_old_address_header_to_street(ctx):
    header = _legacy_header() + ",Address"
    rows = [
        _legacy_row("ZZZ0000606", "12") + ",Main St",
        _legacy_row("ZZZ0000607", "") + ",Side St",
        _legacy_row("ZZZ0000608", "9") + ",",
    ]

    imported = _import(ctx, "\n".join([header, *rows]))

    assert imported.json()["imported"] == 3, imported.text
    stored = {i["voter_id"]: (i["house_no"], i["street"]) for i in search(ctx).json()["items"]}
    assert stored == {
        "ZZZ0000606": ("12", "Main St"),
        "ZZZ0000607": (None, "Side St"),
        "ZZZ0000608": ("9", None),
    }


def test_csv_import_still_requires_the_core_columns(ctx):
    header = _legacy_header().replace("VoterId,", "", 1)

    rejected = _import(ctx, "\n".join([header, "x"]))

    assert rejected.status_code == 400
    assert "missing VoterId" in rejected.json()["detail"]


def test_duplicate_voter_id_on_add_returns_a_clear_error(ctx):
    user = make_user(ctx)
    _create(ctx)

    for headers in (ctx.auth, user):
        duplicate = ctx.client.post("/api/voters", headers=headers, json=voter_payload(first_name="Other"))
        assert duplicate.status_code == 409
        assert "ZZZ1000001 already exists" in duplicate.json()["detail"]
    assert search(ctx).json()["total"] == 1


# --- Unit: helpers ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Yes", True), ("yes", True), ("Y", True), ("Local", True), ("local", True), (True, True),
        ("No", False), ("Non-Local", False), ("non local", False), ("", True), (None, True), (False, False),
    ],
)
def test_canonical_local(raw, expected):
    assert canonical_local(raw) is expected


def test_canonical_local_rejects_other_text():
    with pytest.raises(ValueError):
        canonical_local("sometimes")


# --- Migrations ---------------------------------------------------------------------


def _alembic(db_file: Path) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", "sqlite:///" + db_file.as_posix())
    return cfg


def _old_row(conn, number: int, house_no: str | None) -> None:
    conn.execute(
        "INSERT INTO voters (id, booth, ward, voter_id, first_name, gender, house_no,"
        " created_at, updated_at, revision) VALUES (?, '1', '2', ?, 'Fakeperson', 'Male', ?,"
        " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', ?)",
        (f"00000000-0000-0000-0000-{number:012d}", f"ZZZ800000{number}", house_no, number),
    )


def test_migrations_fold_house_no_into_address_and_keep_every_row(tmp_path):
    db_file = tmp_path / "old.db"
    cfg = _alembic(db_file)
    command.upgrade(cfg, "0004_rename_booth_ward")
    with sqlite3.connect(db_file) as conn:
        _old_row(conn, 1, "12")  # address empty -> "H.No 12"
        _old_row(conn, 2, "12")  # address present -> "H.No 12, Main St"
        _old_row(conn, 3, "")  # empty house_no -> address unchanged
        _old_row(conn, 4, None)
        _old_row(conn, 5, "7")  # no address at all, other fields kept
    command.upgrade(cfg, "0005_add_address_village_local")
    with sqlite3.connect(db_file) as conn:
        conn.execute("UPDATE voters SET address = 'Main St' WHERE voter_id = 'ZZZ8000002'")
        conn.execute("UPDATE voters SET address = 'Kept St' WHERE voter_id = 'ZZZ8000003'")

    command.upgrade(cfg, "0006_fold_house_no_into_address")

    with sqlite3.connect(db_file) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(voters)")}
        rows = {
            voter_id: (address, village, permanent, is_local, booth)
            for voter_id, address, village, permanent, is_local, booth in conn.execute(
                "SELECT voter_id, address, village, permanent_address, is_local, booth FROM voters"
            )
        }
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(voters)")}
    assert "house_no" not in columns
    assert {"address", "village", "permanent_address", "is_local"} <= columns
    assert len(rows) == 5
    assert rows["ZZZ8000001"][0] == "H.No 12"
    assert rows["ZZZ8000002"][0] == "H.No 12, Main St"
    assert rows["ZZZ8000003"][0] == "Kept St"
    assert rows["ZZZ8000004"][0] is None
    assert rows["ZZZ8000005"][0] == "H.No 7"
    assert all(row[1] is None and row[2] is None and row[3] == 0 and row[4] == "1" for row in rows.values())
    assert "uq_voters_voter_id_active" in indexes


def test_migrations_downgrade_keeps_every_row(tmp_path):
    db_file = tmp_path / "down.db"
    cfg = _alembic(db_file)
    command.upgrade(cfg, "0004_rename_booth_ward")
    with sqlite3.connect(db_file) as conn:
        _old_row(conn, 1, "12")
    command.upgrade(cfg, "head")

    command.downgrade(cfg, "0004_rename_booth_ward")

    with sqlite3.connect(db_file) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(voters)")}
        assert conn.execute("SELECT COUNT(*) FROM voters").fetchone() == (1,)
    assert "house_no" in columns
    assert not {"address", "village", "permanent_address", "is_local"} & columns
