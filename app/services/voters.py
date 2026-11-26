"""Voter CRUD and search. voter_id is the primary key; soft-deleted rows stay for sync."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.constants import DATA_FIELDS, EXPORT_ROW_LIMIT, LABEL_BY_FIELD, USER_EDITABLE_FIELDS
from app.errors import ServiceError
from app.models import User, Voter
from app.permissions import can_restore
from app.schemas import DeletedVoterPublic, VoterPatch, VoterPublic, VoterSearch, VoterWrite
from app.services.revision import allocate_revision
from app.validation import dump_dt


DUPLICATE_VOTER_ID_MESSAGE = (
    "A voter with VoterId {voter_id} already exists. Each VoterId must be unique; "
    "search for the existing voter or use a different VoterId."
)
VOTER_ID_LOCKED_MESSAGE = "VoterId cannot be changed after the voter is created"
HARD_DELETED_MESSAGE = (
    "This voter was permanently deleted. Its personal data is gone and it cannot be restored."
)
SOFT_DELETED_VOTER_ID_MESSAGE = (
    "VoterId belongs to a deleted record; ask a manager to restore it"
)
HARD_DELETE_NEEDS_SOFT_DELETE_MESSAGE = (
    "Soft delete the voter first. Only deleted voters can be permanently deleted."
)
UNRECOVERABLE_VOTER_ID_MESSAGE = (
    "VoterId belongs to a deleted record whose details were removed before soft delete kept "
    "them, so it cannot be restored or reused. A manager or admin must permanently delete it first."
)
UNRECOVERABLE_RESTORE_MESSAGE = (
    "This voter's details were removed before soft delete kept them, so it cannot be restored. "
    "It can only be permanently deleted."
)


def _now() -> str:
    return dump_dt(datetime.now(timezone.utc))


def _active(stmt):
    return stmt.where(Voter.deleted_at.is_(None))


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _contains(column, value: str):
    return column.ilike(_like(value), escape="\\")


def is_unrecoverable_soft_delete(voter: Voter) -> bool:
    """True for a soft-deleted row whose personal fields were removed before PR #19.

    Those rows were tombstoned by strip_to_tombstone: first_name is "", is_local is False, and
    every other data field is None. Post-PR-19 soft deletes keep their fields, so they never match.
    Hard-deleted rows are excluded: hard_deleted_at already makes them unrestorable.
    Derived from stored values only; no column is written or backfilled.
    """
    if voter.deleted_at is None or voter.hard_deleted_at is not None:
        return False
    if voter.first_name != "" or voter.is_local is not False:
        return False
    return all(
        getattr(voter, field) is None
        for field in DATA_FIELDS
        if field not in {"voter_id", "first_name", "is_local"}
    )


def normalize_voter_id(raw: str) -> str:
    """Path and body values are matched in the stored form: trimmed and upper-case."""
    return raw.strip().upper()


def duplicate_voter_id_error(voter_id: str) -> ServiceError:
    return ServiceError(DUPLICATE_VOTER_ID_MESSAGE.format(voter_id=voter_id), 409, "voter_id")


def ensure_voter_id_unchanged(voter: Voter, requested: str) -> None:
    """VoterId is the unique key: it is fixed once the voter exists."""
    if requested != voter.voter_id:
        raise ServiceError(VOTER_ID_LOCKED_MESSAGE, 422, "voter_id")


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
        f"Your role cannot change: {names}. Users and managers may edit only {allowed}; "
        "ask an admin to change other fields."
    )


def ensure_user_may_change(voter: Voter, values: dict[str, Any]) -> None:
    blocked = disallowed_changes(voter, values)
    if blocked:
        raise ServiceError(forbidden_fields_message(blocked), 403, blocked[0])


def voter_to_public(voter: Voter) -> VoterPublic:
    return VoterPublic.model_validate(voter)


def deleted_voter_to_public(voter: Voter) -> DeletedVoterPublic:
    public = voter_to_public(voter).model_dump()
    return DeletedVoterPublic(**public, details_recoverable=not is_unrecoverable_soft_delete(voter))


def apply_write(voter: Voter, data: VoterWrite) -> None:
    for field in DATA_FIELDS:
        setattr(voter, field, getattr(data, field))


def get_active(db: Session, voter_id: str) -> Voter:
    voter = db.get(Voter, normalize_voter_id(voter_id))
    if voter is None or voter.deleted_at is not None:
        raise ServiceError("Voter not found", 404)
    return voter


def create_voter(db: Session, data: VoterWrite, source_id: str, user: User) -> Voter:
    """Insert a voter. A soft-deleted row with the same voter_id is revived in place, but only
    by a role that may restore; anyone else gets a 409 and nothing changes."""
    existing = db.get(Voter, data.voter_id)
    if existing is not None and existing.deleted_at is None:
        raise duplicate_voter_id_error(data.voter_id)
    if existing is not None and is_unrecoverable_soft_delete(existing):
        raise ServiceError(UNRECOVERABLE_VOTER_ID_MESSAGE, 409, "voter_id")
    if existing is not None and existing.hard_deleted_at is None and not can_restore(user):
        raise ServiceError(SOFT_DELETED_VOTER_ID_MESSAGE, 409, "voter_id")
    now = _now()
    if existing is None:
        voter = Voter(voter_id=data.voter_id, created_at=now, source_id=source_id, revision=0)
        db.add(voter)
    else:
        # Reusing the VoterId of a hard-deleted row is a new record: the payload replaces every
        # field and the old data is not restored. A soft-deleted row is restored only by a
        # manager or admin, who may reuse it here as well.
        voter = existing
        voter.created_at = now
        voter.deleted_at = None
        voter.hard_deleted_at = None
    voter.updated_at = now
    voter.source_id = source_id
    apply_write(voter, data)
    voter.revision = allocate_revision(db)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise duplicate_voter_id_error(data.voter_id) from exc
    db.refresh(voter)
    return voter


def update_voter(
    db: Session, voter_id: str, patch: VoterPatch, source_id: str, *, all_fields: bool
) -> Voter:
    """Apply a partial edit. `all_fields` is admin (owner included); others edit USER_EDITABLE_FIELDS."""
    voter = get_active(db, voter_id)
    changes = patch.model_dump(exclude_unset=True)
    if not changes:
        return voter
    for required in ("voter_id", "first_name"):
        if required in changes and changes[required] is None:
            raise ServiceError(f"{required} cannot be null", 422, required)
    if "voter_id" in changes:
        ensure_voter_id_unchanged(voter, changes.pop("voter_id"))
    if not all_fields:
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
    db: Session, voter_id: str, data: VoterWrite, source_id: str, *, all_fields: bool
) -> Voter:
    """Replace every data column. Used by the web form, which always posts the full row."""
    voter = get_active(db, voter_id)
    ensure_voter_id_unchanged(voter, data.voter_id)
    if not all_fields:
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
    """Erase every personal field. Only voter_id, timestamps, revision and deleted_at remain.

    voter_id is the primary key, so it stays and a later create or sync push with the same
    voter_id revives the row. first_name is NOT NULL, so it holds an empty string.
    """
    for field in DATA_FIELDS:
        if field != "voter_id":
            setattr(voter, field, None)
    voter.is_local = False
    voter.first_name = ""


def soft_delete_voter(db: Session, voter: Voter, source_id: str, now: str) -> None:
    """Soft-delete one loaded row without committing (shared by single and bulk delete).

    Every field is kept, so the row can be restored exactly. It leaves the active list.
    """
    voter.deleted_at = now
    voter.updated_at = now
    voter.source_id = source_id
    voter.revision = allocate_revision(db)


def soft_delete(db: Session, voter_id: str, source_id: str) -> Voter:
    voter = get_active(db, voter_id)
    soft_delete_voter(db, voter, source_id, _now())
    db.commit()
    db.refresh(voter)
    return voter


def hard_delete_voter(db: Session, voter: Voter, source_id: str, now: str) -> None:
    """Permanently remove personal data from one soft-deleted row, without committing.

    The row stays so sync pulls the wipe: personal fields are nulled, deleted_at is kept,
    and hard_deleted_at marks the row as unrestorable. Campaign recipient copies of the
    mobile are scrubbed here so hard-deleted numbers do not linger in campaign tables.
    """
    strip_to_tombstone(voter)
    voter.deleted_at = voter.deleted_at or now
    voter.hard_deleted_at = now
    voter.updated_at = now
    voter.source_id = source_id
    voter.revision = allocate_revision(db)
    from app.services.campaigns import scrub_recipient_mobiles_for_voter

    scrub_recipient_mobiles_for_voter(db, voter.voter_id)


def check_hard_deletable(voter: Voter) -> None:
    if voter.hard_deleted_at is not None:
        raise ServiceError(HARD_DELETED_MESSAGE, 409, "voter_id")
    if voter.deleted_at is None:
        raise ServiceError(HARD_DELETE_NEEDS_SOFT_DELETE_MESSAGE, 409, "voter_id")


def hard_delete(db: Session, voter_id: str, source_id: str) -> Voter:
    voter = load_any(db, voter_id)
    check_hard_deletable(voter)
    hard_delete_voter(db, voter, source_id, _now())
    db.commit()
    db.refresh(voter)
    return voter


def check_restorable(voter: Voter) -> None:
    if voter.hard_deleted_at is not None:
        raise ServiceError(HARD_DELETED_MESSAGE, 409, "voter_id")
    if voter.deleted_at is None:
        raise ServiceError("Voter is not deleted", 409, "voter_id")
    if is_unrecoverable_soft_delete(voter):
        raise ServiceError(UNRECOVERABLE_RESTORE_MESSAGE, 409, "voter_id")


def restore(db: Session, voter_id: str, source_id: str) -> Voter:
    """Bring a soft-deleted voter back exactly as it was at soft-delete time."""
    voter = load_any(db, voter_id)
    check_restorable(voter)
    voter.deleted_at = None
    voter.updated_at = _now()
    voter.source_id = source_id
    voter.revision = allocate_revision(db)
    db.commit()
    db.refresh(voter)
    return voter


def load_any(db: Session, voter_id: str) -> Voter:
    """Any row, active or deleted. Only Deleted voters and the restore/hard-delete path use it."""
    voter = db.get(Voter, normalize_voter_id(voter_id))
    if voter is None:
        raise ServiceError("Voter not found", 404)
    return voter


def list_deleted(db: Session, query: VoterSearch) -> tuple[list[Voter], int]:
    """Soft-deleted rows that have not been hard-deleted, newest deletions first."""
    base = select(Voter).where(Voter.deleted_at.is_not(None), Voter.hard_deleted_at.is_(None))
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    stmt = base.order_by(Voter.deleted_at.desc(), Voter.voter_id)
    offset = (query.page - 1) * query.page_size
    rows = list(db.scalars(stmt.offset(offset).limit(query.page_size)).all())
    return rows, int(total)


def _full_name():
    return Voter.first_name.op("||")(" ").op("||")(func.coalesce(Voter.last_name, ""))


def search_voters(db: Session, query: VoterSearch) -> tuple[list[Voter], int]:
    stmt = _apply_filters(_active(select(Voter)), query)
    total = db.scalar(_apply_filters(_active(select(func.count()).select_from(Voter)), query)) or 0
    stmt = stmt.order_by(_full_name(), Voter.voter_id)
    offset = (query.page - 1) * query.page_size
    rows = list(db.scalars(stmt.offset(offset).limit(query.page_size)).all())
    return rows, int(total)


def export_voters(db: Session, query: VoterSearch) -> list[Voter]:
    stmt = _apply_filters(_active(select(Voter)), query)
    stmt = stmt.order_by(_full_name(), Voter.voter_id).limit(EXPORT_ROW_LIMIT)
    return list(db.scalars(stmt).all())


_SEARCH_TEXT_COLUMNS = (
    Voter.first_name,
    Voter.last_name,
    Voter.relative_name,
    Voter.voter_id,
    Voter.house_no,
    Voter.street,
    Voter.village,
    Voter.panchayath,
    Voter.mandal,
    Voter.current_city,
    Voter.mobile,
    Voter.caste,
    Voter.booth,
    Voter.ward,
    Voter.party,
    Voter.constituency,
)


def _apply_filters(stmt, query: VoterSearch):
    if query.q:
        like = _like(query.q)
        matches = [_contains(column, query.q) for column in _SEARCH_TEXT_COLUMNS]
        matches.append(cast(_full_name(), String).ilike(like, escape="\\"))
        stmt = stmt.where(or_(*matches))
    if query.voter_id:
        stmt = stmt.where(_contains(Voter.voter_id, query.voter_id))
    if query.village:
        stmt = stmt.where(_contains(Voter.village, query.village))
    if query.panchayath:
        stmt = stmt.where(_contains(Voter.panchayath, query.panchayath))
    if query.mandal:
        stmt = stmt.where(_contains(Voter.mandal, query.mandal))
    if query.is_local:
        stmt = stmt.where(Voter.is_local.is_(query.is_local == "Local"))
    if query.booth:
        stmt = stmt.where(_contains(Voter.booth, query.booth))
    if query.ward:
        stmt = stmt.where(_contains(Voter.ward, query.ward))
    if query.caste:
        stmt = stmt.where(_contains(Voter.caste, query.caste))
    if query.party:
        stmt = stmt.where(func.lower(Voter.party) == query.party.lower())
    if query.gender:
        stmt = stmt.where(Voter.gender == query.gender)
    if query.mobile:
        stmt = stmt.where(_contains(Voter.mobile, query.mobile))
    return stmt
