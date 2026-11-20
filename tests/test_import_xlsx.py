"""Excel (.xlsx) import next to CSV import. Synthetic data only."""

from __future__ import annotations

import csv
import io
from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from app.constants import CSV_HEADERS, IMPORT_BYTE_LIMIT
from tests.conftest import csrf_from_page, make_user

SAMPLE_CSV = Path("sample/voters_sample.csv")
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
HEADER = list(CSV_HEADERS)
FIELD_USER = ("field.user", "field-pass-123")


def _row(voter_id: str, **overrides) -> list:
    base = {
        "Booth": 1, "Booth S.No": 10, "Ward": 2, "Ward S.No": 3, "VoterId": voter_id,
        "FirstName": "Testperson", "LastName": "One", "RelativeName": "Relative One",
        "HouseNo": "H-1", "Gender": "Female", "Age": 42, "Street": "Sample Street A",
        "Mobile": 9000000001, "Caste": "SampleGroupA", "Village": "Sampleville A",
        "Panchayath": "Samplepanchayath", "Mandal": "Samplemandal", "Current City": "Nathavaram",
        "Local/Non-Local": "Local", "Party": "TDP", "Constituency": "Sampleconstituency",
    }
    base.update(overrides)
    return [base[name] for name in HEADER]


def _xlsx(rows: list[list], header: list | None = None) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.append(HEADER if header is None else header)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _csv(rows: list[list], header: list | None = None) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(HEADER if header is None else header)
    for row in rows:
        writer.writerow(["" if cell is None else cell for cell in row])
    return buffer.getvalue().encode("utf-8")


def _api_xlsx(ctx, content: bytes, name: str = "voters.xlsx", headers=None):
    return ctx.client.post(
        "/api/voters/import.xlsx",
        headers=headers or ctx.auth,
        files={"file": (name, content, XLSX_MIME)},
    )


def _api_csv(ctx, content: bytes):
    return ctx.client.post(
        "/api/voters/import.csv",
        headers=ctx.auth,
        files={"file": ("voters.csv", content, "text/csv")},
    )


def _voter(ctx, voter_id: str) -> dict:
    got = ctx.client.get(f"/api/voters/{voter_id}", headers=ctx.auth)
    assert got.status_code == 200, got.text
    return got.json()


def _web_login(ctx, username: str = "admin", password: str = "admin-pass-123") -> None:
    ctx.client.cookies.clear()
    signed = ctx.client.post(
        "/login", data={"username": username, "password": password}, follow_redirects=False
    )
    assert signed.status_code == 303, signed.text


def _web_upload(ctx, name: str, content: bytes, mime: str = XLSX_MIME):
    token = csrf_from_page(ctx.client.get("/import").text)
    return ctx.client.post(
        "/import", data={"csrf_token": token}, files={"file": (name, content, mime)}
    )


# --- Parity with CSV -------------------------------------------------------------


def test_xlsx_import_matches_csv_import_results(ctx):
    csv_rows = [
        _row("ZZZ3000001", Mobile="9000000001"),
        _row("ZZZ3000002", Gender="Other"),
        _row("ZZZ3000003", Age="not-a-number"),
        _row("ZZZ3000004", Gender="Robot"),
    ]
    xlsx_rows = [[cell for cell in row] for row in csv_rows]
    for row in xlsx_rows:
        row[4] = row[4].replace("ZZZ3", "ZZZ4")

    from_csv = _api_csv(ctx, _csv(csv_rows))
    from_xlsx = _api_xlsx(ctx, _xlsx(xlsx_rows))

    assert from_csv.status_code == 200, from_csv.text
    assert from_xlsx.status_code == 200, from_xlsx.text
    a, b = from_csv.json(), from_xlsx.json()
    assert (a["rows_read"], a["imported"], a["skipped"]) == (4, 2, 2)
    assert (b["rows_read"], b["imported"], b["skipped"]) == (4, 2, 2)
    assert [(e["row"], e["field"], e["message"]) for e in a["errors"]] == [
        (e["row"], e["field"], e["message"]) for e in b["errors"]
    ]
    for old, new in (("ZZZ3000001", "ZZZ4000001"), ("ZZZ3000002", "ZZZ4000002")):
        left, right = _voter(ctx, old), _voter(ctx, new)
        for key in left:
            if key not in {"voter_id", "created_at", "updated_at", "revision", "source_id"}:
                assert left[key] == right[key], key


