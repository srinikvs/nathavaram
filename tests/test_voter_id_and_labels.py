"""VoterId is the primary key and immutable after create; "2024 Booth"/"2026 Ward" are "Booth"/"Ward"."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone

from alembic import command
from alembic.config import Config

from app.config import ROOT
from app.constants import CSV_HEADERS
from tests.conftest import csrf_from_page, voter_payload

NEW_HEADER = ",".join(CSV_HEADERS)
OLD_HEADER = NEW_HEADER.replace("Booth", "2024 Booth", 1).replace("Ward,", "2026 Ward,", 1)
ROW = (
    "7,10,2,1,{vid},Csvperson,Row,Rel,H-1,,40,Main St,9000000091,SampleGroupA,"
    "Samplevillage,Pan,Man,Nathavaram,Local,"
)


def _create(ctx, **overrides) -> dict:
    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload(**overrides))
    assert created.status_code == 201, created.text
    return created.json()


def _import(ctx, text: str):
    return ctx.client.post(
        "/api/voters/import.csv",
        headers=ctx.auth,
        files={"file": ("voters.csv", text.encode("utf-8"), "text/csv")},
    )


def _web_token(ctx) -> str:
    ctx.client.post("/login", data={"username": "admin", "password": "admin-pass-123"})
    return csrf_from_page(ctx.client.get("/voters").text)


# --- VoterId cannot change -------------------------------------------------


def test_patch_with_a_different_voter_id_is_rejected_and_the_row_is_unchanged(ctx):
    voter = _create(ctx)

    response = ctx.client.patch(
        f"/api/voters/{voter['voter_id']}", headers=ctx.auth, json={"voter_id": "ZZZ1000099"}
    )

    assert response.status_code in (400, 422)
    assert "cannot be changed" in response.text
    assert ctx.client.get(f"/api/voters/{voter['voter_id']}", headers=ctx.auth).json() == voter


def test_patch_that_mixes_a_new_voter_id_with_other_edits_changes_nothing(ctx):
    voter = _create(ctx)

    response = ctx.client.patch(
        f"/api/voters/{voter['voter_id']}",
        headers=ctx.auth,
        json={"voter_id": "ZZZ1000099", "first_name": "Renamed"},
    )

    assert response.status_code in (400, 422)
    assert ctx.client.get(f"/api/voters/{voter['voter_id']}", headers=ctx.auth).json() == voter


def test_patch_may_repeat_the_same_voter_id_and_other_fields_stay_editable(ctx):
    voter = _create(ctx)

    response = ctx.client.patch(
        f"/api/voters/{voter['voter_id']}",
        headers=ctx.auth,
        json={
            "voter_id": voter["voter_id"],
            "first_name": "Renamed",
            "booth": "99",
            "ward": "8",
            "party": "Jana Sena",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["voter_id"], body["first_name"], body["booth"], body["ward"]) == (
        voter["voter_id"],
        "Renamed",
        "99",
        "8",
    )


def test_web_edit_form_shows_voter_id_as_read_only_text(ctx):
    voter = _create(ctx)
    _web_token(ctx)

    edit = ctx.client.get(f"/voters/{voter['voter_id']}/edit")
    new = ctx.client.get("/voters/new")

    assert edit.status_code == 200
    assert re.search(r'id="voter_id_readonly"[^>]*>\s*ZZZ1000001\s*<', edit.text)
    assert 'name="voter_id"' not in edit.text
    assert 'name="voter_id"' in new.text  # creating still asks for a VoterId


def test_web_edit_post_with_a_different_voter_id_is_refused(ctx):
    voter = _create(ctx)
    token = _web_token(ctx)
    form = {**voter_payload(first_name="Hacked", voter_id="ZZZ1000099"), "csrf_token": token}

    response = ctx.client.post(f"/voters/{voter['voter_id']}/edit", data=form)

    assert response.status_code == 400
    assert "cannot be changed" in response.text
    stored = ctx.client.get(f"/api/voters/{voter['voter_id']}", headers=ctx.auth).json()
    assert stored["voter_id"] == "ZZZ1000001"
    assert stored["first_name"] == "Testperson"


def test_web_edit_post_without_voter_id_saves_the_other_fields(ctx):
    voter = _create(ctx)
    token = _web_token(ctx)
    form = voter_payload(first_name="Renamed", booth="55", ward="6")
    del form["voter_id"]

    response = ctx.client.post(
        f"/voters/{voter['voter_id']}/edit", data={**form, "csrf_token": token}, follow_redirects=False
    )

    assert response.status_code == 303
    stored = ctx.client.get(f"/api/voters/{voter['voter_id']}", headers=ctx.auth).json()
    assert (stored["voter_id"], stored["first_name"], stored["booth"], stored["ward"]) == (
        "ZZZ1000001",
        "Renamed",
        "55",
        "6",
    )


def test_sync_push_with_another_voter_id_is_a_different_record_and_leaves_the_original(ctx):
    voter = _create(ctx)
    change = {
        "voter_id": "ZZZ1000099",
        "updated_at": (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat(),
        "first_name": "Synced",
    }

    pushed = ctx.client.post(
        "/api/sync/push", headers=ctx.auth, json={"device_id": "lab-phone-1", "changes": [change]}
    )

    assert pushed.json()["results"][0]["status"] == "applied"
    stored = ctx.client.get(f"/api/voters/{voter['voter_id']}", headers=ctx.auth).json()
    assert stored == voter
    assert ctx.client.get("/api/voters/ZZZ1000099", headers=ctx.auth).json()["first_name"] == "Synced"


def test_sync_change_without_a_voter_id_is_rejected(ctx):
    change = {"updated_at": datetime.now(timezone.utc).isoformat(), "first_name": "NoKey"}

    pushed = ctx.client.post(
        "/api/sync/push", headers=ctx.auth, json={"device_id": "lab-phone-1", "changes": [change]}
    )

    assert pushed.status_code == 422


# --- Duplicate VoterId on add ----------------------------------------------


def test_duplicate_voter_id_on_add_returns_a_clear_error_from_the_api(ctx):
    _create(ctx)

    duplicate = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload())

    assert duplicate.status_code == 409
    detail = duplicate.json()["detail"]
    assert "ZZZ1000001" in detail
    assert "already exists" in detail
    assert "unique" in detail


def test_duplicate_voter_id_on_add_shows_a_clear_message_on_the_web_form(ctx):
    _create(ctx)
    token = _web_token(ctx)

    response = ctx.client.post("/voters/new", data={**voter_payload(), "csrf_token": token})

    assert response.status_code == 409
    assert "A voter with VoterId ZZZ1000001 already exists" in response.text
    assert "VoterId:" in response.text


# --- Booth / Ward labels ---------------------------------------------------


def test_web_pages_say_booth_and_ward_not_the_year_labels(ctx):
    _create(ctx)
    token = _web_token(ctx)

    pages = {
        "list": ctx.client.get("/voters").text,
        "search": ctx.client.post("/voters/search", data={"csrf_token": token, "booth": "12"}).text,
        "new": ctx.client.get("/voters/new").text,
        "reports": ctx.client.get("/reports").text,
        "import": ctx.client.get("/import").text,
    }

    # The import page deliberately documents the legacy headers it still accepts.
    legacy_note = re.search(r'<p class="lede">Only VoterId.*?</p>', pages["import"], re.S)
    assert legacy_note is not None and '"2024 Booth"' in legacy_note.group(0)
    pages["import"] = pages["import"].replace(legacy_note.group(0), "")

    for name, html in pages.items():
        assert "2024 Booth" not in html, name
        assert "2026 Ward" not in html, name
    assert '<label for="booth">Booth</label>' in pages["list"]
    assert '<label for="ward">Ward</label>' in pages["list"]
    assert "<th>Booth</th>" in pages["list"]
    assert "<th>Ward</th>" in pages["list"]
    assert 'id="booth" name="booth"' in pages["new"]
    assert "Booth" in pages["reports"] and "Ward" in pages["reports"]
    assert "Booth, Booth S.No, Ward, Ward S.No, VoterId," in pages["import"]


def test_search_filters_booth_and_ward(ctx):
    _create(ctx, booth="12A", ward="3")
    _create(ctx, voter_id="ZZZ1000002", mobile="9000000022", booth="40", ward="9")

    by_booth = ctx.client.post("/api/voters/search", headers=ctx.auth, json={"booth": "40"}).json()
    by_ward = ctx.client.post("/api/voters/search", headers=ctx.auth, json={"ward": "3"}).json()

    assert [item["voter_id"] for item in by_booth["items"]] == ["ZZZ1000002"]
    assert [item["voter_id"] for item in by_ward["items"]] == ["ZZZ1000001"]


def test_report_summary_and_report_csv_use_booth_and_ward(ctx):
    _create(ctx, booth="12A", ward="3")

    summary = ctx.client.get("/api/reports/summary", headers=ctx.auth).json()
    exported = ctx.client.get("/api/reports/export.csv", headers=ctx.auth).content.decode("utf-8-sig")

    assert summary["by_booth"] == [{"label": "12A", "count": 1}]
    assert summary["by_ward"] == [{"label": "3", "count": 1}]
    assert "Booth,12A,1" in exported
    assert "Ward,3,1" in exported
    assert "2024 Booth" not in exported and "2026 Ward" not in exported


def test_voter_csv_export_uses_booth_and_ward_headers(ctx):
    _create(ctx)

    exported = ctx.client.post("/api/voters/export.csv", headers=ctx.auth, json={})

    header = exported.content.decode("utf-8-sig").splitlines()[0].split(",")
    assert header[0] == "Booth"
    assert header[2] == "Ward"
    assert "2024 Booth" not in header and "2026 Ward" not in header


def test_api_responses_use_booth_and_ward_and_still_accept_the_old_json_keys(ctx):
    legacy = voter_payload(voter_id="ZZZ1000005", mobile="9000000025")
    legacy["booth_2024"] = legacy.pop("booth")
    legacy["ward_2026"] = legacy.pop("ward")

    created = ctx.client.post("/api/voters", headers=ctx.auth, json=legacy)

    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["booth"], body["ward"]) == ("12A", "3")
    assert "booth_2024" not in body and "ward_2026" not in body


# --- CSV import: old and new headers ---------------------------------------


def test_csv_import_accepts_the_new_booth_and_ward_headers(ctx):
    imported = _import(ctx, "\n".join([NEW_HEADER, ROW.format(vid="ZZZ0000501")]))

    assert imported.status_code == 200, imported.text
    assert imported.json()["imported"] == 1
    stored = ctx.client.post(
        "/api/voters/search", headers=ctx.auth, json={"voter_id": "ZZZ0000501"}
    ).json()["items"][0]
    assert (stored["booth"], stored["ward"]) == ("7", "2")


def test_csv_import_accepts_the_old_2024_booth_and_2026_ward_headers(ctx):
    assert "2024 Booth" in OLD_HEADER and "2026 Ward" in OLD_HEADER

    imported = _import(ctx, "\n".join([OLD_HEADER, ROW.format(vid="ZZZ0000502")]))

    assert imported.status_code == 200, imported.text
    assert imported.json()["imported"] == 1
    stored = ctx.client.post(
        "/api/voters/search", headers=ctx.auth, json={"voter_id": "ZZZ0000502"}
    ).json()["items"][0]
    assert (stored["booth"], stored["ward"]) == ("7", "2")


def test_csv_import_accepts_the_old_column_names_booth_2024_and_ward_2026(ctx):
    header = NEW_HEADER.replace("Booth", "booth_2024", 1).replace("Ward,", "ward_2026,", 1)

    imported = _import(ctx, "\n".join([header, ROW.format(vid="ZZZ0000503")]))

    assert imported.status_code == 200, imported.text
    assert imported.json()["imported"] == 1


def test_csv_import_rejects_old_and_new_names_for_the_same_column(ctx):
    header = NEW_HEADER + ",2024 Booth"

    rejected = _import(ctx, "\n".join([header, ROW.format(vid="ZZZ0000504") + ",x"]))

    assert rejected.status_code == 400
    assert "twice" in rejected.json()["detail"]


def test_csv_import_still_rejects_unknown_and_missing_columns(ctx):
    missing = _import(ctx, "\n".join([NEW_HEADER.replace("VoterId,", "", 1), "x"]))
    unknown = _import(ctx, "\n".join([NEW_HEADER + ",Nonsense", ROW.format(vid="ZZZ0000505") + ",x"]))

    assert missing.status_code == 400 and "missing VoterId" in missing.json()["detail"]
    assert unknown.status_code == 400 and "unexpected Nonsense" in unknown.json()["detail"]


def test_csv_import_duplicate_voter_id_is_reported_per_row(ctx):
    _create(ctx, voter_id="ZZZ0000506", mobile="9000000026")

    imported = _import(ctx, "\n".join([NEW_HEADER, ROW.format(vid="ZZZ0000506")]))

    report = imported.json()
    assert report["imported"] == 0
    assert report["errors"][0]["message"] == "VoterId ZZZ0000506 already exists"


# --- Migration -------------------------------------------------------------


def test_migration_renames_the_columns_and_keeps_every_value(tmp_path):
    db_file = tmp_path / "old.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", "sqlite:///" + db_file.as_posix())
    command.upgrade(cfg, "0003_token_version")
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            "INSERT INTO voters (id, booth_2024, ward_2026, voter_id, first_name, gender,"
            " created_at, updated_at, revision) VALUES"
            " ('00000000-0000-0000-0000-000000000001', '12A', '3', 'ZZZ9000001', 'Fakeperson',"
            " 'Male', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 1)"
        )

    command.upgrade(cfg, "head")

    with sqlite3.connect(db_file) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(voters)")}
        row = conn.execute("SELECT booth, ward, voter_id FROM voters").fetchone()
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(voters)")}
    assert {"booth", "ward"} <= columns
    assert not ({"booth_2024", "ward_2026"} & columns)
    assert row == ("12A", "3", "ZZZ9000001")
    assert "uq_voters_voter_id_active" not in indexes  # voter_id is the primary key now
    assert {"ix_voters_booth", "ix_voters_ward"} <= indexes

    command.downgrade(cfg, "0003_token_version")
    with sqlite3.connect(db_file) as conn:
        assert conn.execute("SELECT booth_2024, ward_2026 FROM voters").fetchone() == ("12A", "3")
