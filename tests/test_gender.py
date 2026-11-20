"""Gender: optional voter field between HouseNo and Age. Synthetic data only."""

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
from openpyxl import load_workbook

from app.constants import (
    CSV_HEADERS,
    DATA_FIELDS,
    DROPPED_IMPORT_HEADERS,
    GENDERS,
    SEARCH_FILTER_FIELDS,
    USER_EDITABLE_FIELDS,
)
from app.validation import canonical_gender
from app.web.templates import display
from tests.conftest import csrf_from_page, make_user, search, voter_payload

ROOT = Path(__file__).resolve().parents[1]
USER_NAME, USER_PASSWORD = "field.user", "field-pass-123"

EXPECTED_COLUMNS = [
    "Booth", "Booth S.No", "Ward", "Ward S.No", "VoterId", "FirstName", "LastName",
    "RelativeName", "HouseNo", "Gender", "Age", "Street", "Mobile", "Caste", "Village",
    "Panchayath", "Mandal", "Current City", "Local/Non-Local", "Party", "Constituency",
]


def _create(ctx, **overrides) -> dict:
    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload(**overrides))
    assert created.status_code == 201, created.text
    return created.json()


def _get(ctx, voter_id: str) -> dict:
    return ctx.client.get(f"/api/voters/{voter_id}", headers=ctx.auth).json()


def _web_login(ctx, username: str = "admin", password: str = "admin-pass-123") -> None:
    ctx.client.cookies.clear()
    signed = ctx.client.post(
        "/login",
        data={"username": username, "password": password, "next": "/voters"},
        follow_redirects=False,
    )
    assert signed.status_code == 303, signed.text


def _import(ctx, text: str):
    return ctx.client.post(
        "/api/voters/import.csv",
        headers=ctx.auth,
        files={"file": ("voters.csv", text.encode("utf-8"), "text/csv")},
    )


def _three_voters(ctx) -> None:
    _create(ctx, voter_id="ZZZ2000001", mobile="9000000031", gender="Male")
    _create(ctx, voter_id="ZZZ2000002", mobile="9000000032", gender="Female")
    _create(ctx, voter_id="ZZZ2000003", mobile="9000000033")  # gender left blank


# --- Column contract -------------------------------------------------------------


def test_voter_columns_are_in_veeras_order_with_gender_after_house_no():
    assert list(CSV_HEADERS) == EXPECTED_COLUMNS
    assert len(CSV_HEADERS) == 21
    assert DATA_FIELDS.index("gender") == DATA_FIELDS.index("house_no") + 1
    assert DATA_FIELDS.index("age") == DATA_FIELDS.index("gender") + 1


def test_gender_is_a_real_import_column_while_the_other_dropped_headers_stay_dropped():
    assert "Gender" not in DROPPED_IMPORT_HEADERS and "gender" not in DROPPED_IMPORT_HEADERS
    assert {"Permanent address", "Profession"} <= DROPPED_IMPORT_HEADERS
    assert GENDERS == ("Male", "Female", "Other")


def test_gender_is_locked_for_regular_users_and_filtered_by_post_only():
    assert "gender" not in USER_EDITABLE_FIELDS
    assert "gender" in SEARCH_FILTER_FIELDS


# --- Validation ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Male", "Male"), ("male", "Male"), ("MALE", "Male"), (" female ", "Female"),
        ("OTHER", "Other"), ("", None), ("   ", None), (None, None),
    ],
)
def test_canonical_gender_canonicalizes_case_and_blank(raw, expected):
    assert canonical_gender(raw) == expected


@pytest.mark.parametrize("raw", ["Unknown", "M", "Prefer not to say", "Maleish"])
def test_canonical_gender_rejects_other_values(raw):
    with pytest.raises(ValueError, match="Male, Female, Other"):
        canonical_gender(raw)


# --- API create / edit ------------------------------------------------------------


@pytest.mark.parametrize(("sent", "stored"), [("Male", "Male"), ("female", "Female"), ("OTHER", "Other"), ("", None)])
def test_create_api_accepts_the_allowed_values_and_blank(ctx, sent, stored):
    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload(gender=sent))

    assert created.status_code == 201, created.text
    assert created.json()["gender"] == stored
    assert _get(ctx, "ZZZ1000001")["gender"] == stored