def test_numeric_cells_are_stored_without_trailing_point_zero(ctx):
    row = _row(
        "ZZZ4100001", Booth=1.0, **{"Booth S.No": 10.0, "Ward S.No": 3.0}, Ward=2.0, Age=42.0,
        Mobile=9000000001.0,
    )
    report = _api_xlsx(ctx, _xlsx([row])).json()
    assert report["imported"] == 1, report
    stored = _voter(ctx, "ZZZ4100001")
    assert stored["gender"] == "Female"
    assert stored["age"] == 42
    for key in ("booth", "booth_s_no", "ward", "ward_s_no", "mobile"):
        assert not str(stored[key]).endswith(".0"), (key, stored[key])
    assert stored["mobile"] == "9000000001"
    assert stored["booth"] == "1"


def test_integer_cells_and_blank_cells(ctx):
    row = _row("ZZZ4100002", Gender=None, Party=None, Constituency=None, Street=None)
    assert _api_xlsx(ctx, _xlsx([row])).json()["imported"] == 1
    stored = _voter(ctx, "ZZZ4100002")
    assert stored["mobile"] == "9000000001"
    assert not stored["gender"]
    assert not stored["party"]


def test_fully_empty_rows_are_skipped_and_not_counted(ctx):
    rows = [_row("ZZZ4200001"), [None] * len(HEADER), _row("ZZZ4200002")]
    report = _api_xlsx(ctx, _xlsx(rows)).json()
    assert (report["rows_read"], report["imported"], report["skipped"]) == (2, 2, 0)


def test_error_row_numbers_are_sheet_rows(ctx):
    rows = [_row("ZZZ4300001"), [None] * len(HEADER), _row("ZZZ4300002", Gender="Robot")]
    report = _api_xlsx(ctx, _xlsx(rows)).json()
    assert report["skipped"] == 1
    assert report["errors"][0]["row"] == 4
    assert report["errors"][0]["field"] == "Gender"


def test_date_cell_does_not_crash_and_is_a_row_error(ctx):
    rows = [_row("ZZZ4400001", Age=datetime(2024, 1, 2)), _row("ZZZ4400002")]
    response = _api_xlsx(ctx, _xlsx(rows))
    assert response.status_code == 200, response.text
    report = response.json()
    assert (report["imported"], report["skipped"]) == (1, 1)
    assert report["errors"][0]["field"] == "Age"


def test_only_the_first_sheet_is_read(ctx):
    book = Workbook()
    first = book.active
    first.append(HEADER)
    first.append(_row("ZZZ4500001"))
    second = book.create_sheet("Other")
    second.append(HEADER)
    second.append(_row("ZZZ4500002"))
    buffer = io.BytesIO()
    book.save(buffer)
    report = _api_xlsx(ctx, buffer.getvalue()).json()
    assert report["imported"] == 1
    assert ctx.client.get("/api/voters/ZZZ4500002", headers=ctx.auth).status_code == 404


def test_duplicate_voter_id_is_skipped_and_reported_like_csv(ctx):
    assert _api_xlsx(ctx, _xlsx([_row("ZZZ4600001")])).json()["imported"] == 1
    again = _api_xlsx(ctx, _xlsx([_row("ZZZ4600001", FirstName="Changed"), _row("ZZZ4600002")]))
    report = again.json()
    assert (report["imported"], report["skipped"]) == (1, 1)
    assert report["errors"][0]["field"] == "VoterId"
    assert report["errors"][0]["message"] == "VoterId ZZZ4600001 already exists"
    assert _voter(ctx, "ZZZ4600001")["first_name"] == "Testperson"


