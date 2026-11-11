"""Voter CRUD and search. Soft-deleted rows stay in the table for sync."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.constants import DATA_FIELDS, EXPORT_ROW_LIMIT
from app.errors import ServiceError
from app.models import Voter
from app.schemas import VoterPatch, VoterPublic, VoterSearch, VoterWrite
from app.services.revision import allocate_revision
from app.validation import dump_dt


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
        raise ServiceError("VoterId already exists", 409, "voter_id")


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


def update_voter(db: Session, voter_id: str, patch: VoterPatch, source_id: str) -> Voter:
    voter = get_active(db, voter_id)
    changes = patch.model_dump(exclude_unset=True)
    if not changes:
        return voter
    if "voter_id" in changes:
        ensure_unique_voter_id(db, changes["voter_id"], exclude_id=voter.id)
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


def replace_voter(db: Session, voter_id: str, data: VoterWrite, source_id: str) -> Voter:
    """Replace every data column. Used by the web form, which always posts the full row."""
    voter = get_active(db, voter_id)
    ensure_unique_voter_id(db, data.voter_id, exclude_id=voter.id)
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


def soft_delete(db: Session, voter_id: str, source_id: str) -> Voter:
    voter = get_active(db, voter_id)
    now = _now()
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
                _contains(Voter.house_no, query.q),
                _contains(Voter.mobile, query.q),
                _contains(Voter.caste, query.q),
                _contains(Voter.booth_2024, query.q),
                _contains(Voter.ward_2026, query.q),
                _contains(Voter.party, query.q),
                cast(full_name, String).ilike(like, escape="\\"),
            )
        )
    if query.voter_id:
        stmt = stmt.where(_contains(Voter.voter_id, query.voter_id))
    if query.house_no:
        stmt = stmt.where(_contains(Voter.house_no, query.house_no))
    if query.booth:
        stmt = stmt.where(_contains(Voter.booth_2024, query.booth))
    if query.ward:
        stmt = stmt.where(_contains(Voter.ward_2026, query.ward))
    if query.caste:
        stmt = stmt.where(_contains(Voter.caste, query.caste))
    if query.party:
        stmt = stmt.where(func.lower(Voter.party) == query.party.lower())
    if query.mobile:
        stmt = stmt.where(_contains(Voter.mobile, query.mobile))
    if query.gender:
        stmt = stmt.where(Voter.gender == query.gender)
    return stmt
