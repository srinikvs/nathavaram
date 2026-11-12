"""CSV import, CSV/Excel export, and report counts."""

from __future__ import annotations

import csv
import io
from pathlib import Path

from openpyxl import load_workbook

from app.constants import CSV_HEADERS
from tests.conftest import csrf_from_page as csrf_value
from tests.conftest import make_user, search, voter_payload

SAMPLE = Path("sample/voters_sample.csv")


def test_sample_header_and_import(ctx):
    header = SAMPLE.read_text(encoding="utf-8").splitlines()[0].lstrip("\ufeff")
    assert header.split(",") == list(CSV_HEADERS)
    rows = SAMPLE.read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 10
    assert rows[1].endswith(",")

    imported = ctx.client.post(
        "/api/voters/import.csv",
        headers=ctx.auth,
        files={"file": ("voters_sample.csv", SAMPLE.read_bytes(), "text/csv")},
    )
    assert imported.status_code == 200, imported.text
    report = imported.json()
    assert report["imported"] == 9
    assert report["skipped"] == 0
    telugu = search(ctx, q="నమూనా")
    assert telugu.json()["total"] == 1
    assert telugu.json()["items"][0]["party"] is None
    assert telugu.json()["items"][0]["c_city"] == "నాతవరం"

    again = ctx.client.post(
        "/api/voters/import.csv",
        headers=ctx.auth,
        files={"file": ("voters_sample.csv", SAMPLE.read_bytes(), "text/csv")},
    )
    assert again.json()["imported"] == 0
    assert again.json()["skipped"] == 9


def test_import_reports_row_errors_and_keeps_valid_rows(ctx):
    header = ",".join(CSV_HEADERS)
    body = "\n".join(
        [
            header,
            "1,10,1,1,ZZZ0000100,Good,Row,Rel,H-1,Male,40,SampleGroupA,9000000099,Nathavaram,Farmer,",
            "1,10,1,1,BAD ID,Nope,Row,Rel,H-1,Male,40,SampleGroupA,9000000098,Nathavaram,Farmer,",
            "1,10,1,1,ZZZ0000101,Also,Bad,Rel,H-1,Male,nope,SampleGroupA,123,Nathavaram,Farmer,NotAParty",
        ]
    )
    imported = ctx.client.post(
        "/api/voters/import.csv",
        headers=ctx.auth,
        files={"file": ("mixed.csv", body.encode("utf-8"), "text/csv")},
    )
    assert imported.status_code == 200, imported.text
    report = imported.json()
    assert report["imported"] == 1
    assert report["skipped"] == 2
    assert {item["row"] for item in report["errors"]} == {3, 4}

    missing = ctx.client.post(
        "/api/voters/import.csv",
        headers=ctx.auth,
        files={"file": ("bad.csv", b"VoterId,FirstName\nZZZ1,Only\n", "text/csv")},
    )
    assert missing.status_code == 400


def test_csv_and_excel_round_trip_telugu(ctx):
    ctx.client.post(
        "/api/voters",
        headers=ctx.auth,
        json=voter_payload(first_name="=నమూనా", voter_id="ZZZ1000088"),
    )
    csv_response = ctx.client.post("/api/voters/export.csv", headers=ctx.auth, json={"booth": "12A"})
    assert csv_response.status_code == 200
    assert csv_response.content.startswith(b"\xef\xbb\xbf")
    text = csv_response.content.decode("utf-8-sig")
    assert text.splitlines()[0] == ",".join(CSV_HEADERS)
    assert "'=నమూనా" in text

    workbook = load_workbook(io.BytesIO(ctx.client.post("/api/voters/export.xlsx", headers=ctx.auth).content))
    sheet = workbook["Voters"]
    assert sheet["A1"].value == "Booth"
    assert sheet["E1"].value == "VoterId"


