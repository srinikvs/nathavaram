"""Pull changes since a revision and push batched offline edits.

Conflict policy (also documented in docs/SYNC.md):
- Last-write-wins by updated_at.
- The server assigns a new revision only when a change is stored.
- Equal timestamps with the same content are an idempotent replay.
- Equal timestamps with different content keep the server row.
- VoterId clashes with a different active row are reported and not applied.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import DATA_FIELDS, SYNC_BATCH_LIMIT
from app.models import Voter
from app.schemas import SyncChange, SyncPullResponse, SyncPushRequest, SyncPushResponse, SyncResult, VoterPublic, VoterWrite
from app.services.revision import allocate_revision, current_revision
from app.services.voters import voter_to_public
from app.validation import check_client_timestamp, dump_dt, parse_dt


def _server_time() -> str:
    return dump_dt(datetime.now(timezone.utc))


def pull_changes(db: Session, since: int, limit: int) -> SyncPullResponse:
    if since < 0:
        since = 0
    if limit < 1:
        limit = 1
    if limit > 1000:
        limit = 1000
    rows = list(
        db.scalars(
            select(Voter)
            .where(Voter.revision > since)
            .order_by(Voter.revision.asc())
            .limit(limit + 1)
        ).all()
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    return SyncPullResponse(
        server_time=_server_time(),
        server_revision=current_revision(db),
        since=since,
        has_more=has_more,
        changes=[voter_to_public(row) for row in page],
    )


def push_changes(db: Session, body: SyncPushRequest) -> SyncPushResponse:
    if len(body.changes) > SYNC_BATCH_LIMIT:
        from app.errors import ServiceError

        raise ServiceError(f"A sync batch can contain at most {SYNC_BATCH_LIMIT} changes", 422)
    results: list[SyncResult] = []
    for change in body.changes:
        results.append(_apply_one(db, change, body.device_id))
    db.commit()
    return SyncPushResponse(
        server_time=_server_time(),
        server_revision=current_revision(db),
        results=results,
    )


def _apply_one(db: Session, change: SyncChange, device_id: str) -> SyncResult:
    try:
        incoming = check_client_timestamp(change.updated_at)
    except ValueError as exc:
        return SyncResult(id=str(change.id), status="validation_error", message=str(exc))

    record_id = str(change.id)
    existing = db.get(Voter, record_id)
    if change.deleted:
        return _apply_delete(db, change, existing, incoming, device_id)

    try:
        snapshot = VoterWrite.model_validate(change.model_dump(exclude={"deleted", "base_revision", "created_at", "updated_at"}))
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = first.get("loc") or ()
        field = str(loc[-1]) if loc else "record"
        return SyncResult(
            id=record_id,
            status="validation_error",
            message=f"{field}: {first['msg']}",
        )

    clash = _active_voter_id_owner(db, snapshot.voter_id, exclude_id=record_id)
    if clash is not None:
        return SyncResult(
            id=record_id,
            status="voter_id_conflict",
            message=f"VoterId {snapshot.voter_id} belongs to another active record",
            server_revision=clash.revision,
            server_record=voter_to_public(clash),
        )

    if existing is None:
        created = dump_dt(incoming)
        if change.created_at is not None:
            try:
                created = dump_dt(check_client_timestamp(change.created_at))
            except ValueError as exc:
                return SyncResult(id=record_id, status="validation_error", message=str(exc))
        voter = Voter(
            id=record_id,
            created_at=created,
            updated_at=dump_dt(incoming),
            source_id=device_id,
            revision=0,
            deleted_at=None,
        )
        _write_snapshot(voter, snapshot)
        voter.revision = allocate_revision(db)
        db.add(voter)
        db.flush()
        return SyncResult(
            id=record_id,
            status="applied",
            server_revision=voter.revision,
            server_record=voter_to_public(voter),
        )

    return _merge_existing(db, existing, snapshot, incoming, device_id, deleted=False)


def _apply_delete(
    db: Session,
    change: SyncChange,
    existing: Voter | None,
    incoming: datetime,
    device_id: str,
) -> SyncResult:
    record_id = str(change.id)
    if existing is None:
        return SyncResult(
            id=record_id,
            status="applied",
            message="No server record; delete ignored",
            server_revision=None,
            server_record=None,
        )
    stored = parse_dt(existing.updated_at)
    if incoming < stored:
        return _conflict(existing, "Server record is newer")
    if incoming == stored and existing.deleted_at is not None:
        return SyncResult(
            id=record_id,
            status="applied",
            message="Already deleted",
            server_revision=existing.revision,
            server_record=voter_to_public(existing),
        )
    if incoming == stored and existing.deleted_at is None:
        return _conflict(existing, "Server record has the same updated_at and is still active")
    stamp = dump_dt(incoming)
    existing.deleted_at = stamp
    existing.updated_at = stamp
    existing.source_id = device_id
    existing.revision = allocate_revision(db)
    db.flush()
    return SyncResult(
        id=record_id,
        status="applied",
        server_revision=existing.revision,
        server_record=voter_to_public(existing),
    )


def _merge_existing(
    db: Session,
    existing: Voter,
    snapshot: VoterWrite,
    incoming: datetime,
    device_id: str,
    deleted: bool,
) -> SyncResult:
    stored = parse_dt(existing.updated_at)
    if incoming < stored:
        return _conflict(existing, "Server record is newer")
    if incoming == stored:
        if _same_content(existing, snapshot, deleted=deleted):
            return SyncResult(
                id=existing.id,
                status="applied",
                message="Already stored",
                server_revision=existing.revision,
                server_record=voter_to_public(existing),
            )
        return _conflict(existing, "Server record has the same updated_at and different content")
    _write_snapshot(existing, snapshot)
    existing.deleted_at = None
    existing.updated_at = dump_dt(incoming)
    existing.source_id = device_id
    existing.revision = allocate_revision(db)
    db.flush()
    return SyncResult(
        id=existing.id,
        status="applied",
        server_revision=existing.revision,
        server_record=voter_to_public(existing),
    )


def _write_snapshot(voter: Voter, snapshot: VoterWrite) -> None:
    for field in DATA_FIELDS:
        setattr(voter, field, getattr(snapshot, field))


def _same_content(voter: Voter, snapshot: VoterWrite, *, deleted: bool) -> bool:
    if (voter.deleted_at is not None) != deleted:
        return False
    for field in DATA_FIELDS:
        if getattr(voter, field) != getattr(snapshot, field):
            return False
    return True


def _active_voter_id_owner(db: Session, voter_id: str, exclude_id: str) -> Voter | None:
    return db.scalar(
        select(Voter).where(
            Voter.voter_id == voter_id,
            Voter.deleted_at.is_(None),
            Voter.id != exclude_id,
        )
    )


def _conflict(existing: Voter, message: str) -> SyncResult:
    return SyncResult(
        id=existing.id,
        status="conflict",
        message=message,
        server_revision=existing.revision,
        server_record=voter_to_public(existing),
    )