def test_create_api_without_gender_stores_null(ctx):
    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload())

    assert created.status_code == 201
    assert created.json()["gender"] is None


def test_create_api_rejects_an_invalid_gender_with_a_clear_message(ctx):
    rejected = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload(gender="Unknown"))

    assert rejected.status_code == 422
    assert "Male, Female, Other" in rejected.text
    assert search(ctx).json()["total"] == 0


def test_admin_patch_sets_changes_and_clears_gender(ctx):
    voter = _create(ctx)

    for sent, stored in (("Male", "Male"), ("female", "Female"), ("Other", "Other"), ("", None)):
        patched = ctx.client.patch(f"/api/voters/{voter['voter_id']}", headers=ctx.auth, json={"gender": sent})
        assert patched.status_code == 200, patched.text
        assert patched.json()["gender"] == stored


def test_patch_rejects_an_invalid_gender_and_keeps_the_stored_value(ctx):
    voter = _create(ctx, gender="Male")

    rejected = ctx.client.patch(f"/api/voters/{voter['voter_id']}", headers=ctx.auth, json={"gender": "Robot"})

    assert rejected.status_code == 422
    assert _get(ctx, voter["voter_id"])["gender"] == "Male"


def test_regular_user_cannot_change_gender_on_an_existing_voter(ctx):
    user = make_user(ctx)
    voter = _create(ctx, gender="Male")

    denied = ctx.client.patch(f"/api/voters/{voter['voter_id']}", headers=user, json={"gender": "Female"})

    assert denied.status_code == 403
    assert "Gender" in denied.json()["detail"]
    assert _get(ctx, voter["voter_id"])["gender"] == "Male"


def test_regular_user_echoing_the_stored_gender_back_is_not_refused(ctx):
    user = make_user(ctx)
    voter = _create(ctx, gender="Male")

    patched = ctx.client.patch(
        f"/api/voters/{voter['voter_id']}", headers=user, json={"gender": "male", "caste": "SampleGroupZ"}
    )

    assert patched.status_code == 200, patched.text
    assert patched.json()["gender"] == "Male"


def test_sync_push_stores_a_canonical_gender_and_rejects_an_invalid_one(ctx):
    voter = _create(ctx)
    later = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
    snapshot = {field: voter[field] for field in DATA_FIELDS}
    snapshot.update(updated_at=later, deleted=False)

    good = ctx.client.post(
        "/api/sync/push",
        headers=ctx.auth,
        json={"device_id": "lab-phone-1", "changes": [{**snapshot, "gender": "female"}]},
    )

    assert good.status_code == 200, good.text
    assert good.json()["results"][0]["status"] == "applied"
    assert _get(ctx, voter["voter_id"])["gender"] == "Female"
    bad = ctx.client.post(
        "/api/sync/push",
        headers=ctx.auth,
        json={"device_id": "lab-phone-1", "changes": [{**snapshot, "gender": "Robot"}]},
    )
    assert bad.status_code == 422 or bad.json()["results"][0]["status"] == "validation_error"
    assert _get(ctx, voter["voter_id"])["gender"] == "Female"


# --- Web list and form ------------------------------------------------------------------


def test_list_headers_are_exactly_the_21_columns_then_actions(ctx):
    _create(ctx, gender="Female")
    _web_login(ctx)

    page = ctx.client.get("/voters").text

    thead = re.search(r"<thead>(.*?)</thead>", page, re.S).group(1)
    assert re.findall(r"<th>([^<]*)</th>", thead) == [*EXPECTED_COLUMNS, ""]
    assert re.findall(r'<td data-label="([^"]*)"', page) == [*EXPECTED_COLUMNS, "Actions"]