def test_duplicate_voter_id_inside_one_xlsx_matches_csv_message(ctx):
    report = _api_xlsx(ctx, _xlsx([_row("ZZZ4700001"), _row("ZZZ4700001")])).json()
    assert (report["imported"], report["skipped"]) == (1, 1)
    assert report["errors"][0]["message"] == "Duplicate VoterId ZZZ4700001 earlier in this file"


def test_legacy_headers_and_dropped_columns_work_on_xlsx(ctx):
    header = [
        "Booth", "2024 B.S.No", "Ward", "Ward S No", "VoterId", "FirstName", "SurName",
        "RelativeName", "Address", "Village", "Permanent address", "Local", "Gender", "Age",
        "Caste", "Mobile", "C_City", "Profession", "Party",
    ]
    row = [
        1, 10, 1, 1, "ZZZ4800001", "Fake", "Row", "Rel", "H-1", "Sampleville", "Old place",
        "Local", "Male", 40, "SampleGroupA", 9000000092, "Nathavaram", "Farmer", None,
    ]
    report = _api_xlsx(ctx, _xlsx([row], header=header)).json()
    assert (report["imported"], report["skipped"]) == (1, 0), report
    stored = _voter(ctx, "ZZZ4800001")
    assert stored["last_name"] == "Row"
    assert stored["gender"] == "Male"
    assert stored["current_city"] == "Nathavaram"
    assert "profession" not in stored and "permanent_address" not in stored


def test_file_without_gender_column_leaves_gender_blank(ctx):
    report = _api_xlsx(ctx, _xlsx([["ZZZ4900001", "Only"]], header=["VoterId", "FirstName"])).json()
    assert report["imported"] == 1, report
    assert not _voter(ctx, "ZZZ4900001")["gender"]


def test_invalid_gender_is_a_row_error_on_xlsx(ctx):
    report = _api_xlsx(
        ctx,
        _xlsx(
            [["ZZZ5000001", "Good", "Male"], ["ZZZ5000002", "Bad", "Robot"]],
            header=["VoterId", "FirstName", "Gender"],
        ),
    ).json()
    assert (report["imported"], report["skipped"]) == (1, 1)
    assert report["errors"][0]["field"] == "Gender"
    assert report["errors"][0]["row"] == 3


def test_header_rules_match_csv_with_excel_wording(ctx):
    missing = _api_xlsx(ctx, _xlsx([["Only"]], header=["FirstName"]))
    assert missing.status_code == 400
    assert missing.json()["detail"].startswith("Excel file columns must match the voter list")
    dup = _api_xlsx(ctx, _xlsx([["a", "b"]], header=["VoterId", "VoterId"]))
    assert dup.status_code == 400
    assert "Excel file header contains duplicate column names" in dup.json()["detail"]
    blank = _api_xlsx(ctx, _xlsx([["a", "b", "c"]], header=["VoterId", None, "FirstName"]))
    assert blank.status_code == 400
    assert "empty column name" in blank.json()["detail"]
    empty = _api_xlsx(ctx, _xlsx([], header=[]))
    assert empty.status_code == 400
    assert "Excel file is empty" in empty.json()["detail"]


def test_row_limit_applies_to_xlsx(ctx, monkeypatch):
    monkeypatch.setattr("app.services.impex.IMPORT_ROW_LIMIT", 2)
    rows = [_row("ZZZ5100001"), _row("ZZZ5100002"), _row("ZZZ5100003")]
    response = _api_xlsx(ctx, _xlsx(rows))
    assert response.status_code == 400
    assert "exceeds 2 data rows" in response.json()["detail"]


def test_csv_endpoint_still_reports_csv_wording(ctx):
    response = ctx.client.post(
        "/api/voters/import.csv",
        headers=ctx.auth,
        files={"file": ("bad.csv", b"FirstName\nOnly\n", "text/csv")},
    )
    assert response.status_code == 400
    assert response.json()["detail"].startswith("CSV columns must match the voter list")


# --- Rejections ------------------------------------------------------------------


