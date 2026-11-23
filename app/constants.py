"""Column contract shared by the database, API, CSV, and web form.

Display names are the exact headers used in the UI and in CSV import/export.
Internal names are snake_case.
"""

from __future__ import annotations

import re

# (internal name, exact display name) in the order clerks see on paper.
# voter_id is the primary key of the voters table.
VOTER_COLUMNS: tuple[tuple[str, str], ...] = (
    ("booth", "Booth"),
    ("booth_s_no", "Booth S.No"),
    ("ward", "Ward"),
    ("ward_s_no", "Ward S.No"),
    ("voter_id", "VoterId"),
    ("first_name", "FirstName"),
    ("last_name", "LastName"),
    ("relative_name", "RelativeName"),
    ("house_no", "HouseNo"),
    ("gender", "Gender"),
    ("age", "Age"),
    ("street", "Street"),
    ("mobile", "Mobile"),
    ("caste", "Caste"),
    ("village", "Village"),
    ("panchayath", "Panchayath"),
    ("mandal", "Mandal"),
    ("current_city", "Current City"),
    ("is_local", "Local/Non-Local"),
    ("party", "Party"),
    ("constituency", "Constituency"),
)

CSV_HEADERS: tuple[str, ...] = tuple(label for _, label in VOTER_COLUMNS)
FIELD_BY_LABEL: dict[str, str] = {label: name for name, label in VOTER_COLUMNS}
LABEL_BY_FIELD: dict[str, str] = {name: label for name, label in VOTER_COLUMNS}
DATA_FIELDS: tuple[str, ...] = tuple(name for name, _ in VOTER_COLUMNS)

# Extra CSV import headers: labels and column names used before the voterId-key rework.
# Permanent address and Profession no longer exist; those columns are ignored.
# Gender is a real optional column again (see VOTER_COLUMNS).
LEGACY_HEADER_ALIASES: dict[str, str] = {
    "2024 B.S.No": "booth_s_no",
    "B.S.No": "booth_s_no",
    "bs_no_2024": "booth_s_no",
    "Ward S No": "ward_s_no",
    "SurName": "last_name",
    "surname": "last_name",
    "Address": "street",
    "address": "street",
    "C_City": "current_city",
    "c_city": "current_city",
    "Local": "is_local",
    "2024 Booth": "booth",
    "2026 Ward": "ward",
    "booth_2024": "booth",
    "ward_2026": "ward",
    "House No": "house_no",
    "House no": "house_no",
}
# Headers of columns that were dropped. They are accepted and discarded so old exports import.
DROPPED_IMPORT_HEADERS: frozenset[str] = frozenset(
    {"Permanent address", "permanent_address", "Profession", "profession"}
)
# Display labels, internal column names (house_no, booth_s_no, ...) and the legacy aliases.
IMPORT_HEADER_FIELDS: dict[str, str] = {
    **{name: name for name, _ in VOTER_COLUMNS},
    **FIELD_BY_LABEL,
    **LEGACY_HEADER_ALIASES,
}

def normalize_header(name: str) -> str:
    """Lookup key for an import header: case-insensitive, whitespace-collapsed, no trailing dots.

    Dots, underscores and spaces are also treated as equivalent separators ("B.S.No" ~ "B S No"
    ~ "b_s_no"). The build below refuses any rule that would merge headers of different fields.
    """
    return re.sub(r"[\s._]+", " ", name.casefold()).strip()


def _build_header_lookup() -> tuple[dict[str, str], frozenset[str]]:
    lookup: dict[str, str] = {}
    for header, field in IMPORT_HEADER_FIELDS.items():
        key = normalize_header(header)
        if lookup.setdefault(key, field) != field:
            raise ValueError(f"import header {header!r} collides on normalized key {key!r}")
    dropped = frozenset(normalize_header(h) for h in DROPPED_IMPORT_HEADERS)
    clash = dropped & lookup.keys()
    if clash:
        raise ValueError(f"dropped import headers collide with real ones: {sorted(clash)}")
    return lookup, dropped


# Normalized header -> internal field, and normalized names of ignored columns.
NORMALIZED_HEADER_FIELDS, NORMALIZED_DROPPED_HEADERS = _build_header_lookup()

# Columns an older CSV may lack. Only VoterId and FirstName are required.
OPTIONAL_IMPORT_FIELDS: frozenset[str] = frozenset(
    {name for name, _ in VOTER_COLUMNS} - {"voter_id", "first_name"}
)

# Gender is deliberately absent: only owner/admin may set it on an existing voter (like Age).
# The only fields the regular "user" role may change on an existing voter.
# Owner/admin may change every field except voter_id, which nobody can change.
# HouseNo is included because it used to be part of Address, which users could edit.
USER_EDITABLE_FIELDS: frozenset[str] = frozenset(
    {"party", "caste", "mobile", "street", "house_no"}
)

# Exact dropdown values. Blank / null is allowed and is not one of these.
GENDERS: tuple[str, ...] = ("Male", "Female", "Other")

PARTIES: tuple[str, ...] = (
    "TDP",
    "YSRCP",
    "Jana Sena",
    "BJP",
    "INC",
    "CPI",
    "Independent",
    "Unknown",
    "Prefer not to say",
)

AGE_BANDS: tuple[str, ...] = (
    "Under 18",
    "18–25",
    "26–35",
    "36–45",
    "46–55",
    "56–65",
    "66+",
    "Unknown",
)

ROLES: tuple[str, ...] = ("admin", "user")

TEXT_LIMITS: dict[str, int] = {
    "booth": 64,
    "booth_s_no": 64,
    "ward": 64,
    "ward_s_no": 64,
    "voter_id": 32,
    "first_name": 128,
    "last_name": 128,
    "relative_name": 128,
    "house_no": 64,
    "gender": 16,
    "street": 1000,
    "caste": 128,
    "mobile": 20,
    "village": 128,
    "panchayath": 128,
    "mandal": 128,
    "current_city": 128,
    "party": 32,
    "constituency": 128,
}

# Filters that can expose personal data. They travel in a POST body, never in a URL.
SEARCH_FILTER_FIELDS: tuple[str, ...] = (
    "q",
    "voter_id",
    "village",
    "panchayath",
    "mandal",
    "is_local",
    "gender",
    "booth",
    "ward",
    "caste",
    "party",
    "mobile",
)

# Bulk edit/delete: most voter_ids one request may name, and the fields bulk edit may set.
# voter_id is the key; timestamps, revision, deleted_at and source_id are not data fields.
BULK_ID_LIMIT = 1000
BULK_EDIT_FIELDS: tuple[str, ...] = tuple(name for name, _ in VOTER_COLUMNS if name != "voter_id")

SYNC_BATCH_LIMIT = 500
IMPORT_ROW_LIMIT = 20_000
IMPORT_BYTE_LIMIT = 5 * 1024 * 1024
EXPORT_ROW_LIMIT = 50_000
