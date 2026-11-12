"""Voter CRUD and search. Soft-deleted rows stay in the table for sync."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.constants import DATA_FIELDS, EXPORT_ROW_LIMIT, LABEL_BY_FIELD, USER_EDITABLE_FIELDS
from app.errors import ServiceError
from app.models import User, Voter
from app.schemas import VoterPatch, VoterPublic, VoterSearch, VoterWrite
from app.services.revision import allocate_revision
from app.validation import dump_dt


DUPLICATE_VOTER_ID_MESSAGE = (
    "A voter with VoterId {voter_id} already exists. Each VoterId must be unique; "
    "search for the existing voter or use a different VoterId."
)
VOTER_ID_LOCKED_MESSAGE = "VoterId cannot be changed after the voter is created"


def _now() -> str:
    return dump_dt(datetime.now(timezone.utc))


def _active(stmt):
    return stmt.where(Voter.deleted_at.is_(None))


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _contains(column, value: str):
    return column.ilike(_like(value), escape="\\")


def ensure_unique_voter_id(db: Session, voter_id: str, exclude_id: str | None) -> None:
    stmt = select(Voter.id).where(Voter.voter_id == voter_id, Voter.deleted_at.is_(None))
    if exclude_id is not None:
        stmt = stmt.where(Voter.id != exclude_id)
    other = db.scalar(stmt)
    if other is not None:
        raise ServiceError(DUPLICATE_VOTER_ID_MESSAGE.format(voter_id=voter_id), 409, "voter_id")


def ensure_voter_id_unchanged(voter: Voter, requested: str) -> None:
    """VoterId is the unique key: it is fixed once the voter exists."""
    if requested != voter.voter_id:
        raise ServiceError(VOTER_ID_LOCKED_MESSAGE, 422, "voter_id")


def is_privileged(user: User) -> bool:
    """Owner/admin check used across the app: the owner is always an admin."""
    return user.role == "admin"


def disallowed_changes(voter: Voter, values: dict[str, Any]) -> list[str]:
    """Fields in `values` that a regular user may not change and that differ from `voter`.

    Unchanged values never count, so a client may echo a full row back. voter_id is
    handled separately because nobody may change it.
    """
    return [
        field
        for field, value in values.items()
        if field in DATA_FIELDS
        and field != "voter_id"
        and field not in USER_EDITABLE_FIELDS
        and getattr(voter, field) != value
    ]


def forbidden_fields_message(fields: list[str]) -> str:
    names = ", ".join(LABEL_BY_FIELD[field] for field in fields)
    allowed = ", ".join(LABEL_BY_FIELD[field] for field in DATA_FIELDS if field in USER_EDITABLE_FIELDS)
    return (
        f"Your role cannot change: {names}. Regular users may edit only {allowed}; "
        "ask an owner or admin to change other fields."
    )


def ensure_user_may_change(voter: Voter, values: dict[str, Any]) -> None:
    blocked = disallowed_changes(voter, values)
    if blocked:
        raise ServiceError(forbidden_fields_message(blocked), 403, blocked[0])


def voter_to_public(voter: Voter) -> VoterPublic:
    return VoterPublic.model_validate(voter)


def apply_write(voter: Voter, data: VoterWrite) -> None:
    for field in DATA_FIELDS:
        setattr(voter, field, getattr(data, field))


def get_active(db: Session, voter_id: str) -> Voter:
    voter = db.get(Voter, voter_id)
    if voter is None or voter.deleted_at is not None:
        raise ServiceError("Voter not found", 404)
    return voter


def create_voter(db: Session, data: VoterWrite, source_id: str) -> Voter:
    record_id = str(data.id) if data.id is not None else str(uuid.uuid4())
    if db.get(Voter, record_id) is not None:
        raise ServiceError("A voter with this id already exists", 409, "id")
    ensure_unique_voter_id(db, data.voter_id, exclude_id=None)
    now = _now()
    voter = Voter(id=record_id, created_at=now, updated_at=now, source_id=source_id, revision=0)
    apply_write(voter, data)
    voter.revision = allocate_revision(db)
    db.add(voter)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ServiceError("VoterId already exists", 409, "voter_id") from exc
    db.refresh(voter)
    return voter


def update_voter(
    db: Session, voter_id: str, patch: VoterPatch, source_id: str, *, privileged: bool
) -> Voter:
    """Apply a partial edit. `privileged` is owner/admin; others edit USER_EDITABLE_FIELDS only."""
    voter = get_active(db, voter_id)
    changes = patch.model_dump(exclude_unset=True)
    if not changes:
        return voter
    for required in ("voter_id", "first_name", "gender"):
        if required in changes and changes[required] is None:
            raise ServiceError(f"{required} cannot be null", 422, required)
    if "voter_id" in changes:
        ensure_voter_id_unchanged(voter, changes.pop("voter_id"))
    if not privileged:
        ensure_user_may_change(voter, changes)
    if not changes:
        return voter
    for key, value in changes.items():
        setattr(voter, key, value)
    voter.updated_at = _now()
    voter.source_id = source_id
    voter.revision = allocate_revision(db)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ServiceError("VoterId already exists", 409, "voter_id") from exc
    db.refresh(voter)
    return voter


def replace_voter(
    db: Session, voter_id: str, data: VoterWrite, source_id: str, *, privileged: bool
) -> Voter:
    """Replace every data column. Used by the web form, which always posts the full row."""
    voter = get_active(db, voter_id)
    ensure_voter_id_unchanged(voter, data.voter_id)
    if not privileged:
        ensure_user_may_change(voter, {field: getattr(data, field) for field in DATA_FIELDS})
    apply_write(voter, data)
    voter.updated_at = _now()
    voter.source_id = source_id
    voter.revision = allocate_revision(db)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ServiceError("VoterId already exists", 409, "voter_id") from exc
    db.refresh(voter)
    return voter


def strip_to_tombstone(voter: Voter) -> None:
    """Erase every personal field. Only id, timestamps, revision and deleted_at remain.

    voter_id, first_name and gender are NOT NULL columns, so they hold inert placeholders:
    the row id without dashes, an empty string, and 'Unknown'. The unique index ignores
    soft-deleted rows, so the real VoterId is free to be reused.
    """
    for field in DATA_FIELDS:
        setattr(voter, field, None)
    voter.is_local = False
    voter.voter_id = voter.id.replace("-", "")[:32]
    voter.first_name = ""
    voter.gender = "Unknown"


def soft_delete(db: Session, voter_id: str, source_id: str) -> Voter:
    voter = get_active(db, voter_id)
    now = _now()
    strip_to_tombstone(voter)
    voter.deleted_at = now
    voter.updated_at = now
    voter.source_id = source_id
    voter.revision = allocate_revision(db)
    db.commit()
    db.refresh(voter)
    return voter


def search_voters(db: Session, query: VoterSearch) -> tuple[list[Voter], int]:
    stmt = _apply_filters(_active(select(Voter)), query)
    total = db.scalar(_apply_filters(_active(select(func.count()).select_from(Voter)), query)) or 0
    full_name = Voter.first_name.op("||")(" ").op("||")(func.coalesce(Voter.surname, ""))
    stmt = stmt.order_by(full_name, Voter.voter_id)
    offset = (query.page - 1) * query.page_size
    rows = list(db.scalars(stmt.offset(offset).limit(query.page_size)).all())
    return rows, int(total)


def export_voters(db: Session, query: VoterSearch) -> list[Voter]:
    stmt = _apply_filters(_active(select(Voter)), query)
    full_name = Voter.first_name.op("||")(" ").op("||")(func.coalesce(Voter.surname, ""))
    stmt = stmt.order_by(full_name, Voter.voter_id).limit(EXPORT_ROW_LIMIT)
    return list(db.scalars(stmt).all())


def _apply_filters(stmt, query: VoterSearch):
    if query.q:
        like = _like(query.q)
        full_name = Voter.first_name.op("||")(" ").op("||")(func.coalesce(Voter.surname, ""))
        stmt = stmt.where(
            or_(
                _contains(Voter.first_name, query.q),
                _contains(Voter.surname, query.q),
                _contains(Voter.relative_name, query.q),
                _contains(Voter.voter_id, query.q),
                _contains(Voter.address, query.q),
                _contains(Voter.village, query.q),
                _contains(Voter.mobile, query.q),
                _contains(Voter.caste, query.q),
                _contains(Voter.booth, query.q),
                _contains(Voter.ward, query.q),
                _contains(Voter.party, query.q),
                cast(full_name, String).ilike(like, escape="\\"),
            )
        )
    if query.voter_id:
        stmt = stmt.where(_contains(Voter.voter_id, query.voter_id))
    if query.village:
        stmt = stmt.where(_contains(Voter.village, query.village))
    if query.is_local:
        stmt = stmt.where(Voter.is_local.is_(query.is_local == "Yes"))
    if query.booth:
        stmt = stmt.where(_contains(Voter.booth, query.booth))
    if query.ward:
        stmt = stmt.where(_contains(Voter.ward, query.ward))
    if query.caste:
        stmt = stmt.where(_contains(Voter.caste, query.caste))
    if query.party:
        stmt = stmt.where(func.lower(Voter.party) == query.party.lower())
    if query.mobile:
        stmt = stmt.where(_contains(Voter.mobile, query.mobile))
    if query.gender:
        stmt = stmt.where(Voter.gender == query.gender)
    return stmt
