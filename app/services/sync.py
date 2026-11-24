"""Pull changes since a revision and push batched offline edits.

Conflict policy (also documented in docs/SYNC.md):
- A change that carries base_revision is a conflict when the server row has moved past it.
- Without base_revision (or when it is current): last-write-wins by updated_at.
- Every change runs in its own savepoint, so one rejected change never rolls back the batch.
- The server assigns a new revision only when a change is stored.
- Equal timestamps with the same content are an idempotent replay.
- Equal timestamps with different content keep the server row.
- voter_id is the primary key: a change for an existing voter_id is a merge, never a clash.
- Soft delete (deleted=true from any role) keeps every field and sets deleted_at.
  A newer non-delete change for a soft-deleted row is a restore: only manager and admin may
  restore. Hard delete is not available through sync.
- A hard-deleted row (personal fields stripped, hard_deleted_at set) cannot be restored. A
  newer non-delete change for it creates a new record under the same voter_id.
- A change whose voter_id is not valid is reported as validation_error and not applied.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.constants import DATA_FIELDS, SYNC_BATCH_LIMIT
from app.errors import ServiceError
from app.models import User, Voter
from app.permissions import can_edit_all_fields, can_restore
from app.schemas import (
    SyncChange,
    SyncPullResponse,
    SyncPushRequest,
    SyncPushResponse,
    SyncResult,
    VoterWrite,
)
from app.services.revision import allocate_revision, current_revision
from app.services.voters import (
    disallowed_changes,
    forbidden_fields_message,
    normalize_voter_id,
    voter_to_public,
)
from app.validation import canonical_voter_id, check_client_timestamp, dump_dt, parse_dt

RESTORE_FORBIDDEN_MESSAGE = (
    "Your role cannot restore deleted voters; ask a manager or admin to restore this voter."
)


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


def push_changes(db: Session, body: SyncPushRequest, user: User) -> SyncPushResponse:
    """Apply a device batch as `user`. Field and restore rules follow the user's role."""
    if len(body.changes) > SYNC_BATCH_LIMIT:
        raise ServiceError(f"A sync batch can contain at most {SYNC_BATCH_LIMIT} changes", 422)
    results: list[SyncResult] = []
    for change in body.changes:
        results.append(_apply_guarded(db, change, body.device_id, user))
    db.commit()
    return SyncPushResponse(
        server_time=_server_time(),
        server_revision=current_revision(db),
        results=results,
    )


def _apply_guarded(db: Session, change: SyncChange, device_id: str, user: User) -> SyncResult:
    """Apply one change inside a savepoint; a database error rejects only this change."""
    try:
        with db.begin_nested():
            return _apply_one(db, change, device_id, user)
    except IntegrityError:
        # Two writers inserted the same voter_id at once; the other write won.
        key = normalize_voter_id(change.voter_id)
        winner = db.get(Voter, key)
        return SyncResult(
            voter_id=key,
            status="conflict",
            message="Another change for this VoterId was stored first",
            server_revision=winner.revision if winner is not None else None,
            server_record=voter_to_public(winner) if winner is not None else None,
        )


def _stale_base(existing: Voter, base_revision: int | None) -> bool:
    return base_revision is not None and existing.revision > base_revision


def _forbidden(existing: Voter, message: str) -> SyncResult:
    """Sync analogue of HTTP 403: this change is refused and nothing is written."""
    return SyncResult(
        voter_id=existing.voter_id,
        status="forbidden",
        message=message,
        server_revision=existing.revision,
        server_record=voter_to_public(existing),
    )