@pytest.mark.parametrize("name", ["voters.xls", "voters.ods", "voters.txt", "voters", "voters.bin"])
def test_other_file_types_are_rejected_on_the_xlsx_api(ctx, name):
    response = _api_xlsx(ctx, b"anything", name=name)
    assert response.status_code == 400
    assert "Only .csv and .xlsx files are accepted" in response.json()["detail"]


def test_csv_upload_to_the_xlsx_endpoint_is_rejected(ctx):
    response = _api_xlsx(ctx, _csv([_row("ZZZ5200001")]), name="voters.csv")
    assert response.status_code == 400
    assert "import.csv" in response.json()["detail"]


def test_password_protected_workbook_is_rejected_clearly(ctx):
    locked = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512
    response = _api_xlsx(ctx, locked)
    assert response.status_code == 400
    assert "password-protected" in response.json()["detail"]
    assert "Traceback" not in response.text


@pytest.mark.parametrize(
    "content", [b"not a workbook at all", b"PK\x03\x04 truncated zip", b"PK\x05\x06" + b"\x00" * 18]
)
def test_corrupt_workbook_is_rejected_clearly(ctx, content):
    response = _api_xlsx(ctx, content)
    assert response.status_code == 400
    assert "could not be read" in response.json()["detail"]
    assert "Traceback" not in response.text


def test_oversized_xlsx_gets_413_on_the_api(ctx):
    response = _api_xlsx(ctx, b"PK" + b"0" * IMPORT_BYTE_LIMIT)
    assert response.status_code == 413
    assert "5 MB" in response.json()["detail"]
    assert "Excel" in response.json()["detail"]