def test_list_shows_gender_and_the_placeholder_for_a_voter_without_one(ctx):
    # Arrange
    ids = {"Male": "ZZZ2000003", "Female": "ZZZ2000004", "Other": "ZZZ2000001", None: "ZZZ2000002"}
    for offset, (gender, voter_id) in enumerate(ids.items()):
        # The voter without a gender also has no house number, so both show the placeholder.
        extra = {"gender": gender} if gender else {"house_no": ""}
        _create(ctx, voter_id=voter_id, mobile=f"90000000{31 + offset}", **extra)
    _web_login(ctx)

    # Act
    page = ctx.client.get("/voters").text

    # Assert
    rows = {g: next(r for r in page.split("<tr>") if v in r) for g, v in ids.items()}
    for gender in ("Male", "Female", "Other"):
        assert f'<td data-label="Gender">{gender}</td>' in rows[gender]
    placeholder = display(None)
    assert f'<td data-label="Gender">{placeholder}</td>' in rows[None]
    assert '<td data-label="Gender"></td>' not in rows[None]
    # Same placeholder as other empty fields on the row (e.g. HouseNo).
    assert f'<td data-label="HouseNo" translate="no">{placeholder}</td>' in rows[None]


def test_form_has_gender_select_between_house_no_and_age(ctx):
    voter = _create(ctx)
    _web_login(ctx)

    for url in ("/voters/new", f"/voters/{voter['voter_id']}/edit"):
        page = ctx.client.get(url).text
        assert page.index('id="house_no"') < page.index('id="gender"') < page.index('id="age"'), url
        select = re.search(r'<select id="gender".*?</select>', page, re.S).group(0)
        assert re.findall(r'<option value="([^"]*)"', select) == ["", "Male", "Female", "Other"], url


def _gender_tag(ctx, url: str) -> str:
    return re.search(r'<select id="gender"[^>]*>', ctx.client.get(url).text).group(0)


def test_edit_form_locks_gender_for_a_user_but_not_for_an_admin(ctx):
    voter = _create(ctx, gender="Male")
    make_user(ctx)

    _web_login(ctx, USER_NAME, USER_PASSWORD)
    user_tag = _gender_tag(ctx, f"/voters/{voter['voter_id']}/edit")
    new_tag = _gender_tag(ctx, "/voters/new")
    _web_login(ctx)
    admin_tag = _gender_tag(ctx, f"/voters/{voter['voter_id']}/edit")

    assert "disabled" in user_tag
    assert "disabled" not in admin_tag
    assert "disabled" not in new_tag  # a new voter may be created with a gender


def _edit_post(ctx, voter_id: str, /, **fields):
    token = csrf_from_page(ctx.client.get(f"/voters/{voter_id}/edit").text)
    return ctx.client.post(
        f"/voters/{voter_id}/edit", data={"csrf_token": token, **fields}, follow_redirects=False
    )


def _posted_row(voter: dict, **overrides) -> dict:
    """What a browser posts for an existing voter: every text field, as stored."""
    row = {field: ("" if voter[field] is None else str(voter[field])) for field in DATA_FIELDS}
    row["is_local"] = "Local" if voter["is_local"] else "Non-Local"
    row.update(overrides)
    return row


def test_web_admin_edit_saves_each_gender_value_and_blank(ctx):
    voter = _create(ctx)
    _web_login(ctx)

    for sent, stored in (("Male", "Male"), ("Female", "Female"), ("Other", "Other"), ("", None)):
        saved = _edit_post(ctx, voter["voter_id"], **_posted_row(voter, gender=sent))
        assert saved.status_code == 303, saved.text
        assert _get(ctx, voter["voter_id"])["gender"] == stored


def test_web_user_post_cannot_change_gender(ctx):
    voter = _create(ctx, gender="Male")
    make_user(ctx)
    _web_login(ctx, USER_NAME, USER_PASSWORD)

    refused = _edit_post(ctx, voter["voter_id"], **_posted_row(voter, gender="Female"))

    assert refused.status_code == 403
    assert "Gender" in refused.text
    assert _get(ctx, voter["voter_id"])["gender"] == "Male"


def test_web_user_edit_without_the_disabled_gender_keeps_it(ctx):
    voter = _create(ctx, gender="Male")
    make_user(ctx)
    _web_login(ctx, USER_NAME, USER_PASSWORD)
    posted = {key: value for key, value in _posted_row(voter, caste="SampleGroupZ").items() if key != "gender"}

    saved = _edit_post(ctx, voter["voter_id"], **posted)  # a disabled select is not posted

    assert saved.status_code == 303, saved.text
    stored = _get(ctx, voter["voter_id"])
    assert stored["gender"] == "Male" and stored["caste"] == "SampleGroupZ"