def _apply_one(db: Session, change: SyncChange, device_id: str, user: User) -> SyncResult:
    try:
        voter_key = canonical_voter_id(change.voter_id)
    except ValueError as exc:
        return SyncResult(
            voter_id=normalize_voter_id(change.voter_id),
            status="validation_error",
            message=f"voter_id: {exc}",
        )
    try:
        incoming = check_client_timestamp(change.updated_at)
    except ValueError as exc:
        return SyncResult(voter_id=voter_key, status="validation_error", message=str(exc))

    existing = db.get(Voter, voter_key)
    if change.deleted:
        return _apply_delete(db, voter_key, existing, incoming, device_id, change.base_revision)

    try:
        snapshot = VoterWrite.model_validate(
            change.model_dump(exclude={"deleted", "base_revision", "created_at", "updated_at"})
        )
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = first.get("loc") or ()
        field = str(loc[-1]) if loc else "record"
        return SyncResult(
            voter_id=voter_key,
            status="validation_error",
            message=f"{field}: {first['msg']}",
        )

    if existing is None:
        created = dump_dt(incoming)
        if change.created_at is not None:
            try:
                created = dump_dt(check_client_timestamp(change.created_at))
            except ValueError as exc:
                return SyncResult(voter_id=voter_key, status="validation_error", message=str(exc))
        voter = Voter(
            voter_id=voter_key,
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
            voter_id=voter_key,
            status="applied",
            server_revision=voter.revision,
            server_record=voter_to_public(voter),
        )

    return _merge_existing(db, existing, snapshot, incoming, device_id, change.base_revision, user)


def _apply_delete(
    db: Session,
    record_id: str,
    existing: Voter | None,
    incoming: datetime,
    device_id: str,
    base_revision: int | None,
) -> SyncResult:
    """Soft delete: keep every field, set deleted_at. Any role may do this."""
    if existing is None:
        return SyncResult(
            voter_id=record_id,
            status="applied",
            message="No server record; delete ignored",
            server_revision=None,
            server_record=None,
        )
    if existing.hard_deleted_at is not None:
        return SyncResult(
            voter_id=record_id,
            status="applied",
            message="Already permanently deleted",
            server_revision=existing.revision,
            server_record=voter_to_public(existing),
        )
    stored = parse_dt(existing.updated_at)
    if incoming < stored:
        return _conflict(existing, "Server record is newer")
    if incoming == stored and existing.deleted_at is not None:
        return SyncResult(
            voter_id=record_id,
            status="applied",
            message="Already deleted",
            server_revision=existing.revision,
            server_record=voter_to_public(existing),
        )
    if incoming == stored and existing.deleted_at is None:
        return _conflict(existing, "Server record has the same updated_at and is still active")
    if existing.deleted_at is None and _stale_base(existing, base_revision):
        return _conflict(existing, "Server record changed after base_revision")
    stamp = dump_dt(incoming)
    existing.deleted_at = stamp
    existing.updated_at = stamp
    existing.source_id = device_id
    existing.revision = allocate_revision(db)
    db.flush()
    return SyncResult(
        voter_id=record_id,
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
    user: User,
) -> SyncResult:
    stored = parse_dt(existing.updated_at)
    if incoming < stored:
        return _conflict(existing, "Server record is newer")
    if incoming == stored:
        if _same_content(existing, snapshot, deleted=False):
            return SyncResult(
                voter_id=existing.voter_id,
                status="applied",
                message="Already stored",
                server_revision=existing.revision,
                server_record=voter_to_public(existing),
            )
        return _conflict(existing, "Server record has the same updated_at and different content")
    if _stale_base(existing, base_revision):
        return _conflict(existing, "Server record changed after base_revision")

    # Stale and replayed changes were handled above, so only a genuine edit reaches this point.
    if existing.hard_deleted_at is not None:
        # Personal data is gone and cannot come back: this is a new record under the same key.
        existing.created_at = dump_dt(incoming)
    else:
        if existing.deleted_at is not None:
            if not can_restore(user):
                return _forbidden(existing, RESTORE_FORBIDDEN_MESSAGE)
            existing.created_at = dump_dt(incoming)  # a restored row is a new voter, as on create
        if not can_edit_all_fields(user):
            # Values equal to the stored ones are ignored: only real changes are refused.
            blocked = disallowed_changes(existing, {f: getattr(snapshot, f) for f in DATA_FIELDS})
            if blocked:
                return _forbidden(existing, forbidden_fields_message(blocked))
    _write_snapshot(existing, snapshot)
    existing.deleted_at = None
    existing.hard_deleted_at = None
    existing.updated_at = dump_dt(incoming)
    existing.source_id = device_id
    existing.revision = allocate_revision(db)
    db.flush()
    return SyncResult(
        voter_id=existing.voter_id,
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


def _conflict(existing: Voter, message: str) -> SyncResult:
    return SyncResult(
        voter_id=existing.voter_id,
        status="conflict",
        message=message,
        server_revision=existing.revision,
        server_record=voter_to_public(existing),
    )
