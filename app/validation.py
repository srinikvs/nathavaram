"""Shared field rules for API writes, the web form, and CSV import."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from app.constants import GENDERS, PARTIES, TEXT_LIMITS

_MOBILE_SEPARATORS = re.compile(r"[\s\-().]")
_VOTER_ID = re.compile(r"^[A-Z0-9]{3,32}$")
_USERNAME = re.compile(r"^[A-Za-z0-9._-]{3,32}$")

_GENDER_LOOKUP = {value.casefold(): value for value in GENDERS}
_PARTY_LOOKUP = {value.casefold(): value for value in PARTIES}

FORMULA_PREFIXES = frozenset({"=", "+", "-", "@", "\t", "\r"})

MIN_TIMESTAMP =datetime(2000, 1, 1, tzinfo=timezone.utc)
FUTURE_SKEW = timedelta(minutes=5)


def clean_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Expected text")
    text = value.strip()
    if not text:
        return None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise ValueError("Contains invalid control characters")
    return text


def require_text(value: object, label: str) -> str:
    text = clean_text(value)
    if text is None:
        raise ValueError(f"{label} is required")
    return text


def limit_text(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    limit = TEXT_LIMITS[field]
    if len(value) > limit:
        raise ValueError(f"Use at most {limit} characters")
    return value


def canonical_gender(value: object) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return "Unknown"
    text = clean_text(value)
    if text is None:
        return "Unknown"
    found = _GENDER_LOOKUP.get(text.casefold())
    if found is None:
        allowed = ", ".join(GENDERS)
        raise ValueError(f"Gender must be one of: {allowed}")
    return found


def canonical_party(value: object) -> str | None:
    text = clean_text(value) if not isinstance(value, str) or value.strip() else clean_text(value)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    text = clean_text(value)
    if text is None:
        return None
    found = _PARTY_LOOKUP.get(text.casefold())
    if found is None:
        allowed = ", ".join(PARTIES)
        raise ValueError(f"Party must be empty or one of: {allowed}")
    return found


def canonical_voter_id(value: object) -> str:
    text = require_text(value, "VoterId")
    upper = text.upper()
    if _VOTER_ID.fullmatch(upper) is None:
        raise ValueError("VoterId must be 3–32 letters or digits (EPIC-style)")
    return upper


def is_loose_indian_mobile(raw: str) -> bool:
    """Accept a 10-digit Indian mobile, with optional +91 / 91 / 0 and separators.

    The stored value is the trimmed original, so leading zeros and +91 stay.
    """
    compact = _MOBILE_SEPARATORS.sub("", raw.strip())
    if compact.startswith("+"):
        compact = compact[1:]
    if not compact.isdigit():
        return False
    if compact.startswith("91") and len(compact) == 12:
        national = compact[2:]
    elif compact.startswith("0") and len(compact) == 11:
        national = compact[1:]
    else:
        national = compact
    return bool(re.fullmatch(r"[6-9]\d{9}", national))


def canonical_mobile(value: object) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    text = clean_text(value)
    if text is None:
        return None
    if len(text) > TEXT_LIMITS["mobile"]:
        raise ValueError(f"Use at most {TEXT_LIMITS['mobile']} characters")
    if not is_loose_indian_mobile(text):
        raise ValueError(
            "Enter a 10-digit Indian mobile, optionally prefixed with +91 or 0"
        )
    return text


def canonical_age(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("Age must be a whole number")
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return None
        if not re.fullmatch(r"\d+", text):
            raise ValueError("Age must be a whole number")
        value = int(text)
    if not isinstance(value, int):
        raise ValueError("Age must be a whole number")
    if value < 1 or value > 130:
        raise ValueError("Age must be between 1 and 130, or blank if unknown")
    return value


def canonical_username(value: object) -> str:
    text = require_text(value, "Username")
    if _USERNAME.fullmatch(text) is None:
        raise ValueError("Username must be 3–32 characters: letters, digits, . _ -")
    return text


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def dump_dt(value: datetime) -> str:
    moment = as_utc(value)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond:06d}Z"


def parse_dt(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return as_utc(parsed)


def check_client_timestamp(value: datetime, *, now: datetime | None = None) -> datetime:
    moment = as_utc(value)
    current = as_utc(now or datetime.now(timezone.utc))
    if moment < MIN_TIMESTAMP:
        raise ValueError("updated_at must be on or after 2000-01-01 UTC")
    if moment > current + FUTURE_SKEW:
        raise ValueError("updated_at is more than 5 minutes in the future")
    return moment


def age_band(age: int | None) -> str:
    if age is None:
        return "Unknown"
    if age < 18:
        return "Under 18"
    if age <= 25:
        return "18–25"
    if age <= 35:
        return "26–35"
    if age <= 45:
        return "36–45"
    if age <= 55:
        return "46–55"
    if age <= 65:
        return "56–65"
    return "66+"


def csv_safe(value: object) -> str:
    """Neutralise spreadsheet formulas when a cell is opened in Excel."""
    if value is None:
        return ""
    text = str(value)
    if text[:1] in FORMULA_PREFIXES:
        return "'" + text
    return text


def csv_unsafe(value: str) -> str:
    """Undo csv_safe on import: drop one leading quote the exporter added.

    Only a quote directly in front of a formula character is removed, so a +91 mobile
    round-trips and ordinary text that starts with a quote is left alone.
    """
    if len(value) > 1 and value[0] == "'" and value[1] in FORMULA_PREFIXES:
        return value[1:]
    return value
