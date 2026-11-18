"""Column contract shared by the database, API, CSV, and web form.

Display names are the exact headers used in the UI and in CSV import/export.
Internal names are snake_case.
"""

from __future__ import annotations

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
)

CSV_HEADERS: tuple[str, ...] = tuple(label for _, label in VOTER_COLUMNS)
FIELD_BY_LABEL: dict[str, str] = {label: name for name, label in VOTER_COLUMNS}
LABEL_BY_FIELD: dict[str, str] = {name: label for name, label in VOTER_COLUMNS}
DATA_FIELDS: tuple[str, ...] = tuple(name for name, _ in VOTER_COLUMNS)

# Extra CSV import headers: labels and column names used before the voterId-key rework.
# Gender, Permanent address and Profession no longer exist; those columns are ignored.
LEGACY_HEADER_ALIASES: dict[str, str] = {
    "2024 B.S.No": "booth_s_no",
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
    {"Gender", "gender", "Permanent address", "permanent_address", "Profession", "profession"}
)
# Display labels, internal column names (house_no, booth_s_no, ...) and the legacy aliases.
IMPORT_HEADER_FIELDS: dict[str, str] = {
    **{name: name for name, _ in VOTER_COLUMNS},
    **FIELD_BY_LABEL,
    **LEGACY_HEADER_ALIASES,
}
# Columns an older CSV may lack. Only VoterId and FirstName are required.
OPTIONAL_IMPORT_FIELDS: frozenset[str] = frozenset(
    {name for name, _ in VOTER_COLUMNS} - {"voter_id", "first_name"}
)

# The only fields the regular "user" role may change on an existing voter.
# Owner/admin may change every field except voter_id, which nobody can change.
# HouseNo is included because it used to be part of Address, which users could edit.
USER_EDITABLE_FIELDS: frozenset[str] = frozenset(
    {"party", "caste", "mobile", "street", "house_no"}
)

# Exact dropdown values. Blank / null is allowed and is not one of these.
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
    "street": 1000,
    "caste": 128,
    "mobile": 20,
    "village": 128,
    "panchayath": 128,
    "mandal": 128,
    "current_city": 128,
    "party": 32,
}

# Filters that can expose personal data. They travel in a POST body, never in a URL.
SEARCH_FILTER_FIELDS: tuple[str, ...] = (
    "q",
    "voter_id",
    "village",
    "panchayath",
    "mandal",
    "is_local",
    "booth",
    "ward",
    "caste",
    "party",
    "mobile",
)

SYNC_BATCH_LIMIT = 500
IMPORT_ROW_LIMIT = 20_000
IMPORT_BYTE_LIMIT = 5 * 1024 * 1024
EXPORT_ROW_LIMIT = 50_000
