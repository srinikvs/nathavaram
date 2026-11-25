"""Public Issues: lookup, create, list and search, status changes, notes, photos, and delete.

Every permission and status rule is enforced here, so the JSON API and the web pages get the
same answer. The routes only translate ServiceError into an HTTP status.

- Any signed-in role: look up a voter, create, list, search, view, add a note, add a photo.
- Status: only a manager or admin may move an issue beyond Initiated -> In Process
  (see app/issue_rules.py).
- Delete an issue and remove a photo: manager or admin.
- Delete a note: admin only. Notes themselves are never edited.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.errors import ServiceError
from app.issue_rules import (
    INITIAL_STATUS,
    ISSUE_STATUSES,
    MAX_PHOTOS_PER_ISSUE,
    NAME_MAX_LENGTH,
    NOTE_KIND,
    NOTE_MAX_LENGTH,
    SUBJECT_MAX_LENGTH,
    SYSTEM_KIND,
    ISSUE_MAX_LENGTH,
    check_transition,
    status_change_line,
)
from app.issue_schemas import IssueCreate, IssueSearch
from app.models import (
    PublicIssue,
    PublicIssueNote,
    PublicIssuePhoto,
    PublicIssueStatusEvent,
    User,
    Voter,
)
from app.permissions import can_delete_issue_note, can_manage_issues
from app.services import issue_photos
from app.services.voters import normalize_voter_id
from app.validation import canonical_mobile, dump_dt, require_text

PHOTO_CAP_MESSAGE = "This issue already has {cap} photos. Remove one before adding another."
PHOTO_NOT_FOUND_MESSAGE = "Photo not found"
ISSUE_NOT_FOUND_MESSAGE = "Issue not found"
MOBILE_VISIBLE_CHARS = 4
MASK_CHAR = "*"


def _now() -> str:
    return dump_dt(datetime.now(timezone.utc))


def _required(value: object, label: str, limit: int) -> str:
    try:
        text = require_text(value, label)
    except ValueError as exc:
        raise ServiceError(str(exc), 422, label.lower()) from exc
    if len(text) > limit:
        raise ServiceError(f"{label} must be at most {limit} characters", 422, label.lower())
    return text


def _mobile(value: object) -> str:
    try:
        mobile = canonical_mobile(value)
    except ValueError as exc:
        raise ServiceError(str(exc), 422, "mobile") from exc
    if mobile is None:
        raise ServiceError("Mobile is required", 422, "mobile")
    return mobile


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def display_name(voter: Voter) -> str:
    """First name then last name, the same order the rest of the app uses."""
    return " ".join(part for part in (voter.first_name, voter.last_name) if part).strip()


def find_active_voter(db: Session, voter_id: str | None) -> Voter | None:
    """The voter only if it is active: not soft-deleted and not hard-deleted."""
    if voter_id is None or not voter_id.strip():
        return None
    voter = db.get(Voter, normalize_voter_id(voter_id))
    if voter is None or voter.deleted_at is not None or voter.hard_deleted_at is not None:
        return None
    return voter


def lookup_voter(db: Session, voter_id: str | None) -> dict:
    """Fields for auto-fill. A deleted or unknown VoterId is reported as not found."""
    voter = find_active_voter(db, voter_id)
    if voter is None:
        return {"found": False, "voter_id": None, "name": "", "mobile": ""}
    return {
        "found": True,
        "voter_id": voter.voter_id,
        "name": display_name(voter),
        "mobile": (voter.mobile or "").strip(),
    }


def _contact_for(db: Session, data: IssueCreate) -> tuple[str | None, str, str]:
    """(voter_id, name, mobile) to store. An active voter fills name and mobile from its record;
    otherwise the typed name and mobile are used and voter_id is stored as NULL."""
    voter = find_active_voter(db, data.voter_id)
    if voter is None:
        return None, _required(data.name, "Name", NAME_MAX_LENGTH), _mobile(data.mobile)
    name = display_name(voter) or _required(data.name, "Name", NAME_MAX_LENGTH)
    stored_mobile = (voter.mobile or "").strip()
    mobile = stored_mobile or _mobile(data.mobile)
    return voter.voter_id, name, mobile


def create_issue(db: Session, data: IssueCreate, actor: User) -> PublicIssue:
    """Create an issue. It always starts as Initiated; the request cannot choose the status."""
    voter_id, name, mobile = _contact_for(db, data)
    subject = _required(data.subject, "Subject", SUBJECT_MAX_LENGTH)
    issue_text = _required(data.issue, "Issue", ISSUE_MAX_LENGTH)
    now = _now()
    row = PublicIssue(
        voter_id=voter_id,
        name=name,
        mobile=mobile,
        subject=subject,
        issue=issue_text,
        status=INITIAL_STATUS,
        created_at=now,
        updated_at=now,
        created_by=actor.username,
        updated_by=actor.username,
    )
    db.add(row)
    db.flush()
    db.add(
        PublicIssueStatusEvent(
            issue_id=row.id,
            from_status=None,
            to_status=INITIAL_STATUS,
            changed_by=actor.username,
            changed_at=now,
        )
    )
    db.commit()
    db.refresh(row)
    return row


def get_issue(db: Session, issue_id: int) -> PublicIssue:
    row = db.get(PublicIssue, issue_id)
    if row is None:
        raise ServiceError(ISSUE_NOT_FOUND_MESSAGE, 404)
    return row


def status_counts(db: Session) -> dict[str, int]:
    """Count of every issue per status, across all issues (not the filtered list)."""
    counts = {status: 0 for status in ISSUE_STATUSES}
    rows = db.execute(select(PublicIssue.status, func.count()).group_by(PublicIssue.status)).all()
    for status, total in rows:
        counts[status] = int(total)
    return counts


def search_issues(db: Session, query: IssueSearch) -> tuple[list[PublicIssue], int]:
    """Filter by status and search Voter ID, Name and Subject. Oldest first, so S No is stable."""
    status = (query.status or "").strip()
    if status and status not in ISSUE_STATUSES:
        raise ServiceError(f"Status must be one of: {', '.join(ISSUE_STATUSES)}", 422, "status")
    stmt = select(PublicIssue)
    if status:
        stmt = stmt.where(PublicIssue.status == status)
    text = (query.q or "").strip()
    if text:
        like = _like(text)
        stmt = stmt.where(
            or_(
                PublicIssue.voter_id.ilike(like, escape="\\"),
                PublicIssue.name.ilike(like, escape="\\"),
                PublicIssue.subject.ilike(like, escape="\\"),
            )
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    offset = (query.page - 1) * query.page_size
    rows = db.scalars(stmt.order_by(PublicIssue.id).offset(offset).limit(query.page_size)).all()
    return list(rows), int(total)


def mask_issue_mobile(value: str | None) -> str | None:
    """Hide every character of an issue mobile except the last four, for display and JSON only.

    A value shorter than four characters is fully masked, so a short number is never shown in
    full. None and the empty string are returned unchanged. The stored value is not touched.
    """
    if not value:
        return value
    visible = value[-MOBILE_VISIBLE_CHARS:] if len(value) >= MOBILE_VISIBLE_CHARS else ""
    return MASK_CHAR * (len(value) - len(visible)) + visible


def issue_payload(issue: PublicIssue) -> dict:
    return {
        "id": issue.id,
        "voter_id": issue.voter_id,
        "name": issue.name,
        "mobile": mask_issue_mobile(issue.mobile),
        "subject": issue.subject,
        "issue": issue.issue,
        "status": issue.status,
        "created_at": issue.created_at,
        "updated_at": issue.updated_at,
        "created_by": issue.created_by,
        "updated_by": issue.updated_by,
    }


def photo_payload(photo: PublicIssuePhoto) -> dict:
    return {
        "id": photo.id,
        "width": photo.width,
        "height": photo.height,
        "uploaded_by": photo.uploaded_by,
        "uploaded_at": photo.uploaded_at,
        "url": f"/api/issues/photos/{photo.id}/file",
    }


def note_payload(note: PublicIssueNote) -> dict:
    return {
        "id": note.id,
        "kind": note.kind,
        "body": note.body,
        "author": note.author,
        "created_at": note.created_at,
    }


def issue_detail(db: Session, issue_id: int) -> dict:
    issue = get_issue(db, issue_id)
    photos = db.scalars(
        select(PublicIssuePhoto).where(PublicIssuePhoto.issue_id == issue.id).order_by(PublicIssuePhoto.id)
    ).all()
    notes = db.scalars(
        select(PublicIssueNote).where(PublicIssueNote.issue_id == issue.id).order_by(PublicIssueNote.id)
    ).all()
    history = db.scalars(
        select(PublicIssueStatusEvent)
        .where(PublicIssueStatusEvent.issue_id == issue.id)
        .order_by(PublicIssueStatusEvent.id)
    ).all()
    return {
        "issue": issue_payload(issue),
        "photos": [photo_payload(photo) for photo in photos],
        "notes": [note_payload(note) for note in notes],
        "history": [
            {
                "from_status": event.from_status,
                "to_status": event.to_status,
                "changed_by": event.changed_by,
                "changed_at": event.changed_at,
            }
            for event in history
        ],
    }


def change_status(db: Session, issue_id: int, target: str, actor: User) -> PublicIssue:
    """Move an issue to `target` if this actor may. Records history and a notes system line."""
    issue = get_issue(db, issue_id)
    previous = issue.status
    check_transition(can_manage_issues(actor), previous, target)
    now = _now()
    issue.status = target
    issue.updated_at = now
    issue.updated_by = actor.username
    db.add(
        PublicIssueStatusEvent(
            issue_id=issue.id,
            from_status=previous,
            to_status=target,
            changed_by=actor.username,
            changed_at=now,
        )
    )
    db.add(
        PublicIssueNote(
            issue_id=issue.id,
            kind=SYSTEM_KIND,
            body=status_change_line(previous, target, actor.username),
            author=actor.username,
            created_at=now,
        )
    )
    db.commit()
    db.refresh(issue)
    return issue


def add_note(db: Session, issue_id: int, text: str, actor: User) -> PublicIssueNote:
    get_issue(db, issue_id)
    body = _required(text, "Note", NOTE_MAX_LENGTH)
    note = PublicIssueNote(
        issue_id=issue_id,
        kind=NOTE_KIND,
        body=body,
        author=actor.username,
        created_at=_now(),
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


def delete_note(db: Session, issue_id: int, note_id: int, actor: User) -> None:
    if not can_delete_issue_note(actor):
        raise ServiceError("Only an admin can delete a note", 403)
    note = db.get(PublicIssueNote, note_id)
    if note is None or note.issue_id != issue_id:
        raise ServiceError("Note not found", 404)
    if note.kind != NOTE_KIND:
        raise ServiceError("Status lines in the notes thread cannot be deleted", 409)
    db.delete(note)
    db.commit()


def add_photo(
    db: Session, issue_id: int, data: bytes, actor: User, photo_dir: Path
) -> PublicIssuePhoto:
    """Any signed-in role may add a photo, up to MAX_PHOTOS_PER_ISSUE per issue."""
    get_issue(db, issue_id)
    count = db.scalar(
        select(func.count()).select_from(PublicIssuePhoto).where(PublicIssuePhoto.issue_id == issue_id)
    ) or 0
    if count >= MAX_PHOTOS_PER_ISSUE:
        raise ServiceError(PHOTO_CAP_MESSAGE.format(cap=MAX_PHOTOS_PER_ISSUE), 409, "photos")
    stored = issue_photos.store_photo(data, photo_dir, issue_id)
    row = PublicIssuePhoto(
        issue_id=issue_id,
        storage_name=stored.storage_name,
        width=stored.width,
        height=stored.height,
        uploaded_by=actor.username,
        uploaded_at=_now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_photo(db: Session, photo_id: int) -> PublicIssuePhoto:
    photo = db.get(PublicIssuePhoto, photo_id)
    if photo is None:
        raise ServiceError(PHOTO_NOT_FOUND_MESSAGE, 404)
    return photo


def remove_photo(
    db: Session, issue_id: int, photo_id: int, actor: User, photo_dir: Path
) -> None:
    if not can_manage_issues(actor):
        raise ServiceError("Only a manager or admin can remove a photo", 403)
    photo = get_photo(db, photo_id)
    if photo.issue_id != issue_id:
        raise ServiceError(PHOTO_NOT_FOUND_MESSAGE, 404)
    storage_name = photo.storage_name
    db.delete(photo)
    db.commit()
    issue_photos.delete_photo_file(photo_dir, storage_name)


def delete_issue(db: Session, issue_id: int, actor: User, photo_dir: Path) -> None:
    """Manager or admin only. Removes the issue, its notes, history, photo rows and photo files."""
    if not can_manage_issues(actor):
        raise ServiceError("Only a manager or admin can delete an issue", 403)
    issue = get_issue(db, issue_id)
    storage_names = list(
        db.scalars(
            select(PublicIssuePhoto.storage_name).where(PublicIssuePhoto.issue_id == issue.id)
        ).all()
    )
    db.execute(delete(PublicIssuePhoto).where(PublicIssuePhoto.issue_id == issue.id))
    db.execute(delete(PublicIssueNote).where(PublicIssueNote.issue_id == issue.id))
    db.execute(delete(PublicIssueStatusEvent).where(PublicIssueStatusEvent.issue_id == issue.id))
    db.delete(issue)
    db.commit()
    for storage_name in storage_names:
        issue_photos.delete_photo_file(photo_dir, storage_name)
