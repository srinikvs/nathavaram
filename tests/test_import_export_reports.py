"""CSV import, CSV/Excel export, and report counts."""

from __future__ import annotations

import io
from pathlib import Path

from openpyxl import load_workbook

from app.constants import CSV_HEADERS
from tests.conftest import voter_payload

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
    telugu = ctx.client.get("/api/voters", headers=ctx.auth, params={"q": "నమూనా"})
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
    csv_response = ctx.client.get("/api/voters/export.csv", headers=ctx.auth, params={"booth": "12A"})
    assert csv_response.status_code == 200
    assert csv_response.content.startswith(b"\xef\xbb\xbf")
    text = csv_response.content.decode("utf-8-sig")
    assert text.splitlines()[0] == ",".join(CSV_HEADERS)
    assert "'=నమూనా" in text

    workbook = load_workbook(io.BytesIO(ctx.client.get("/api/voters/export.xlsx", headers=ctx.auth).content))
    sheet = workbook["Voters"]
    assert sheet["A1"].value == "2024 Booth"
    assert sheet["E1"].value == "VoterId"


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
    assert parties["(blank)"] == 9
    assert parties["TDP"] == 0
    bands = {row["label"]: row["count"] for row in body["by_age_band"]}
    assert bands["Unknown"] == 1
    assert bands["66+"] == 2
    exported = ctx.client.get("/api/reports/export.csv", headers=ctx.auth)
    assert exported.status_code == 200
    assert "2024 Booth" in exported.content.decode("utf-8-sig")
    assert "Age band" in exported.content.decode("utf-8-sig")
