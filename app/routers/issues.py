"""Public Issues JSON API. Each route only translates ServiceError; the rules live in the service.

Signed-in roles may look up, create, search, view, add notes and add photos. Deleting an
issue (manager or admin) is enforced with require_manager. Photo and note permissions are
checked in app/services/issues.py. Filters and VoterIds travel in POST bodies, never in URLs.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_manager, require_user
from app.errors import ServiceError
from app.issue_schemas import IssueCreate, IssueNoteCreate, IssueSearch, IssueStatusChange, VoterLookup
from app.models import User
from app.services import issue_photos
from app.services.issues import (
    add_note,
    add_photo,
    change_status,
    create_issue,
    delete_issue,
    delete_note,
    get_photo,
    issue_detail,
    issue_payload,
    lookup_voter,
    note_payload,
    photo_payload,
    remove_photo,
    search_issues,
    status_counts,
)

router = APIRouter(prefix="/api/issues", tags=["issues"])


def _photo_dir(request: Request) -> Path:
    return Path(request.app.state.settings.issue_photo_dir)


def _run(call, *args, **kwargs):
    """Call a service function and turn its ServiceError into an HTTP error."""
    try:
        return call(*args, **kwargs)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/voter-lookup")
def voter_lookup(
    body: VoterLookup, db: Session = Depends(get_db), user: User = Depends(require_user)
) -> dict:
    return lookup_voter(db, body.voter_id)


@router.post("/search")
def search(
    body: IssueSearch, db: Session = Depends(get_db), user: User = Depends(require_user)
) -> dict:
    rows, total = _run(search_issues, db, body)
    return {
        "items": [issue_payload(row) for row in rows],
        "total": total,
        "page": body.page,
        "page_size": body.page_size,
        "counts": status_counts(db),
    }


@router.post("", status_code=201)
def create(
    body: IssueCreate, db: Session = Depends(get_db), user: User = Depends(require_user)
) -> dict:
    return issue_payload(_run(create_issue, db, body, user))


@router.get("/{issue_id}")
def detail(issue_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)) -> dict:
    return _run(issue_detail, db, issue_id)


@router.post("/{issue_id}/status")
def set_status(
    issue_id: int,
    body: IssueStatusChange,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    return issue_payload(_run(change_status, db, issue_id, body.status, user))


@router.delete("/{issue_id}")
def remove_issue(
    issue_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_manager),
) -> dict:
    _run(delete_issue, db, issue_id, user, _photo_dir(request))
    return {"deleted": issue_id}


@router.post("/{issue_id}/photos", status_code=201)
async def upload_photo(
    issue_id: int,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    data = await file.read(issue_photos.MAX_UPLOAD_BYTES + 1)
    return photo_payload(_run(add_photo, db, issue_id, data, user, _photo_dir(request)))


@router.delete("/{issue_id}/photos/{photo_id}")
def remove_photo_route(
    issue_id: int,
    photo_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    _run(remove_photo, db, issue_id, photo_id, user, _photo_dir(request))
    return {"deleted": photo_id}


@router.get("/photos/{photo_id}/file")
def photo_file(
    photo_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> FileResponse:
    photo = _run(get_photo, db, photo_id)
    path = _run(issue_photos.photo_path, _photo_dir(request), photo.storage_name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Photo not found")
    return FileResponse(path, media_type="image/jpeg")


@router.post("/{issue_id}/notes", status_code=201)
def create_note(
    issue_id: int,
    body: IssueNoteCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    return note_payload(_run(add_note, db, issue_id, body.text, user))


@router.delete("/{issue_id}/notes/{note_id}")
def remove_note(
    issue_id: int,
    note_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    _run(delete_note, db, issue_id, note_id, user)
    return {"deleted": note_id}
