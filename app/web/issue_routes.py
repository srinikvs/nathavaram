"""Public Issues web pages. Forms post with the session's csrf_token (enforced by csrf_guard).

The pages call the same service functions as the JSON API, so permissions and transition
rules are identical. A ServiceError re-renders the page with the message and its HTTP status.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.database import get_db
from app.errors import ServiceError
from app.issue_rules import ISSUE_STATUSES, MAX_PHOTOS_PER_ISSUE, status_options
from app.issue_schemas import IssueCreate, IssueSearch
from app.permissions import can_delete_issue_note, can_manage_issues, is_admin
from app.services import issue_photos
from app.services.issues import (
    add_note,
    add_photo,
    change_status,
    create_issue,
    delete_issue,
    delete_note,
    issue_detail,
    issue_payload,
    remove_photo,
    search_issues,
    status_counts,
)
from app.web.routes import web_user
from app.web.templates import render

router = APIRouter()

DONE_NOTICES = {
    "created": "Issue added. It starts as Initiated.",
    "status": "Status updated. The change is in the notes thread.",
    "photo": "Photo added.",
    "photo_removed": "Photo removed.",
    "note": "Note added.",
    "note_deleted": "Note deleted.",
    "deleted": "Issue deleted, with its photos and notes.",
}
FORM_FIELDS = ("voter_id", "name", "mobile", "subject", "issue")
PHOTO_FIELD = "photo"
NEW_ISSUE_PHOTOS_FIELD = "photos"
NO_PHOTO_MESSAGE = "Choose a photo to upload (JPG, PNG or HEIC)."
TOO_MANY_PHOTOS_MESSAGE = "You can add up to {cap} photos to an issue."


def _photo_dir(request: Request) -> Path:
    return Path(request.app.state.settings.issue_photo_dir)


def _first_error(exc: ValidationError) -> str:
    message = exc.errors()[0]["msg"]
    prefix = "Value error, "
    return message[len(prefix) :] if message.startswith(prefix) else message


def _list_page(
    request: Request,
    db: Session,
    user,
    query: IssueSearch,
    error: str | None = None,
    status_code: int = 200,
):
    try:
        rows, total = search_issues(db, query)
    except ServiceError as exc:
        return _list_page(request, db, user, IssueSearch(), exc.message, exc.status_code)
    can_manage = can_manage_issues(user)
    items = [
        {"issue": issue_payload(row), "options": status_options(can_manage, row.status)}
        for row in rows
    ]
    return render(
        request,
        "issues/list.html",
        {
            "user": user,
            "items": items,
            "total": total,
            "query": query,
            "counts": status_counts(db),
            "statuses": ISSUE_STATUSES,
            "error": error,
            "notice": DONE_NOTICES.get(request.query_params.get("done", ""), ""),
        },
        status_code=status_code,
    )


def _detail_or_missing(
    request: Request, db: Session, user, issue_id: int, error: str | None = None, status_code: int = 200
):
    try:
        detail = issue_detail(db, issue_id)
    except ServiceError:
        return render(request, "404.html", {"user": user}, status_code=404)
    can_manage = can_manage_issues(user)
    current = detail["issue"]["status"]
    return render(
        request,
        "issues/detail.html",
        {
            "user": user,
            "detail": detail,
            "options": status_options(can_manage, current),
            "can_manage": can_manage,
            "can_delete_note": can_delete_issue_note(user),
            "admin": is_admin(user),
            "max_photos": MAX_PHOTOS_PER_ISSUE,
            "error": error,
            "notice": DONE_NOTICES.get(request.query_params.get("done", ""), ""),
        },
        status_code=status_code,
    )


def _form_page(request: Request, user, values: dict, errors: list[str], status_code: int = 200):
    return render(
        request,
        "issues/form.html",
        {
            "user": user,
            "values": values,
            "errors": errors,
            "max_photos": MAX_PHOTOS_PER_ISSUE,
        },
        status_code=status_code,
    )


async def _new_issue_photos(form) -> list[bytes]:
    """Read the photos chosen on the add form. Empty file parts (no file picked) are ignored."""
    uploads = [
        item
        for item in form.getlist(NEW_ISSUE_PHOTOS_FIELD)
        if isinstance(item, UploadFile) and item.filename
    ]
    if len(uploads) > MAX_PHOTOS_PER_ISSUE:
        raise ServiceError(TOO_MANY_PHOTOS_MESSAGE.format(cap=MAX_PHOTOS_PER_ISSUE), 422, "photos")
    payloads = [await upload.read(issue_photos.MAX_UPLOAD_BYTES + 1) for upload in uploads]
    return [payload for payload in payloads if payload]


def _search_from_form(form) -> IssueSearch:
    keys = ("q", "status")
    values = {key: str(form.get(key)) for key in keys if form.get(key) not in (None, "")}
    return IssueSearch.model_validate(values)


@router.get("/issues")
def issues_page(request: Request, db: Session = Depends(get_db), user=Depends(web_user)):
    status = request.query_params.get("status") or None
    try:
        query = IssueSearch(status=status)
    except ValidationError as exc:
        return _list_page(request, db, user, IssueSearch(), _first_error(exc), 422)
    return _list_page(request, db, user, query)


@router.post("/issues/search")
async def issues_search(request: Request, db: Session = Depends(get_db), user=Depends(web_user)):
    form = await request.form()
    try:
        query = _search_from_form(form)
    except ValidationError as exc:
        return _list_page(request, db, user, IssueSearch(), _first_error(exc), 422)
    return _list_page(request, db, user, query)


@router.get("/issues/new")
def new_issue_form(request: Request, user=Depends(web_user)):
    return _form_page(request, user, {}, [])


@router.post("/issues/new")
async def new_issue_submit(request: Request, db: Session = Depends(get_db), user=Depends(web_user)):
    form = await request.form()
    values = {field: str(form.get(field) or "") for field in FORM_FIELDS}
    try:
        data = IssueCreate(**{field: value or None for field, value in values.items()})
        photos = await _new_issue_photos(form)
        # Check every photo before the issue exists, so a bad photo never leaves an orphan issue.
        for payload in photos:
            issue_photos.normalize_image(payload)
        issue = create_issue(db, data, user)
        for payload in photos:
            add_photo(db, issue.id, payload, user, _photo_dir(request))
    except ValidationError as exc:
        return _form_page(request, user, values, [_first_error(exc)], 422)
    except ServiceError as exc:
        return _form_page(request, user, values, [exc.message], exc.status_code)
    return RedirectResponse(f"/issues/{issue.id}?done=created", status_code=303)


@router.get("/issues/{issue_id}")
def issue_page(issue_id: int, request: Request, db: Session = Depends(get_db), user=Depends(web_user)):
    return _detail_or_missing(request, db, user, issue_id)


@router.post("/issues/{issue_id}/status")
async def status_submit(
    issue_id: int, request: Request, db: Session = Depends(get_db), user=Depends(web_user)
):
    form = await request.form()
    try:
        change_status(db, issue_id, str(form.get("status") or ""), user)
    except ServiceError as exc:
        return _detail_or_missing(request, db, user, issue_id, exc.message, exc.status_code)
    return RedirectResponse(f"/issues/{issue_id}?done=status", status_code=303)


@router.post("/issues/{issue_id}/delete")
def delete_submit(issue_id: int, request: Request, db: Session = Depends(get_db), user=Depends(web_user)):
    try:
        delete_issue(db, issue_id, user, _photo_dir(request))
    except ServiceError as exc:
        return _detail_or_missing(request, db, user, issue_id, exc.message, exc.status_code)
    return RedirectResponse("/issues?done=deleted", status_code=303)


@router.post("/issues/{issue_id}/photos")
async def photo_submit(
    issue_id: int, request: Request, db: Session = Depends(get_db), user=Depends(web_user)
):
    form = await request.form()
    upload = form.get(PHOTO_FIELD)
    if not isinstance(upload, UploadFile):
        return _detail_or_missing(request, db, user, issue_id, NO_PHOTO_MESSAGE, 422)
    data = await upload.read(issue_photos.MAX_UPLOAD_BYTES + 1)
    try:
        add_photo(db, issue_id, data, user, _photo_dir(request))
    except ServiceError as exc:
        return _detail_or_missing(request, db, user, issue_id, exc.message, exc.status_code)
    return RedirectResponse(f"/issues/{issue_id}?done=photo", status_code=303)


@router.post("/issues/{issue_id}/photos/{photo_id}/delete")
def photo_remove_submit(
    issue_id: int,
    photo_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(web_user),
):
    try:
        remove_photo(db, issue_id, photo_id, user, _photo_dir(request))
    except ServiceError as exc:
        return _detail_or_missing(request, db, user, issue_id, exc.message, exc.status_code)
    return RedirectResponse(f"/issues/{issue_id}?done=photo_removed", status_code=303)


@router.post("/issues/{issue_id}/notes")
async def note_submit(issue_id: int, request: Request, db: Session = Depends(get_db), user=Depends(web_user)):
    form = await request.form()
    try:
        add_note(db, issue_id, str(form.get("text") or ""), user)
    except ServiceError as exc:
        return _detail_or_missing(request, db, user, issue_id, exc.message, exc.status_code)
    return RedirectResponse(f"/issues/{issue_id}?done=note", status_code=303)


@router.post("/issues/{issue_id}/notes/{note_id}/delete")
def note_delete_submit(
    issue_id: int, note_id: int, request: Request, db: Session = Depends(get_db), user=Depends(web_user)
):
    try:
        delete_note(db, issue_id, note_id, user)
    except ServiceError as exc:
        return _detail_or_missing(request, db, user, issue_id, exc.message, exc.status_code)
    return RedirectResponse(f"/issues/{issue_id}?done=note_deleted", status_code=303)