def test_web_create_saves_gender_and_rejects_an_invalid_one(ctx):
    _web_login(ctx)
    token = csrf_from_page(ctx.client.get("/voters/new").text)
    base = {"csrf_token": token, "voter_id": "ZZZ2000010", "first_name": "Webperson"}

    created = ctx.client.post("/voters/new", data={**base, "gender": "Female"}, follow_redirects=False)
    rejected = ctx.client.post(
        "/voters/new", data={**base, "voter_id": "ZZZ2000011", "gender": "Robot"}, follow_redirects=False
    )

    assert created.status_code == 303, created.text
    assert _get(ctx, "ZZZ2000010")["gender"] == "Female"
    assert rejected.status_code == 400
    assert "Gender" in rejected.text
    assert ctx.client.get("/api/voters/ZZZ2000011", headers=ctx.auth).status_code == 404


# --- Search filter ----------------------------------------------------------------------


def test_api_gender_filter_returns_only_matching_rows(ctx):
    _three_voters(ctx)

    female = search(ctx, gender="Female").json()
    lower = search(ctx, gender="female").json()
    other = search(ctx, gender="Other").json()

    assert [item["voter_id"] for item in female["items"]] == ["ZZZ2000002"]
    assert lower["total"] == 1
    assert other["total"] == 0


@pytest.mark.parametrize("blank", ["", "  ", "Any", "any", None])
def test_blank_or_any_gender_filter_does_not_restrict(ctx, blank):
    _three_voters(ctx)

    assert search(ctx, gender=blank).json()["total"] == 3


def test_invalid_gender_filter_is_a_422(ctx):
    assert search(ctx, gender="Robot").status_code == 422


def test_web_gender_filter_is_post_only_and_a_url_filter_is_ignored(ctx):
    _three_voters(ctx)
    _web_login(ctx)
    token = csrf_from_page(ctx.client.get("/voters").text)

    posted = ctx.client.post("/voters/search", data={"csrf_token": token, "gender": "Male"})
    via_url = ctx.client.get("/voters?gender=Male")
    any_posted = ctx.client.post("/voters/search", data={"csrf_token": token, "gender": ""})

    assert "ZZZ2000001" in posted.text
    assert "ZZZ2000002" not in posted.text and "ZZZ2000003" not in posted.text
    assert "match the filters" in posted.text
    assert '<option value="Male" selected>' in posted.text
    assert 'name="gender" value="Male"' in posted.text  # carried to pager/export as a hidden input
    assert all(f"ZZZ200000{n}" in via_url.text for n in (1, 2, 3))  # URL filters are never read
    assert "match the filters" not in via_url.text
    assert all(f"ZZZ200000{n}" in any_posted.text for n in (1, 2, 3))


def test_web_filter_form_offers_any_male_female_other(ctx):
    _create(ctx)
    _web_login(ctx)

    page = ctx.client.get("/voters").text

    select = re.search(r'<select id="gender" name="gender">.*?</select>', page, re.S).group(0)
    assert re.findall(r'<option value="([^"]*)"', select) == ["", "Male", "Female", "Other"]
    assert ">Any</option>" in select


def test_gender_filter_is_applied_to_exports(ctx):
    _three_voters(ctx)

    exported = ctx.client.post("/api/voters/export.csv", headers=ctx.auth, json={"gender": "Female"})

    rows = list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))
    assert [row["VoterId"] for row in rows] == ["ZZZ2000002"]


# --- Export ---------------------------------------------------------------------------------


def test_csv_export_uses_the_21_columns_and_includes_gender_values(ctx):
    _three_voters(ctx)

    exported = ctx.client.post("/api/voters/export.csv", headers=ctx.auth, json={})

    text = exported.content.decode("utf-8-sig")
    assert next(csv.reader(io.StringIO(text))) == EXPECTED_COLUMNS
    rows = {row["VoterId"]: row for row in csv.DictReader(io.StringIO(text))}
    assert [rows[key]["Gender"] for key in ("ZZZ2000001", "ZZZ2000002", "ZZZ2000003")] == ["Male", "Female", ""]


