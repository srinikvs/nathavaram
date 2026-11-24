"""Admin bulk edit and bulk delete of voters. Both are all-or-nothing in one transaction."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import BULK_EDIT_FIELDS, BULK_ID_LIMIT, LABEL_BY_FIELD, USER_EDITABLE_FIELDS
from app.errors import ServiceError
from app.models import Voter
from app.schemas import VoterPatch
from app.services.revision import allocate_revision
from app.services.voters import (
    _now,
    check_hard_deletable,
    forbidden_fields_message,
    hard_delete_voter,
    normalize_voter_id,
    soft_delete_voter,
)

CONFIRM_REQUIRED_MESSAGE = "Bulk delete needs confirmation: send confirm=true after the user agrees"
CONFIRM_HARD_REQUIRED_MESSAGE = (
    "Permanent delete needs confirmation: send confirm=true after the user agrees. "
    "This removes the personal data and cannot be undone."
)


def normalize_ids(raw_ids: list[str]) -> list[str]:
    """Trim, upper-case and de-duplicate while keeping the order the client sent."""
    ids = list(
        dict.fromkeys(normalize_voter_id(v) for v in raw_ids if isinstance(v, str) and v.strip())
    )
    if not ids:
        raise ServiceError("Select at least one voter", 422, "voter_ids")
    if len(ids) > BULK_ID_LIMIT:
        raise ServiceError(f"Select at most {BULK_ID_LIMIT} voters at a time", 422, "voter_ids")
    return ids


def load_active(db: Session, ids: list[str]) -> list[Voter]:
    """Exactly the named active voters, or a 404 naming the missing ones (nothing applied)."""
    rows = {v.voter_id: v for v in db.scalars(select(Voter).where(Voter.voter_id.in_(ids)))}
    missing = [i for i in ids if i not in rows or rows[i].deleted_at is not None]
    if missing:
        shown = ", ".join(missing[:5]) + (" …" if len(missing) > 5 else "")
        raise ServiceError(
            f"Voter not found or already removed: {shown}. No changes were made.", 404, "voter_ids"
        )
    return [rows[i] for i in ids]


def ensure_bulk_fields_allowed(values: dict[str, Any], *, all_fields: bool) -> None:
    """Users and managers may bulk-edit only USER_EDITABLE_FIELDS; admin may edit any field."""
    if all_fields or not isinstance(values, dict):
        return
    blocked = sorted(k for k in values if k in BULK_EDIT_FIELDS and k not in USER_EDITABLE_FIELDS)
    if blocked:
        raise ServiceError(forbidden_fields_message(blocked), 403, blocked[0])


def validate_bulk_values(values: dict[str, Any]) -> dict[str, Any]:
    """Validate with the single-edit schema (VoterPatch). Raises ServiceError(422)."""
    if not isinstance(values, dict) or not values:
        raise ServiceError("Choose at least one field to change", 422, "values")
    if "voter_id" in values:
        raise ServiceError("VoterId cannot be changed", 422, "voter_id")
    unknown = sorted(k for k in values if k not in BULK_EDIT_FIELDS)
    if unknown:
        raise ServiceError(f"Field cannot be bulk edited: {', '.join(unknown)}", 422, unknown[0])
    cleaned = dict(values)
    if "is_local" in cleaned and (cleaned["is_local"] is None or cleaned["is_local"] == ""):
        cleaned["is_local"] = True  # blank Local/Non-Local means the default, Local
    if "first_name" in cleaned and not (
        isinstance(cleaned["first_name"], str) and cleaned["first_name"].strip()
    ):
        raise ServiceError("FirstName is required", 422, "first_name")
    try:
        patch = VoterPatch.model_validate(cleaned)
    except ValidationError as exc:
        err = exc.errors()[0]
        field = str(err["loc"][-1]) if err.get("loc") else "values"
        message = err["msg"].removeprefix("Value error, ")
        raise ServiceError(f"{LABEL_BY_FIELD.get(field, field)}: {message}", 422, field) from exc
    return patch.model_dump(exclude_unset=True)


def bulk_edit_voters(
    db: Session,
    voter_ids: list[str],
    values: dict[str, Any],
    source_id: str,
    *,
    all_fields: bool,
) -> list[Voter]:
    ensure_bulk_fields_allowed(values, all_fields=all_fields)
    changes = validate_bulk_values(values)
    voters = load_active(db, normalize_ids(voter_ids))
    now = _now()
    for voter in voters:
        for key, value in changes.items():
            setattr(voter, key, value)
        voter.updated_at = now
        voter.source_id = source_id
        voter.revision = allocate_revision(db)
    db.commit()
    return voters


def bulk_delete_voters(db: Session, voter_ids: list[str], source_id: str, *, confirm: bool) -> int:
    """Soft delete only: every field is kept and the rows can be restored by a manager or admin."""
    if confirm is not True:
        raise ServiceError(CONFIRM_REQUIRED_MESSAGE, 400, "confirm")
    voters = load_active(db, normalize_ids(voter_ids))
    now = _now()
    for voter in voters:
        soft_delete_voter(db, voter, source_id, now)
    db.commit()
    return len(voters)


def load_deleted_for_hard_delete(db: Session, ids: list[str]) -> list[Voter]:
    """Exactly the named soft-deleted rows. Any missing, active or already hard-deleted id stops
    the whole batch with nothing applied."""
    rows = {v.voter_id: v for v in db.scalars(select(Voter).where(Voter.voter_id.in_(ids)))}
    missing = [i for i in ids if i not in rows]
    if missing:
        shown = ", ".join(missing[:5]) + (" …" if len(missing) > 5 else "")
        raise ServiceError(f"Voter not found: {shown}. No changes were made.", 404, "voter_ids")
    picked = [rows[i] for i in ids]
    for voter in picked:
        check_hard_deletable(voter)
    return picked


def bulk_hard_delete_voters(db: Session, voter_ids: list[str], source_id: str, *, confirm: bool) -> int:
    """Permanently remove personal data from the listed soft-deleted voters (manager/admin)."""
    if confirm is not True:
        raise ServiceError(CONFIRM_HARD_REQUIRED_MESSAGE, 400, "confirm")
    voters = load_deleted_for_hard_delete(db, normalize_ids(voter_ids))
    now = _now()
    for voter in voters:
        hard_delete_voter(db, voter, source_id, now)
    db.commit()
    return len(voters)
