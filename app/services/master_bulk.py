"""Admin rename and guarded delete of Master Data values.

Master Data lists are a GROUP BY over active voters, so a "row" is a value (or composite key)
that voters currently carry. There is no catalog to edit:

* Rename/replace rewrites ONE voter column, the tab's primary value (the first key column),
  on every active voter whose key columns match the selected row. Companion key columns of a
  composite row (street+ward, mandalam+constituency, panchayathi+mandal) are used only to
  find the voters, so "Main Street" in Ward 1 is renamed without touching "Main Street" in
  Ward 2. Selecting several rows replaces them all with the one new value (a merge).
* A "(blank)" key matches only voters whose value is actually empty.
* Delete is refused while any active voter still uses the value. Nothing is cleared.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import ServiceError
from app.models import Voter
from app.schemas import VoterPatch
from app.services.master_data import BLANK, SPECS, _clean
from app.services.revision import allocate_revision
from app.services.voters import _now

MASTER_ROW_LIMIT = 500
UNAVAILABLE_MESSAGE = "This list is unavailable, so it has nothing to change"


def key_names(entity: str) -> tuple[str, ...]:
    return tuple(k for k, _, _ in SPECS[entity].keys)


def _spec(entity: str):
    spec = SPECS.get(entity)
    if spec is None:
        raise ServiceError("Unknown master data list", 404, "entity")
    if not spec.keys:
        raise ServiceError(UNAVAILABLE_MESSAGE, 409, "entity")
    return spec


def _clean_items(entity: str, items: Any) -> list[dict[str, str]]:
    spec = _spec(entity)
    names = {k for k, _, _ in spec.keys}
    if not isinstance(items, list) or not items:
        raise ServiceError("Select at least one row", 422, "items")
    if len(items) > MASTER_ROW_LIMIT:
        raise ServiceError(f"Select at most {MASTER_ROW_LIMIT} rows at a time", 422, "items")
    seen: dict[tuple, dict[str, str]] = {}
    for item in items:
        if not isinstance(item, dict) or set(item) != names:
            raise ServiceError(f"Each row must give exactly: {', '.join(sorted(names))}", 422, "items")
        if not all(isinstance(v, str) for v in item.values()):
            raise ServiceError("Row values must be text", 422, "items")
        seen[tuple(sorted(item.items()))] = dict(item)
    return list(seen.values())


def _match(entity: str, item: dict[str, str]):
    clauses = []
    for key, _, column in SPECS[entity].keys:
        if item[key] == BLANK:
            clauses.append(_clean(column).is_(None))
        else:
            clauses.append(func.trim(column) == item[key])
    return Voter.deleted_at.is_(None), *clauses


def usage_counts(db: Session, entity: str, items: Any) -> list[tuple[dict[str, str], int]]:
    """Active voters that still use each selected row."""
    rows = _clean_items(entity, items)
    return [
        (item, db.scalar(select(func.count()).select_from(Voter).where(*_match(entity, item))) or 0)
        for item in rows
    ]


def _describe(item: dict[str, str]) -> str:
    return " / ".join(item.values())


def check_delete(db: Session, entity: str, items: Any) -> dict[str, int]:
    """Refuse when any selected value is in use. Never changes voters."""
    counts = usage_counts(db, entity, items)
    used = [(item, n) for item, n in counts if n]
    if used:
        total = sum(n for _, n in used)
        detail = "; ".join(f"{_describe(item)}: {n}" for item, n in used)
        raise ServiceError(
            f"Cannot delete: {total} active voter{'s' if total != 1 else ''} still "
            f"{'uses' if total == 1 else 'use'} "
            f"{'these values' if len(used) != 1 else 'this value'} ({detail}). "
            "Rename the value instead, or change or remove those voters first. No voters were changed.",
            409,
            "items",
        )
    return {"in_use": 0, "unused": len(counts)}


def rename_values(
    db: Session, entity: str, items: Any, new_value: Any, source_id: str
) -> dict[str, int]:
    """Replace the primary value on every active voter that matches a selected row."""
    spec = _spec(entity)
    rows = _clean_items(entity, items)
    column = spec.keys[0][2]
    try:
        patch = VoterPatch.model_validate({column.key: new_value})
    except ValidationError as exc:
        message = exc.errors()[0]["msg"].removeprefix("Value error, ")
        raise ServiceError(f"{spec.keys[0][1]}: {message}", 422, "new_value") from exc
    value = getattr(patch, column.key)
    if value is None:
        raise ServiceError(f"{spec.keys[0][1]}: enter the new value", 422, "new_value")
    matched: dict[str, Voter] = {}
    for item in rows:
        found = list(db.scalars(select(Voter).where(*_match(entity, item))))
        if not found:
            raise ServiceError(
                f"No active voters currently have {_describe(item)}. No changes were made.",
                404,
                "items",
            )
        matched.update({v.voter_id: v for v in found})
    now = _now()
    changed = 0
    for voter in matched.values():
        if getattr(voter, column.key) == value:
            continue
        setattr(voter, column.key, value)
        voter.updated_at = now
        voter.source_id = source_id
        voter.revision = allocate_revision(db)
        changed += 1
    db.commit()
    return {"updated": changed, "matched": len(matched), "rows": len(rows)}