def test_export_neutralises_formulas_and_keeps_plus91_mobile_round_trip(ctx):
    created = ctx.client.post(
        "/api/voters",
        headers=ctx.auth,
        json=voter_payload(
            voter_id="ZZZ1000201",
            first_name="=SUM(1+1)",
            relative_name="@Relative",
            profession="-Teacher",
            mobile="+91 90000 00022",
        ),
    )
    assert created.status_code == 201, created.text
    voter = created.json()

    exported = ctx.client.post("/api/voters/export.csv", headers=ctx.auth)
    assert exported.status_code == 200
    text = exported.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    cells = dict(zip(rows[0], rows[1]))
    assert cells["FirstName"] == "'=SUM(1+1)"
    assert cells["RelativeName"] == "'@Relative"
    assert cells["Profession"] == "'-Teacher"
    assert cells["Mobile"] == "'+91 90000 00022"
    assert not any(cell.startswith(("=", "+", "-", "@", "\t", "\r")) for cell in rows[1] if cell)

    assert ctx.client.delete(f"/api/voters/{voter['id']}", headers=ctx.auth).status_code == 200
    again = ctx.client.post(
        "/api/voters/import.csv",
        headers=ctx.auth,
        files={"file": ("round-trip.csv", exported.content, "text/csv")},
    )
    assert again.status_code == 200, again.text
    assert again.json()["imported"] == 1
    assert again.json()["errors"] == []
    restored = search(ctx, voter_id="ZZZ1000201").json()["items"][0]
    assert restored["mobile"] == "+91 90000 00022"
    assert restored["first_name"] == "=SUM(1+1)"


def test_xlsx_export_does_not_store_formula_cells(ctx):
    ctx.client.post(
        "/api/voters",
        headers=ctx.auth,
        json=voter_payload(voter_id="ZZZ1000202", first_name="=1+1"),
    )
    book = load_workbook(io.BytesIO(ctx.client.post("/api/voters/export.xlsx", headers=ctx.auth).content))
    cell = book["Voters"]["F2"]
    assert cell.data_type != "f"
    assert cell.value == "'=1+1"


def test_report_csv_labels_are_formula_safe(ctx):
    ctx.client.post(
        "/api/voters",
        headers=ctx.auth,
        json=voter_payload(voter_id="ZZZ1000203", caste="=HYPERLINK(1)", profession="+cmd"),
    )
    exported = ctx.client.get("/api/reports/export.csv", headers=ctx.auth)
    rows = list(csv.reader(io.StringIO(exported.content.decode("utf-8-sig"))))
    labels = {row[1] for row in rows}
    assert "'=HYPERLINK(1)" in labels
    assert "'+cmd" in labels
    assert not any(row[1].startswith(("=", "+", "-", "@")) for row in rows)


def test_plain_leading_quote_text_is_not_altered_on_import(ctx):
    header = ",".join(CSV_HEADERS)
    body = "\n".join(
        [header, "1,10,1,1,ZZZ0000300,'Quoted,Row,Rel,H-1,Male,40,SampleGroupA,9000000091,Nathavaram,Farmer,"]
    )
    ctx.client.post(
        "/api/voters/import.csv",
        headers=ctx.auth,
        files={"file": ("q.csv", body.encode("utf-8"), "text/csv")},
    )
    assert search(ctx, voter_id="ZZZ0000300").json()["items"][0]["first_name"] == "'Quoted"


def test_regular_user_cannot_export_import_or_delete_but_can_search_and_edit(ctx):
    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload()).json()
    user = make_user(ctx)

    assert ctx.client.post("/api/voters/export.csv", headers=user).status_code == 403
    assert ctx.client.post("/api/voters/export.xlsx", headers=user).status_code == 403
    assert ctx.client.post("/api/voters/export.csv", headers=user, json={"q": "test"}).status_code == 403
    denied_import = ctx.client.post(
        "/api/voters/import.csv",
        headers=user,
        files={"file": ("voters_sample.csv", SAMPLE.read_bytes(), "text/csv")},
    )
    assert denied_import.status_code == 403
    assert ctx.client.delete(f"/api/voters/{created['id']}", headers=user).status_code == 403
    assert ctx.client.get("/api/users", headers=user).status_code == 403
    assert (
        ctx.client.post(
            "/api/users",
            headers=user,
            json={"username": "other.user", "password": "other-pass-123", "role": "user"},
        ).status_code
        == 403
    )

    found = ctx.client.post("/api/voters/search", headers=user, json={"q": "testperson"})
    assert found.status_code == 200
    assert found.json()["total"] == 1
    assert ctx.client.get(f"/api/voters/{created['id']}", headers=user).status_code == 200
    edited = ctx.client.patch(f"/api/voters/{created['id']}", headers=user, json={"profession": "Farmer"})
    assert edited.status_code == 200
    assert edited.json()["profession"] == "Farmer"
    # Nothing the user attempted removed or imported rows.
    assert search(ctx).json()["total"] == 1


