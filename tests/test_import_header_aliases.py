"""Import header aliases and normalization, for CSV and xlsx. Synthetic data only."""

from __future__ import annotations

import pytest

from app.constants import (
    DROPPED_IMPORT_HEADERS,
    IMPORT_HEADER_FIELDS,
    NORMALIZED_HEADER_FIELDS,
    normalize_header,
)
from tests.test_import_xlsx import _api_csv, _api_xlsx, _csv, _voter, _xlsx

FORMATS = ("csv", "xlsx")


def _upload(ctx, fmt: str, header: list, row: list):
    if fmt == "csv":
        return _api_csv(ctx, _csv([row], header=header))
    return _api_xlsx(ctx, _xlsx([row], header=header))


def _import_one(ctx, fmt: str, voter_id: str, extra_header: str, value, field: str):
    header = ["VoterId", "FirstName", extra_header]
    response = _upload(ctx, fmt, header, [voter_id, "Fakename", value])
    assert response.status_code == 200, response.text
    report = response.json()
    assert (report["imported"], report["skipped"]) == (1, 0), report
    return _voter(ctx, voter_id)[field]


VERA_MAPPINGS = [
    ("2024 Booth", "booth", "7"),
    ("2024 B.S.No", "booth_s_no", "11"),
    ("2026 Ward", "ward", "5"),
    ("Ward S No", "ward_s_no", "12"),
    ("SurName", "last_name", "Samplesurname"),
    ("C_City", "current_city", "Samplecity"),
    ("B.S.No", "booth_s_no", "13"),
]


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize(("header", "field", "value"), VERA_MAPPINGS)
def test_alias_header_lands_on_the_right_field(ctx, fmt, header, field, value):
    voter_id = f"ZZZ{abs(hash((fmt, header))) % 10**7:07d}"

    stored = _import_one(ctx, fmt, voter_id, header, value, field)

    assert str(stored) == value


NORMALIZED_CASES = [
    ("B.S.No.", "booth_s_no", "21"),
    ("b.s.no", "booth_s_no", "22"),
    ("  B.S.No  ", "booth_s_no", "23"),
    (" SurName ", "last_name", "Spacedsurname"),
    ("SURNAME", "last_name", "Uppersurname"),
    ("Ward   S   No.", "ward_s_no", "24"),
    ("c_city", "current_city", "Lowercity"),
    ("2024  booth", "booth", "8"),
    ("B S No", "booth_s_no", "25"),
]


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize(("header", "field", "value"), NORMALIZED_CASES)
def test_headers_are_normalized_on_lookup(ctx, fmt, header, field, value):
    voter_id = f"ZZZ{abs(hash((fmt, header, 'n'))) % 10**7:07d}"

    stored = _import_one(ctx, fmt, voter_id, header, value, field)

    assert str(stored) == value


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize(
    "pair",
    [("B.S.No", "Booth S.No"), (" SurName ", "LastName"), ("B.S.No.", "2024 B.S.No")],
)
def test_mixed_old_and_new_names_are_a_duplicate_column(ctx, fmt, pair):
    header = ["VoterId", "FirstName", *pair]

    response = _upload(ctx, fmt, header, ["ZZZ9000001", "Fakename", "1", "2"])

    assert response.status_code == 400
    assert "same column twice" in response.json()["detail"]


@pytest.mark.parametrize("fmt", FORMATS)
def test_unknown_header_is_still_rejected(ctx, fmt):
    response = _upload(ctx, fmt, ["VoterId", "FirstName", "Nickname."], ["ZZZ9000002", "Fake", "x"])

    assert response.status_code == 400
    assert "unexpected Nickname." in response.json()["detail"]


@pytest.mark.parametrize("fmt", FORMATS)
def test_dropped_headers_are_ignored_after_normalization(ctx, fmt):
    header = ["VoterId", "FirstName", " PROFESSION. ", "permanent  ADDRESS"]

    response = _upload(ctx, fmt, header, ["ZZZ9000003", "Fake", "Farmer", "Old place"])

    assert response.status_code == 200, response.text
    assert response.json()["imported"] == 1


def test_normalized_lookup_has_no_cross_field_collisions():
    seen: dict[str, str] = {}
    for header, field in IMPORT_HEADER_FIELDS.items():
        assert seen.setdefault(normalize_header(header), field) == field
    assert seen == NORMALIZED_HEADER_FIELDS
    dropped = {normalize_header(name) for name in DROPPED_IMPORT_HEADERS}
    assert not dropped & seen.keys()