def test_excel_export_uses_the_21_columns_and_includes_gender_values(ctx):
    _three_voters(ctx)

    response = ctx.client.post("/api/voters/export.xlsx", headers=ctx.auth, json={})

    sheet = load_workbook(io.BytesIO(response.content))["Voters"]
    header = [cell.value for cell in sheet[1]]
    assert header == EXPECTED_COLUMNS
    id_col, gender_col = header.index("VoterId") + 1, header.index("Gender") + 1
    values = {
        sheet.cell(row=r, column=id_col).value: sheet.cell(row=r, column=gender_col).value
        for r in range(2, sheet.max_row + 1)
    }
    assert values["ZZZ2000001"] == "Male" and values["ZZZ2000002"] == "Female"
    assert values["ZZZ2000003"] in (None, "")


def test_web_admin_export_keeps_the_21_column_order_and_the_filter(ctx):
    _three_voters(ctx)
    _web_login(ctx)
    token = csrf_from_page(ctx.client.get("/voters").text)

    exported = ctx.client.post("/voters/export.csv", data={"csrf_token": token, "gender": "Male"})

    assert exported.status_code == 200
    rows = list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))
    assert list(rows[0]) == EXPECTED_COLUMNS
    assert [row["VoterId"] for row in rows] == ["ZZZ2000001"]


# --- Import ---------------------------------------------------------------------------------


def test_csv_import_persists_valid_gender_values_and_canonicalizes_case(ctx):
    header = ",".join(CSV_HEADERS)
    base = "1,2,3,4,{vid},Csvperson,Row,Rel,H1,{gender},40,Main St,9000000091,G,V,P,M,N,Local,,"

    imported = _import(
        ctx,
        "\n".join([
            header,
            base.format(vid="ZZZ2000021", gender="Male"),
            base.format(vid="ZZZ2000022", gender="female"),
            base.format(vid="ZZZ2000023", gender="OTHER"),
            base.format(vid="ZZZ2000024", gender=""),
        ]),
    )

    assert imported.json()["imported"] == 4, imported.text
    stored = {item["voter_id"]: item["gender"] for item in search(ctx).json()["items"]}
    assert stored == {"ZZZ2000021": "Male", "ZZZ2000022": "Female", "ZZZ2000023": "Other", "ZZZ2000024": None}


def test_csv_import_accepts_a_lowercase_gender_header(ctx):
    imported = _import(ctx, "voter_id,first_name,gender\nZZZ2000030,Csvperson,Male\n")

    assert imported.json()["imported"] == 1, imported.text
    assert search(ctx, voter_id="ZZZ2000030").json()["items"][0]["gender"] == "Male"


def test_csv_import_reports_an_invalid_gender_for_that_row_only(ctx):
    body = "VoterId,FirstName,Gender\nZZZ2000040,Good,Male\nZZZ2000041,Bad,Robot\n"

    imported = _import(ctx, body)

    report = imported.json()
    assert report["imported"] == 1 and report["skipped"] == 1
    assert report["errors"][0]["row"] == 3 and report["errors"][0]["field"] == "Gender"
    assert search(ctx, voter_id="ZZZ2000041").json()["total"] == 0


def test_csv_without_a_gender_column_still_imports_with_null_gender(ctx):
    header = ",".join(h for h in CSV_HEADERS if h != "Gender")
    row = "1,2,3,4,ZZZ2000050,Csvperson,Row,Rel,H1,40,Main St,9000000091,G,V,P,M,N,Local,,"

    imported = _import(ctx, "\n".join([header, row]))

    assert imported.json()["imported"] == 1, imported.text
    assert search(ctx, voter_id="ZZZ2000050").json()["items"][0]["gender"] is None


def test_old_csv_still_ignores_permanent_address_and_profession_but_keeps_gender(ctx):
    header = "VoterId,FirstName,Gender,Permanent address,Profession"
    row = "ZZZ2000060,Oldperson,Female,Perm lane,Farmer"

    imported = _import(ctx, f"{header}\n{row}\n")

    assert imported.status_code == 200, imported.text
    assert imported.json()["imported"] == 1
    voter = search(ctx, voter_id="ZZZ2000060").json()["items"][0]
    assert voter["gender"] == "Female"
    assert not {"profession", "permanent_address"} & set(voter)


