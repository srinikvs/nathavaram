"""Column contract shared by the database, API, CSV, and web form.

Display names are the exact headers used in the UI and in CSV import/export.
Internal names are snake_case.
"""

from __future__ import annotations

# (internal name, exact display name) in the order clerks see on paper.
VOTER_COLUMNS: tuple[tuple[str, str], ...] = (
    ("booth", "Booth"),
    ("bs_no_2024", "2024 B.S.No"),
    ("ward", "Ward"),
    ("ward_s_no", "Ward S No"),
    ("voter_id", "VoterId"),
    ("first_name", "FirstName"),
    ("surname", "SurName"),
    ("relative_name", "RelativeName"),
    ("address", "Address"),
    ("village", "Village"),
    ("permanent_address", "Permanent address"),
    ("is_local", "Local"),
    ("gender", "Gender"),
    ("age", "Age"),
    ("caste", "Caste"),
    ("mobile", "Mobile"),
    ("c_city", "C_City"),
    ("profession", "Profession"),
    ("party", "Party"),
)

CSV_HEADERS: tuple[str, ...] = tuple(label for _, label in VOTER_COLUMNS)
FIELD_BY_LABEL: dict[str, str] = {label: name for name, label in VOTER_COLUMNS}
LABEL_BY_FIELD: dict[str, str] = {name: label for name, label in VOTER_COLUMNS}
DATA_FIELDS: tuple[str, ...] = tuple(name for name, _ in VOTER_COLUMNS)

# Extra CSV import headers: the pre-rename labels and the pre-rename column names.
LEGACY_HEADER_ALIASES: dict[str, str] = {
    "2024 Booth": "booth",
    "2026 Ward": "ward",
    "booth_2024": "booth",
    "ward_2026": "ward",
    # house_no is no longer stored: an imported value is folded into Address.
    "HouseNo": "house_no",
    "House No": "house_no",
    "House no": "house_no",
    "house_no": "house_no",
}
IMPORT_HEADER_FIELDS: dict[str, str] = {**FIELD_BY_LABEL, **LEGACY_HEADER_ALIASES}
# Columns an older CSV may lack. Every other column in VOTER_COLUMNS is required.
OPTIONAL_IMPORT_FIELDS: frozenset[str] = frozenset(
    {"address", "village", "permanent_address", "is_local", "house_no"}
)

# The only fields the regular "user" role may change on an existing voter.
# Owner/admin may change every field except voter_id, which nobody can change.
USER_EDITABLE_FIELDS: frozenset[str] = frozenset({"party", "caste", "mobile", "address"})
HOUSE_NO_PREFIX = "H.No "

GENDERS: tuple[str, ...] = ("Male", "Female", "Other", "Unknown")

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
    "bs_no_2024": 64,
    "ward": 64,
    "ward_s_no": 64,
    "voter_id": 32,
    "first_name": 128,
    "surname": 128,
    "relative_name": 128,
    "address": 1000,
    "village": 128,
    "permanent_address": 1000,
    "caste": 128,
    "mobile": 20,
    "c_city": 128,
    "profession": 128,
    "party": 32,
    "gender": 16,
}

# Filters that can expose personal data. They travel in a POST body, never in a URL.
SEARCH_FILTER_FIELDS: tuple[str, ...] = (
    "q",
    "voter_id",
    "village",
    "is_local",
    "booth",
    "ward",
    "caste",
    "party",
    "mobile",
    "gender",
)

SYNC_BATCH_LIMIT = 500
IMPORT_ROW_LIMIT = 20_000
IMPORT_BYTE_LIMIT = 5 * 1024 * 1024
EXPORT_ROW_LIMIT = 50_000
