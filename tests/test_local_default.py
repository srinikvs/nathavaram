"""Local/Non-Local defaults to Local when nothing is said. Synthetic data only."""

from __future__ import annotations

import io
import re

import pytest
from openpyxl import Workbook

from app import database
from app.models import Voter
from tests.conftest import search, voter_payload

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
LOCAL_HEADER = "Local/Non-Local"


def _table(cells: list[str] | None, with_column: bool = True) -> list[list[str]]:
    """Header plus one row per cell value; ids are ZZZ0000001.. (fake)."""
    header = ["VoterId", "FirstName"] + ([LOCAL_HEADER] if with_column else [])
    rows = []
    for index, cell in enumerate(cells or [""], start=1):
        row = [f"ZZZ{index:07d}", f"Testperson{index}"]
        rows.append(row + ([cell] if with_column else []))
    return [header, *rows]


def _import(ctx, table: list[list[str]], kind: str):
    if kind == "csv":
        body = "\n".join(",".join(row) for row in table).encode("utf-8")
        files = {"file": ("v.csv", body, "text/csv")}
        url = "/api/voters/import.csv"
    else:
        book = Workbook()
        for row in table:
            book.active.append(row)
        buffer = io.BytesIO()
        book.save(buffer)
        files = {"file": ("v.xlsx", buffer.getvalue(), XLSX_MIME)}
        url = "/api/voters/import.xlsx"
    response = ctx.client.post(url, headers=ctx.auth, files=files)
    assert response.status_code == 200, response.text
    return response.json()


def _stored(ctx, voter_id: str) -> bool:
    return search(ctx, voter_id=voter_id).json()["items"][0]["is_local"]


@pytest.mark.parametrize("kind", ["csv", "xlsx"])
def test_import_without_local_column_stores_local(ctx, kind):
    report = _import(ctx, _table(None, with_column=False), kind)

    assert report["imported"] == 1
    assert _stored(ctx, "ZZZ0000001") is True


@pytest.mark.parametrize("kind", ["csv", "xlsx"])
def test_import_blank_local_cell_stores_local(ctx, kind):
    _import(ctx, _table([""]), kind)

    assert _stored(ctx, "ZZZ0000001") is True


@pytest.mark.parametrize("kind", ["csv", "xlsx"])
def test_import_explicit_non_local_values_stay_non_local(ctx, kind):
    cells = ["Non-Local", "No", "False", "non local"]
    _import(ctx, _table(cells), kind)

    assert [_stored(ctx, f"ZZZ{i:07d}") for i in range(1, 5)] == [False] * 4


@pytest.mark.parametrize("kind", ["csv", "xlsx"])
def test_import_explicit_local_values_stay_local(ctx, kind):
    cells = ["Local", "Yes", "True"]
    _import(ctx, _table(cells), kind)

    assert [_stored(ctx, f"ZZZ{i:07d}") for i in range(1, 4)] == [True] * 3


def test_add_voter_form_preselects_local(ctx):
    ctx.client.post("/login", data={"username": "admin", "password": "admin-pass-123", "next": "/voters"})

    page = ctx.client.get("/voters/new").text

    assert _selected_option(page) == "Local"


def test_edit_form_shows_stored_non_local(ctx):
    ctx.client.post("/login", data={"username": "admin", "password": "admin-pass-123", "next": "/voters"})
    ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload(is_local=False))

    page = ctx.client.get("/voters/ZZZ1000001/edit").text

    assert _selected_option(page) == "Non-Local"


def test_api_create_without_is_local_defaults_to_local(ctx):
    body = voter_payload()
    del body["is_local"]

    created = ctx.client.post("/api/voters", headers=ctx.auth, json=body)

    assert created.status_code in (200, 201), created.text
    assert created.json()["is_local"] is True


def test_api_create_blank_is_local_defaults_to_local(ctx):
    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload(is_local=""))

    assert created.json()["is_local"] is True


@pytest.mark.parametrize("value", [False, "Non-Local", "false"])
def test_api_create_explicit_non_local_stays_false(ctx, value):
    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload(is_local=value))

    assert created.json()["is_local"] is False


def test_existing_non_local_voter_is_not_flipped(ctx):
    db = database.SessionLocal()
    try:
        db.add(Voter(voter_id="ZZZ0000009", first_name="Keep", revision=1, created_at=_now(), updated_at=_now(),
                     is_local=False))
        db.commit()
    finally:
        db.close()

    db = database.SessionLocal()
    try:
        assert db.get(Voter, "ZZZ0000009").is_local is False
    finally:
        db.close()
    assert _stored(ctx, "ZZZ0000009") is False


def _selected_option(page: str) -> str:
    select = re.search(r'<select id="is_local".*?</select>', page, re.S).group(0)
    selected = re.findall(r'<option value="([^"]+)"\s+selected', select)
    assert len(selected) == 1, select
    return selected[0]


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC)