def test_sample_csv_header_is_the_21_columns_and_gender_values_are_valid(ctx):
    sample = (ROOT / "sample" / "voters_sample.csv").read_text(encoding="utf-8")

    rows = list(csv.DictReader(io.StringIO(sample)))

    assert next(csv.reader(io.StringIO(sample))) == EXPECTED_COLUMNS
    assert {row["Gender"] for row in rows} <= {"", *GENDERS}
    assert {"Male", "Female", "Other"} <= {row["Gender"] for row in rows}
    assert _import(ctx, sample).json()["imported"] == len(rows)


# --- Soft delete ---------------------------------------------------------------------------


def test_deleting_a_voter_erases_gender_from_the_tombstone(ctx):
    voter = _create(ctx, gender="Female")

    assert ctx.client.delete(f"/api/voters/{voter['voter_id']}", headers=ctx.auth).status_code == 200

    pulled = ctx.client.get("/api/sync/pull?since=0", headers=ctx.auth)
    tombstones = [row for row in pulled.json()["changes"] if row["voter_id"] == voter["voter_id"]]
    assert tombstones and all(row["gender"] is None for row in tombstones)


# --- Migration ------------------------------------------------------------------------------


def _alembic(db_file: Path) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", "sqlite:///" + db_file.as_posix())
    return cfg


def _insert_row(conn, voter_id: str) -> None:
    conn.execute(
        "INSERT INTO voters (voter_id, first_name, created_at, updated_at, revision)"
        " VALUES (?, 'Fakeperson', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 1)",
        (voter_id,),
    )


def test_migration_adds_a_nullable_gender_column_and_leaves_existing_rows_null(tmp_path):
    db_file = tmp_path / "old.db"
    cfg = _alembic(db_file)
    command.upgrade(cfg, "0008_add_constituency")
    with sqlite3.connect(db_file) as conn:
        _insert_row(conn, "ZZZ9000001")
        _insert_row(conn, "ZZZ9000002")

    command.upgrade(cfg, "0009_add_gender")

    with sqlite3.connect(db_file) as conn:
        info = {row[1]: row for row in conn.execute("PRAGMA table_info(voters)")}
        genders = [row[0] for row in conn.execute("SELECT gender FROM voters ORDER BY voter_id")]
        assert conn.execute("SELECT COUNT(*) FROM voters").fetchone() == (2,)
    assert "gender" in info
    assert info["gender"][3] == 0  # notnull = 0: nullable
    assert info["gender"][4] is None  # no server default that would invent data
    assert genders == [None, None]


def test_migration_check_constraint_allows_the_three_values_and_null_only(tmp_path):
    db_file = tmp_path / "check.db"
    command.upgrade(_alembic(db_file), "head")

    with sqlite3.connect(db_file) as conn:
        for number, value in enumerate(("Male", "Female", "Other", None), start=1):
            _insert_row(conn, f"ZZZ900001{number}")
            conn.execute("UPDATE voters SET gender = ? WHERE voter_id = ?", (value, f"ZZZ900001{number}"))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE voters SET gender = 'Robot' WHERE voter_id = 'ZZZ9000011'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE voters SET gender = 'male' WHERE voter_id = 'ZZZ9000011'")


def test_migration_downgrade_drops_gender_and_keeps_every_row(tmp_path):
    db_file = tmp_path / "down.db"
    cfg = _alembic(db_file)
    command.upgrade(cfg, "0009_add_gender")
    with sqlite3.connect(db_file) as conn:
        _insert_row(conn, "ZZZ9000001")
        conn.execute("UPDATE voters SET gender = 'Male'")

    command.downgrade(cfg, "0008_add_constituency")

    with sqlite3.connect(db_file) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(voters)")}
        assert conn.execute("SELECT COUNT(*) FROM voters").fetchone() == (1,)
    assert "gender" not in columns and "constituency" in columns
