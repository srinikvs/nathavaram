"""Pull changes since a revision and push batched offline edits.

Conflict policy (also documented in docs/SYNC.md):
- A change that carries base_revision is a conflict when the server row has moved past it.
- Without base_revision (or when it is current): last-write-wins by updated_at.
- Every change runs in its own savepoint, so one rejected change never rolls back the batch.
- The server assigns a new revision only when a change is stored.
- Equal timestamps with the same content are an idempotent replay.
- Equal timestamps with different content keep the server row.
- VoterId clashes with a different active row are reported and not applied.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.constants import DATA_FIELDS, SYNC_BATCH_LIMIT
from app.models import Voter
from app.schemas import SyncChange, SyncPullResponse, SyncPushRequest, SyncPushResponse, SyncResult, VoterPublic, VoterWrite
from app.services.revision import allocate_revision, current_revision
from app.errors import ServiceError
from app.services.voters import (
    VOTER_ID_LOCKED_MESSAGE,
    disallowed_changes,
    forbidden_fields_message,
    strip_to_tombstone,
    voter_to_public,
)
from app.validation import canonical_voter_id, check_client_timestamp, dump_dt, parse_dt


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


def push_changes(db: Session, body: SyncPushRequest, *, privileged: bool) -> SyncPushResponse:
    """`privileged` is owner/admin. Regular users may change USER_EDITABLE_FIELDS only."""
    if len(body.changes) > SYNC_BATCH_LIMIT:
        raise ServiceError(f"A sync batch can contain at most {SYNC_BATCH_LIMIT} changes", 422)
    results: list[SyncResult] = []
    for change in body.changes:
        results.append(_apply_guarded(db, change, body.device_id, privileged))
    db.commit()
    return SyncPushResponse(
        server_time=_server_time(),
        server_revision=current_revision(db),
        results=results,
    )


def _apply_guarded(db: Session, change: SyncChange, device_id: str, privileged: bool) -> SyncResult:
    """Apply one change inside a savepoint; a database clash rejects only this change."""
    try:
        with db.begin_nested():
            return _apply_one(db, change, device_id, privileged)
    except IntegrityError:
        record_id = str(change.id)
        owner = None
        if change.voter_id:
            try:
                owner = _active_voter_id_owner(db, canonical_voter_id(change.voter_id), exclude_id=record_id)
            except ValueError:
                owner = None
        if owner is None:
            return SyncResult(
                id=record_id,
                status="voter_id_conflict",
                message="VoterId belongs to another active record",
            )
        return SyncResult(
            id=record_id,
            status="voter_id_conflict",
            message=f"VoterId {owner.voter_id} belongs to another active record",
            server_revision=owner.revision,
            server_record=voter_to_public(owner),
        )


def _stale_base(existing: Voter, base_revision: int | None) -> bool:
    return base_revision is not None and existing.revision > base_revision


def _forbidden(existing: Voter, message: str) -> SyncResult:
    """Sync analogue of HTTP 403: this change is refused and nothing is written."""
    return SyncResult(
        id=existing.id,
        status="forbidden",
        message=message,
        server_revision=existing.revision,
        server_record=voter_to_public(existing),
    )


def _apply_one(db: Session, change: SyncChange, device_id: str, privileged: bool) -> SyncResult:
    try:
        incoming = check_client_timestamp(change.updated_at)
    except ValueError as exc:
        return SyncResult(id=str(change.id), status="validation_error", message=str(exc))

    record_id = str(change.id)
    existing = db.get(Voter, record_id)
    if change.deleted:
        # HTTP delete is admin-only; allowing it here would let a user delete and re-add a
        # voter to rewrite fields they may not edit.
        if not privileged and existing is not None and existing.deleted_at is None:
            return _forbidden(existing, "Your role cannot delete voters; ask an owner or admin.")
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

    # VoterId is the unique key: an active record keeps it for life. A tombstone holds only a
    # placeholder, so re-sending a deleted record may carry its (re)chosen VoterId.
    if existing is not None and existing.deleted_at is None and snapshot.voter_id != existing.voter_id:
        return SyncResult(
            id=record_id,
            status="validation_error",
            message=f"voter_id: {VOTER_ID_LOCKED_MESSAGE}",
            server_revision=existing.revision,
            server_record=voter_to_public(existing),
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

    return _merge_existing(db, existing, snapshot, incoming, device_id, change.base_revision, privileged)


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
    if existing.deleted_at is None and _stale_base(existing, change.base_revision):
        return _conflict(existing, "Server record changed after base_revision")
    stamp = dump_dt(incoming)
    strip_to_tombstone(existing)
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
    base_revision: int | None,
    privileged: bool,
) -> SyncResult:
    stored = parse_dt(existing.updated_at)
    if incoming < stored:
        return _conflict(existing, "Server record is newer")
    if incoming == stored:
        if _same_content(existing, snapshot, deleted=False):
            return SyncResult(
                id=existing.id,
                status="applied",
                message="Already stored",
                server_revision=existing.revision,
                server_record=voter_to_public(existing),
            )
        return _conflict(existing, "Server record has the same updated_at and different content")
    if _stale_base(existing, base_revision):
        return _conflict(existing, "Server record changed after base_revision")
    # Stale and replayed changes were handled above, so only a genuine edit reaches this
    # check. Values equal to the stored ones are ignored: only real changes are refused.
    if not privileged and existing.deleted_at is None:
        blocked = disallowed_changes(existing, {f: getattr(snapshot, f) for f in DATA_FIELDS})
        if blocked:
            return _forbidden(existing, forbidden_fields_message(blocked))
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