def test_oversized_csv_keeps_its_message(ctx):
    response = ctx.client.post(
        "/api/voters/import.csv",
        headers=ctx.auth,
        files={"file": ("big.csv", b"0" * (IMPORT_BYTE_LIMIT + 1), "text/csv")},
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "CSV larger than 5 MB"


# --- Web form --------------------------------------------------------------------


def test_web_form_imports_xlsx_with_the_same_report(ctx):
    _web_login(ctx)
    ok = _web_upload(ctx, "voters.xlsx", _xlsx([_row("ZZZ5300001"), _row("ZZZ5300002", Gender="Robot")]))
    assert ok.status_code == 200, ok.text
    assert "1 imported, 1 skipped, 2 rows read." in ok.text
    assert "Validation report" in ok.text
    assert _voter(ctx, "ZZZ5300001")["gender"] == "Female"


def test_web_form_still_imports_csv(ctx):
    _web_login(ctx)
    ok = _web_upload(ctx, "voters.csv", _csv([_row("ZZZ5400001")]), mime="text/csv")
    assert ok.status_code == 200, ok.text
    assert "1 imported, 0 skipped, 1 rows read." in ok.text


def test_web_form_detects_xlsx_by_signature_when_named_csv(ctx):
    _web_login(ctx)
    ok = _web_upload(ctx, "voters.csv", _xlsx([_row("ZZZ5500001")]), mime="text/csv")
    assert ok.status_code == 200, ok.text
    assert "1 imported" in ok.text


@pytest.mark.parametrize("name", ["voters.xls", "voters.ods", "voters.txt"])
def test_web_form_rejects_other_types(ctx, name):
    _web_login(ctx)
    response = _web_upload(ctx, name, b"anything", mime="application/octet-stream")
    assert response.status_code == 400
    assert "Only .csv and .xlsx files are accepted" in response.text


def test_web_form_rejects_corrupt_and_locked_workbooks(ctx):
    _web_login(ctx)
    corrupt = _web_upload(ctx, "bad.xlsx", b"PK\x03\x04 broken")
    assert corrupt.status_code == 400
    assert "could not be read" in corrupt.text
    locked = _web_upload(ctx, "locked.xlsx", b"\xd0\xcf\x11\xe0" + b"\x00" * 64)
    assert locked.status_code == 400
    assert "password-protected" in locked.text


def test_web_form_oversized_upload_message_covers_excel(ctx):
    _web_login(ctx)
    big = _web_upload(ctx, "big.xlsx", b"PK" + b"0" * IMPORT_BYTE_LIMIT)
    assert big.status_code == 413
    assert "CSV and Excel" in big.text
    big_csv = _web_upload(ctx, "big.csv", b"0" * (IMPORT_BYTE_LIMIT + 1), mime="text/csv")
    assert big_csv.status_code == 413
    assert "CSV larger than 5 MB" in big_csv.text


# --- Admin gating ----------------------------------------------------------------


def test_regular_user_gets_403_for_xlsx_import_and_sample(ctx):
    headers = make_user(ctx, *FIELD_USER)
    assert _api_xlsx(ctx, _xlsx([_row("ZZZ5600001")]), headers=headers).status_code == 403
    _web_login(ctx, *FIELD_USER)
    token = csrf_from_page(ctx.client.get("/voters").text)
    assert ctx.client.get("/import").status_code == 403
    assert ctx.client.get("/sample.xlsx").status_code == 403
    assert ctx.client.get("/sample.csv").status_code == 403
    blocked = ctx.client.post(
        "/import",
        data={"csrf_token": token},
        files={"file": ("voters.xlsx", _xlsx([_row("ZZZ5600002")]), XLSX_MIME)},
    )
    assert blocked.status_code == 403
    assert ctx.client.get("/api/voters/ZZZ5600002", headers=headers).status_code == 404


def test_anonymous_cannot_use_the_xlsx_import(ctx):
    ctx.client.cookies.clear()
    response = ctx.client.post(
        "/api/voters/import.xlsx", files={"file": ("v.xlsx", _xlsx([]), XLSX_MIME)}
    )
    assert response.status_code in (401, 403)


# --- Sample workbook -------------------------------------------------------------


def test_sample_xlsx_has_expected_header_and_only_fake_data(ctx):
    _web_login(ctx)
    response = ctx.client.get("/sample.xlsx")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(XLSX_MIME)
    assert "voters_sample.xlsx" in response.headers["content-disposition"]
    rows = list(load_workbook(io.BytesIO(response.content)).active.iter_rows(values_only=True))
    assert list(rows[0]) == HEADER
    assert len(HEADER) == 21 and HEADER[9] == "Gender"
    with SAMPLE_CSV.open(encoding="utf-8-sig", newline="") as handle:
        expected = list(csv.reader(handle))
    assert [["" if c is None else str(c) for c in row] for row in rows] == expected
    for row in rows[1:]:
        record = dict(zip(HEADER, row))
        assert str(record["VoterId"]).startswith("ZZZ")
        mobile = "" if record["Mobile"] is None else str(record["Mobile"])
        # The sample deliberately mixes formats (+91, leading 0) and one blank.
        assert mobile == "" or mobile.startswith(("90000000", "+9190000000", "090000000"))


def test_sample_xlsx_imports_cleanly(ctx):
    _web_login(ctx)
    content = ctx.client.get("/sample.xlsx").content
    report = _api_xlsx(ctx, content, name="voters_sample.xlsx").json()
    assert (report["imported"], report["skipped"]) == (9, 0), report


# --- Page copy and sidebar -------------------------------------------------------


def test_import_page_copy_and_sidebar(ctx):
    _web_login(ctx)
    page = ctx.client.get("/import")
    assert page.status_code == 200
    assert "<title>Import CSV / Excel" in page.text
    assert "<h1>Import CSV / Excel</h1>" in page.text
    assert 'accept=".csv,.xlsx,' in page.text
    assert XLSX_MIME in page.text
    assert "text/csv" in page.text
    assert "CSV or Excel file" in page.text
    assert 'href="/sample.csv"' in page.text
    assert 'href="/sample.xlsx"' in page.text
    assert "removed Gender" not in page.text
    assert "Gender is optional" in page.text
    assert "Permanent address and Profession columns are ignored" in page.text
    assert "Import CSV / Excel" in ctx.client.get("/voters").text
