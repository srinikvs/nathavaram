"""Role-based edit rules, the House No removal, and the Village / Permanent address / Local columns.

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
from app.validation import canonical_local, fold_house_no
from tests.conftest import csrf_from_page, make_user, search, voter_payload

ROOT = Path(__file__).resolve().parents[1]
USER_NAME, USER_PASSWORD = "field.user", "field-pass-123"

# One different value per field a regular user must not change (defaults in voter_payload differ).
LOCKED_CHANGES = {
    "first_name": "Changedname",
    "surname": "Changed",
    "relative_name": "Changed Relative",
    "booth": "99",
    "bs_no_2024": "999",
    "ward": "9",
    "ward_s_no": "99",
    "village": "Othervillage",
    "permanent_address": "Other Lane 9",
    "is_local": False,
    "gender": "Male",
    "age": 50,
    "c_city": "Elsewhere",
    "profession": "Driver",
}
ALLOWED_CHANGES = {
    "party": "BJP",
    "caste": "SampleGroupZ",
    "mobile": "9000000077",
    "address": "New Address 5",
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
    body = {"id": voter["id"], "updated_at": _later(), "deleted": False}
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


def test_user_can_update_party_caste_mobile_and_address(ctx):
    user = make_user(ctx)
    voter = _create(ctx)

    patched = ctx.client.patch(f"/api/voters/{voter['id']}", headers=user, json=ALLOWED_CHANGES)

    assert patched.status_code == 200, patched.text
    for field, value in ALLOWED_CHANGES.items():
        assert patched.json()[field] == value


def test_user_editable_fields_are_exactly_party_caste_mobile_address():
    assert USER_EDITABLE_FIELDS == {"party", "caste", "mobile", "address"}


@pytest.mark.parametrize("field", sorted(LOCKED_CHANGES))
def test_user_is_rejected_when_changing_a_locked_field_via_api(ctx, field):
    user = make_user(ctx)
    voter = _create(ctx)

    denied = ctx.client.patch(
        f"/api/voters/{voter['id']}", headers=user, json={field: LOCKED_CHANGES[field]}
    )

    assert denied.status_code == 403, denied.text
    assert "cannot change" in denied.json()["detail"]
    assert _get(ctx, voter["id"]) == voter  # nothing applied, revision untouched


def test_user_patch_mixing_allowed_and_locked_applies_nothing(ctx):
    user = make_user(ctx)
    voter = _create(ctx)

    denied = ctx.client.patch(
        f"/api/voters/{voter['id']}",
        headers=user,
        json={"caste": "SampleGroupZ", "ward": "9"},
    )

    assert denied.status_code == 403
    assert _get(ctx, voter["id"]) == voter


def test_user_may_echo_unchanged_locked_fields(ctx):
    user = make_user(ctx)
    voter = _create(ctx)
    echo = {field: voter[field] for field in ("first_name", "ward", "village", "is_local", "age")}

    patched = ctx.client.patch(
        f"/api/voters/{voter['id']}", headers=user, json={**echo, "caste": "SampleGroupZ"}
    )

    assert patched.status_code == 200, patched.text
    assert patched.json()["caste"] == "SampleGroupZ"
    assert patched.json()["first_name"] == voter["first_name"]


@pytest.mark.parametrize("field", sorted(LOCKED_CHANGES))
def test_admin_can_edit_every_field_except_voter_id(ctx, field):
    voter = _create(ctx)

    patched = ctx.client.patch(
        f"/api/voters/{voter['id']}", headers=ctx.auth, json={field: LOCKED_CHANGES[field]}
    )

    assert patched.status_code == 200, patched.text
    assert patched.json()[field] == LOCKED_CHANGES[field]


def test_a_second_admin_account_can_edit_locked_fields(ctx):
    other_admin = make_user(ctx, "second.admin", "second-pass-123", role="admin")
    voter = _create(ctx)

    patched = ctx.client.patch(f"/api/voters/{voter['id']}", headers=other_admin, json={"ward": "9"})

    assert patched.status_code == 200
    assert patched.json()["ward"] == "9"


@pytest.mark.parametrize("role", ["admin", "user"])
def test_voter_id_cannot_change_via_edit_api_for_any_role(ctx, role):
    headers = ctx.auth if role == "admin" else make_user(ctx)
    voter = _create(ctx)

    changed = ctx.client.patch(
        f"/api/voters/{voter['id']}", headers=headers, json={"voter_id": "ZZZ9999999"}
    )

    assert changed.status_code in (400, 422)
    assert "cannot be changed" in changed.json()["detail"]
    assert _get(ctx, voter["id"])["voter_id"] == voter["voter_id"]


def test_user_create_behaviour_is_unchanged_and_accepts_the_new_fields(ctx):
    user = make_user(ctx)

    created = ctx.client.post("/api/voters", headers=user, json=voter_payload())

    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["village"], body["permanent_address"], body["is_local"]) == (
        "Samplevillage",
        "Sample Lane 5",
        True,
    )


# --- Sync -------------------------------------------------------------------


def test_sync_user_can_change_allowed_fields_with_a_full_snapshot(ctx):
    user = make_user(ctx)
    voter = _create(ctx)

    result = _push(ctx, user, [_snapshot(voter, **ALLOWED_CHANGES)])["results"][0]

    assert result["status"] == "applied", result
    for field, value in ALLOWED_CHANGES.items():
        assert _get(ctx, voter["id"])[field] == value


@pytest.mark.parametrize("field", sorted(LOCKED_CHANGES))
def test_sync_user_is_rejected_for_a_locked_field_and_nothing_is_applied(ctx, field):
    user = make_user(ctx)
    voter = _create(ctx)

    result = _push(ctx, user, [_snapshot(voter, **{field: LOCKED_CHANGES[field]})])["results"][0]

    assert result["status"] == "forbidden"
    assert "cannot change" in result["message"]
    assert result["server_record"]["revision"] == voter["revision"]
    assert _get(ctx, voter["id"]) == voter


def test_sync_batch_rejects_only_the_forbidden_change(ctx):
    user = make_user(ctx)
    first = _create(ctx)
    second = _create(ctx, voter_id="ZZZ1000002", mobile="9000000022")

    results = _push(
        ctx,
        user,
        [_snapshot(first, ward="9"), _snapshot(second, caste="SampleGroupZ")],
    )["results"]

    assert [item["status"] for item in results] == ["forbidden", "applied"]
    assert _get(ctx, first["id"])["ward"] == first["ward"]
    assert _get(ctx, second["id"])["caste"] == "SampleGroupZ"


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
    stored = _get(ctx, voter["id"])
    for field, value in LOCKED_CHANGES.items():
        assert stored[field] == value


@pytest.mark.parametrize("role", ["admin", "user"])
def test_voter_id_cannot_change_via_sync_for_any_role(ctx, role):
    headers = ctx.auth if role == "admin" else make_user(ctx)
    voter = _create(ctx)

    result = _push(ctx, headers, [_snapshot(voter, voter_id="ZZZ9999999")])["results"][0]

    assert result["status"] == "validation_error"
    assert "cannot be changed" in result["message"]
    assert _get(ctx, voter["id"])["voter_id"] == voter["voter_id"]


def test_sync_user_cannot_delete_a_voter(ctx):
    user = make_user(ctx)
    voter = _create(ctx)

    result = _push(ctx, user, [{"id": voter["id"], "updated_at": _later(), "deleted": True}])[
        "results"
    ][0]

    assert result["status"] == "forbidden"
    assert _get(ctx, voter["id"])["deleted_at"] is None


def test_sync_user_can_still_create_a_new_voter(ctx):
    user = make_user(ctx)
    body = voter_payload(voter_id="ZZZ1000003", mobile="9000000023")
    body.update(id="00000000-0000-4000-8000-000000000003", updated_at=_later(), deleted=False)

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

    page = ctx.client.get(f"/voters/{voter['id']}/edit").text

    for field in ("booth", "ward", "first_name", "village", "permanent_address"):
        assert "readonly" in _tag(page, "input", field), field
    for field in ("is_local", "gender"):
        assert "disabled" in _tag(page, "select", field), field
    for field in ("address", "caste", "mobile"):
        assert "readonly" not in _tag(page, "input", field), field
    assert "disabled" not in _tag(page, "select", "party")
    assert 'id="voter_id"' not in page  # VoterId stays plain text


def test_web_edit_form_is_fully_editable_for_an_admin_except_voter_id(ctx):
    voter = _create(ctx)
    _web_login(ctx, "admin", "admin-pass-123")

    page = ctx.client.get(f"/voters/{voter['id']}/edit").text

    assert "readonly" not in _tag(page, "input", "first_name")
    assert "disabled" not in _tag(page, "select", "is_local")
    assert 'id="voter_id"' not in page


def _edit_post(ctx, voter: dict, **fields) -> object:
    token = csrf_from_page(ctx.client.get(f"/voters/{voter['id']}/edit").text)
    return ctx.client.post(
        f"/voters/{voter['id']}/edit",
        data={"csrf_token": token, **fields},
        follow_redirects=False,
    )


def test_web_user_post_changes_only_the_four_allowed_fields(ctx):
    voter = _create(ctx)
    make_user(ctx)
    _web_login(ctx, USER_NAME, USER_PASSWORD)
    # What a browser sends: read-only text inputs are posted unchanged, disabled selects are not.
    posted = {
        "booth": voter["booth"],
        "ward": voter["ward"],
        "first_name": voter["first_name"],
        "village": voter["village"],
        "permanent_address": voter["permanent_address"],
        "age": str(voter["age"]),
        **ALLOWED_CHANGES,
    }

    saved = _edit_post(ctx, voter, **posted)

    assert saved.status_code == 303, saved.text
    stored = _get(ctx, voter["id"])
    for field, value in ALLOWED_CHANGES.items():
        assert stored[field] == value
    assert stored["is_local"] is True and stored["gender"] == "Female"
    assert stored["first_name"] == voter["first_name"]


def test_web_user_post_with_a_tampered_locked_field_is_refused(ctx):
    voter = _create(ctx)
    make_user(ctx)
    _web_login(ctx, USER_NAME, USER_PASSWORD)

    refused = _edit_post(ctx, voter, first_name="Hacked", caste="SampleGroupZ")

    assert refused.status_code == 403
    assert "cannot change" in refused.text
    assert _get(ctx, voter["id"]) == voter


def test_web_admin_post_can_change_locked_fields(ctx):
    voter = _create(ctx)
    _web_login(ctx, "admin", "admin-pass-123")

    saved = _edit_post(
        ctx,
        voter,
        first_name="Renamed",
        village="Newvillage",
        permanent_address="New Lane 1",
        is_local="No",
        gender="Female",
    )

    assert saved.status_code == 303, saved.text
    stored = _get(ctx, voter["id"])
    assert (stored["first_name"], stored["village"], stored["is_local"]) == ("Renamed", "Newvillage", False)


# --- New columns: create / list / report / search ------------------------------


def test_new_columns_default_to_blank_and_not_local(ctx):
    bare = voter_payload()
    for key in ("address", "village", "permanent_address", "is_local"):
        del bare[key]

    created = ctx.client.post("/api/voters", headers=ctx.auth, json=bare).json()

    assert created["address"] is None and created["village"] is None
    assert created["permanent_address"] is None
    assert created["is_local"] is False


def test_is_local_rejects_values_other_than_yes_or_no(ctx):
    bad = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload(is_local="maybe"))

    assert bad.status_code == 422


def test_search_filters_by_village_and_local(ctx):
    _create(ctx, village="Alpha Village", is_local=True)
    _create(ctx, voter_id="ZZZ1000002", mobile="9000000022", village="Beta Village", is_local=False)

    by_village = search(ctx, village="beta").json()
    local_yes = search(ctx, is_local="Yes").json()
    local_no = search(ctx, is_local="No").json()
    local_any = search(ctx, is_local="Any").json()
    both = search(ctx, village="alpha", is_local="No").json()

    assert [i["voter_id"] for i in by_village["items"]] == ["ZZZ1000002"]
    assert [i["voter_id"] for i in local_yes["items"]] == ["ZZZ1000001"]
    assert [i["voter_id"] for i in local_no["items"]] == ["ZZZ1000002"]
    assert local_any["total"] == 2
    assert both["total"] == 0


def test_free_text_search_matches_address_and_village(ctx):
    _create(ctx, address="Unique Alley 77", village="Hamletia")

    assert search(ctx, q="unique alley").json()["total"] == 1
    assert search(ctx, q="hamletia").json()["total"] == 1


def test_list_page_shows_village_and_local_columns_without_house_no(ctx):
    _create(ctx)
    _web_login(ctx, "admin", "admin-pass-123")

    page = ctx.client.get("/voters").text

    for header in ("<th>Address</th>", "<th>Village</th>", "<th>Local</th>"):
        assert header in page
    assert 'name="village"' in page and 'name="is_local"' in page
    assert "HouseNo" not in page and "house_no" not in page


def test_form_pages_have_no_house_no_and_show_the_new_fields(ctx):
    voter = _create(ctx)
    _web_login(ctx, "admin", "admin-pass-123")

    for url in ("/voters/new", f"/voters/{voter['id']}/edit"):
        page = ctx.client.get(url).text
        assert "HouseNo" not in page and "house_no" not in page
        for label in ("Address", "Village", "Permanent address", "Local"):
            assert f">{label}</label>" in page, (url, label)


def test_report_summary_and_page_count_village_and_local(ctx):
    _create(ctx, village="Alpha Village", is_local=True)
    _create(ctx, voter_id="ZZZ1000002", mobile="9000000022", village="Alpha Village", is_local=False)
    _create(ctx, voter_id="ZZZ1000003", mobile="9000000023", village=None, is_local=False)

    summary = ctx.client.get("/api/reports/summary", headers=ctx.auth).json()
    exported = ctx.client.get("/api/reports/export.csv", headers=ctx.auth).content.decode("utf-8-sig")
    _web_login(ctx, "admin", "admin-pass-123")
    page = ctx.client.get("/reports").text

    assert {"label": "Alpha Village", "count": 2} in summary["by_village"]
    assert {"label": "(blank)", "count": 1} in summary["by_village"]
    assert summary["by_local"] == [{"label": "Yes", "count": 1}, {"label": "No", "count": 2}]
    assert "Village,Alpha Village,2" in exported and "Local,Yes,1" in exported
    assert "<h2>Village</h2>" in page and "<h2>Local</h2>" in page
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


def test_csv_export_has_the_new_headers_and_no_house_no(ctx):
    _create(ctx)
    _create(ctx, voter_id="ZZZ1000002", mobile="9000000022", is_local=False)

    exported = ctx.client.post("/api/voters/export.csv", headers=ctx.auth, json={})

    rows = list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))
    assert list(rows[0]) == list(CSV_HEADERS)
    assert {"Address", "Village", "Permanent address", "Local"} <= set(rows[0])
    assert not {"HouseNo", "House No", "house_no"} & set(rows[0])
    assert {row["VoterId"]: row["Local"] for row in rows} == {"ZZZ1000001": "Yes", "ZZZ1000002": "No"}
    assert rows[0]["Village"] == "Samplevillage"


def test_csv_export_import_round_trip_keeps_the_new_columns(ctx):
    voter = _create(ctx, village="Roundtrip Village", permanent_address="Round Lane 3")
    exported = ctx.client.post("/api/voters/export.csv", headers=ctx.auth, json={}).content
    assert ctx.client.delete(f"/api/voters/{voter['id']}", headers=ctx.auth).status_code == 200

    again = _import(ctx, exported.decode("utf-8-sig"))

    assert again.json()["imported"] == 1, again.text
    restored = search(ctx, voter_id="ZZZ1000001").json()["items"][0]
    assert restored["village"] == "Roundtrip Village"
    assert restored["permanent_address"] == "Round Lane 3"
    assert restored["is_local"] is True


def test_csv_import_accepts_the_new_headers_with_local_yes_no(ctx):
    header = ",".join(CSV_HEADERS)
    row = "1,2,3,4,{vid},Newhdr,Row,Rel,Main St,Samplevillage,Sample Lane,{local},Male,40,G,9000000091,N,F,"

    imported = _import(
        ctx,
        "\n".join([header, row.format(vid="ZZZ0000601", local="Yes"), row.format(vid="ZZZ0000602", local="no"),
                   row.format(vid="ZZZ0000603", local="")]),
    )

    assert imported.json()["imported"] == 3, imported.text
    flags = {i["voter_id"]: i["is_local"] for i in search(ctx).json()["items"]}
    assert flags == {"ZZZ0000601": True, "ZZZ0000602": False, "ZZZ0000603": False}


def test_csv_import_reports_a_bad_local_value_for_that_row(ctx):
    header = ",".join(CSV_HEADERS)
    row = "1,2,3,4,ZZZ0000604,Badlocal,Row,Rel,,,,perhaps,Male,40,G,9000000091,N,F,"

    imported = _import(ctx, "\n".join([header, row]))

    report = imported.json()
    assert report["imported"] == 0
    assert report["errors"][0]["field"] == "Local"


@pytest.mark.parametrize(
    ("house_header", "booth", "ward"),
    [("HouseNo", "2024 Booth", "2026 Ward"), ("house_no", "booth_2024", "ward_2026"), ("House No", "Booth", "Ward")],
)
def test_csv_import_accepts_old_headers_and_folds_house_no_into_address(ctx, house_header, booth, ward):
    text = "\n".join([_legacy_header(house=house_header, booth=booth, ward=ward), _legacy_row("ZZZ0000605", "12")])

    imported = _import(ctx, text)

    assert imported.status_code == 200, imported.text
    assert imported.json()["imported"] == 1
    stored = search(ctx, voter_id="ZZZ0000605").json()["items"][0]
    assert stored["address"] == "H.No 12"
    assert stored["booth"] == "7" and stored["ward"] == "2"
    assert stored["village"] is None and stored["is_local"] is False
    assert "house_no" not in stored


def test_csv_import_folds_house_no_in_front_of_an_existing_address(ctx):
    header = _legacy_header() + ",Address"
    rows = [
        _legacy_row("ZZZ0000606", "12") + ",Main St",
        _legacy_row("ZZZ0000607", "") + ",Side St",
        _legacy_row("ZZZ0000608", "9") + ",",
    ]

    imported = _import(ctx, "\n".join([header, *rows]))

    assert imported.json()["imported"] == 3, imported.text
    addresses = {i["voter_id"]: i["address"] for i in search(ctx).json()["items"]}
    assert addresses == {
        "ZZZ0000606": "H.No 12, Main St",
        "ZZZ0000607": "Side St",
        "ZZZ0000608": "H.No 9",
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
    ("house_no", "address", "expected"),
    [
        ("12", "Main St", "H.No 12, Main St"),
        ("12", "", "H.No 12"),
        ("12", None, "H.No 12"),
        ("  12-A ", "  Main St ", "H.No 12-A, Main St"),
        ("", "Main St", "Main St"),
        (None, "Main St", "Main St"),
        ("   ", "Main St", "Main St"),
        (None, None, None),
    ],
)
def test_fold_house_no(house_no, address, expected):
    assert fold_house_no(house_no, address) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Yes", True), ("yes", True), ("Y", True), (True, True), ("No", False), ("", False), (None, False), (False, False)],
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

    command.upgrade(cfg, "head")

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