def test_admin_can_export_import_delete_and_manage_users(ctx):
    imported = ctx.client.post(
        "/api/voters/import.csv",
        headers=ctx.auth,
        files={"file": ("voters_sample.csv", SAMPLE.read_bytes(), "text/csv")},
    )
    assert imported.status_code == 200
    assert ctx.client.post("/api/voters/export.csv", headers=ctx.auth).status_code == 200
    assert ctx.client.post("/api/voters/export.xlsx", headers=ctx.auth).status_code == 200
    first = search(ctx).json()["items"][0]
    assert ctx.client.delete(f"/api/voters/{first['id']}", headers=ctx.auth).status_code == 200
    assert ctx.client.get("/api/users", headers=ctx.auth).status_code == 200


def test_web_bulk_pages_are_admin_only(ctx):
    make_user(ctx)
    ctx.client.cookies.clear()
    signed = ctx.client.post(
        "/login",
        data={"username": "field.user", "password": "field-pass-123"},
        follow_redirects=False,
    )
    assert signed.status_code == 303
    page = ctx.client.get("/voters")
    assert page.status_code == 200
    assert "Export CSV" not in page.text
    assert 'href="/import"' not in page.text
    token = csrf_value(page.text)
    assert ctx.client.get("/import").status_code == 403
    assert ctx.client.get("/sample.csv").status_code == 403
    assert ctx.client.post("/import", data={"csrf_token": token}).status_code == 403
    assert ctx.client.post("/voters/export.csv", data={"csrf_token": token}).status_code == 403
    assert ctx.client.post("/voters/export.xlsx", data={"csrf_token": token}).status_code == 403
    assert ctx.client.get("/users").status_code == 403


def test_web_admin_can_export_and_import(ctx):
    ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload())
    page = ctx.client.get("/voters")
    assert page.status_code == 200
    assert "Export CSV" in page.text
    token = csrf_value(page.text)
    exported = ctx.client.post("/voters/export.csv", data={"csrf_token": token, "q": "testperson"})
    assert exported.status_code == 200
    assert "ZZZ1000001" in exported.content.decode("utf-8-sig")
    sheet = ctx.client.post("/voters/export.xlsx", data={"csrf_token": token})
    assert sheet.status_code == 200
    imported = ctx.client.post(
        "/import",
        data={"csrf_token": token},
        files={"file": ("voters_sample.csv", SAMPLE.read_bytes(), "text/csv")},
    )
    assert imported.status_code == 200
    assert "9 imported" in imported.text


def test_reports_cover_the_required_breakdowns(ctx):
    sample = SAMPLE.read_bytes()
    imported = ctx.client.post(
        "/api/voters/import.csv",
        headers=ctx.auth,
        files={"file": ("voters_sample.csv", sample, "text/csv")},
    )
    assert imported.status_code == 200
    summary = ctx.client.get("/api/reports/summary", headers=ctx.auth)
    assert summary.status_code == 200
    body = summary.json()
    assert body["total"] == 9
    assert sum(row["count"] for row in body["by_booth"]) == 9
    assert sum(row["count"] for row in body["by_gender"]) == 9
    assert sum(row["count"] for row in body["by_age_band"]) == 9
    booths = {row["label"]: row["count"] for row in body["by_booth"]}
    assert booths["1"] == 2
    parties = {row["label"]: row["count"] for row in body["by_party"]}
    assert parties["(blank)"] == 2
    assert parties["TDP"] == 2
    assert parties["YSRCP"] == 1
    assert parties["Jana Sena"] == 1
    assert parties["BJP"] == 1
    assert parties["INC"] == 1
    assert parties["Independent"] == 1
    bands = {row["label"]: row["count"] for row in body["by_age_band"]}
    assert bands["Unknown"] == 1
    assert bands["66+"] == 2
    exported = ctx.client.get("/api/reports/export.csv", headers=ctx.auth)
    assert exported.status_code == 200
    exported_text = exported.content.decode("utf-8-sig")
    assert exported_text.splitlines()[0].startswith("Booth,2024 B.S.No,Ward,")
    assert "2024 Booth" not in exported_text
    assert "Age band" in exported.content.decode("utf-8-sig")
