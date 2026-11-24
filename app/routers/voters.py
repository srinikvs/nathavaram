"""Voter CRUD, search, CSV import, and CSV/Excel export.

Roles (see app/permissions.py): any signed-in user may search, create, view, edit
USER_EDITABLE_FIELDS (403 otherwise) and soft delete. A manager also sees and restores deleted
voters and may hard delete them. An admin may edit every field except VoterId. Import, export
and Master Data stay admin-only.

Search filters can carry personal data (mobile, VoterId, caste, free text), so they are
accepted only in a POST body. GET /api/voters takes pagination only.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.constants import IMPORT_BYTE_LIMIT, SEARCH_FILTER_FIELDS
from app.database import get_db
from app.deps import require_admin, require_manager, require_user, source_for
from app.errors import ServiceError
from app.models import User
from app.permissions import can_edit_all_fields, can_soft_delete
from app.schemas import (
    BulkResult,
    BulkVoterDelete,
    BulkVoterEdit,
    BulkVoterHardDelete,
    VoterPage,
    VoterPatch,
    VoterPublic,
    VoterSearch,
    VoterWrite,
)
from app.services.bulk import (
    bulk_delete_voters,
    bulk_edit_voters,
    bulk_hard_delete_voters,
    normalize_ids,
)
from app.services.impex import ImportRejected, import_csv, import_upload, voters_to_csv, voters_to_xlsx
from app.services.voters import (
    create_voter,
    export_voters,
    get_active,
    hard_delete,
    list_deleted,
    restore,
    search_voters,
    soft_delete,
    update_voter,
    voter_to_public,
)

router = APIRouter(prefix="/api/voters", tags=["voters"])


def _page_only(request: Request) -> VoterSearch:
    """Build a query from the URL. Filters are refused so they never reach access logs."""
    refused = sorted(set(request.query_params) & set(SEARCH_FILTER_FIELDS))
    if refused:
        raise HTTPException(
            status_code=422,
            detail="Search filters are not accepted in the URL. POST them to /api/voters/search.",
        )
    allowed = {key: value for key, value in request.query_params.items() if key in {"page", "page_size"}}
    try:
        return VoterSearch.model_validate(allowed)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()[0]["msg"]) from exc


def _page(db: Session, query: VoterSearch) -> VoterPage:
    rows, total = search_voters(db, query)
    return VoterPage(
        items=[voter_to_public(row) for row in rows],
        total=total,
        page=query.page,
        page_size=query.page_size,
    )


@router.get("", response_model=VoterPage)
def list_voters(
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_user),
) -> VoterPage:
    return _page(db, _page_only(request))


@router.post("/search", response_model=VoterPage)
def post_search(
    body: VoterSearch,
    db: Session = Depends(get_db),
    _: User = Depends(require_user),
) -> VoterPage:
    return _page(db, body)


@router.post("", response_model=VoterPublic, status_code=201)
def post_voter(
    body: VoterWrite,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> VoterPublic:
    try:
        voter = create_voter(db, body, source_for(user), user)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return voter_to_public(voter)


@router.get("/deleted", response_model=VoterPage)
def list_deleted_voters(
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_manager),
) -> VoterPage:
    """Soft-deleted voters that are not hard-deleted yet. Manager and admin only."""
    query = _page_only(request)
    rows, total = list_deleted(db, query)
    return VoterPage(
        items=[voter_to_public(row) for row in rows],
        total=total,
        page=query.page,
        page_size=query.page_size,
    )


@router.post("/bulk-edit", response_model=BulkResult)
def post_bulk_edit(
    body: BulkVoterEdit,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> BulkResult:
    try:
        voters = bulk_edit_voters(
            db,
            body.voter_ids,
            body.values,
            source_for(user),
            all_fields=can_edit_all_fields(user),
        )
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return BulkResult(count=len(voters), voter_ids=[v.voter_id for v in voters])


@router.post("/bulk-delete", response_model=BulkResult)
def post_bulk_delete(
    body: BulkVoterDelete,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> BulkResult:
    """Soft delete for every role that may soft delete. Nothing is removed from the database."""
    if not can_soft_delete(user):
        raise HTTPException(status_code=403, detail="Your role cannot delete voters")
    try:
        ids = normalize_ids(body.voter_ids)
        count = bulk_delete_voters(db, ids, source_for(user), confirm=body.confirm)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return BulkResult(count=count, voter_ids=ids)


@router.post("/bulk-hard-delete", response_model=BulkResult)
def post_bulk_hard_delete(
    body: BulkVoterHardDelete,
    db: Session = Depends(get_db),
    user: User = Depends(require_manager),
) -> BulkResult:
    """Permanently remove personal data from soft-deleted voters. Manager and admin only."""
    try:
        ids = normalize_ids(body.voter_ids)
        count = bulk_hard_delete_voters(db, ids, source_for(user), confirm=body.confirm)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return BulkResult(count=count, voter_ids=ids)


@router.post("/export.csv")
def export_csv(
    body: VoterSearch | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> Response:
    payload = voters_to_csv(export_voters(db, body or VoterSearch())).encode("utf-8-sig")
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="nathavaram-voters.csv"'},
    )


@router.post("/export.xlsx")
def export_xlsx(
    body: VoterSearch | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> Response:
    payload = voters_to_xlsx(export_voters(db, body or VoterSearch()))
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="nathavaram-voters.xlsx"'},
    )


@router.post("/import.csv")
def post_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    content = file.file.read(IMPORT_BYTE_LIMIT + 1)
    if len(content) > IMPORT_BYTE_LIMIT:
        raise HTTPException(status_code=413, detail="CSV larger than 5 MB")
    try:
        return import_csv(db, content, source_for(user))
    except ImportRejected as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc


@router.post("/import.xlsx")
def post_import_xlsx(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    content = file.file.read(IMPORT_BYTE_LIMIT + 1)
    if len(content) > IMPORT_BYTE_LIMIT:
        raise HTTPException(status_code=413, detail="Excel file larger than 5 MB")
    try:
        return import_upload(db, file.filename or "", content, source_for(user), only="xlsx")
    except ImportRejected as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc


@router.get("/{voter_id}", response_model=VoterPublic)
def get_voter(
    voter_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_user),
) -> VoterPublic:
    try:
        return voter_to_public(get_active(db, voter_id))
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.patch("/{voter_id}", response_model=VoterPublic)
def patch_voter(
    voter_id: str,
    body: VoterPatch,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> VoterPublic:
    try:
        updated = update_voter(
            db, voter_id, body, source_for(user), all_fields=can_edit_all_fields(user)
        )
        return voter_to_public(updated)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.delete("/{voter_id}", response_model=VoterPublic)
def delete_voter(
    voter_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> VoterPublic:
    """Soft delete (any role). Every field is kept; a manager or admin can restore it."""
    if not can_soft_delete(user):
        raise HTTPException(status_code=403, detail="Your role cannot delete voters")
    try:
        return voter_to_public(soft_delete(db, voter_id, source_for(user)))
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/{voter_id}/restore", response_model=VoterPublic)
def restore_voter(
    voter_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_manager),
) -> VoterPublic:
    try:
        return voter_to_public(restore(db, voter_id, source_for(user)))
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/{voter_id}/hard-delete", response_model=VoterPublic)
def hard_delete_voter_route(
    voter_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_manager),
) -> VoterPublic:
    """Permanently remove personal data. Only for soft-deleted voters; the row stays for sync."""
    try:
        return voter_to_public(hard_delete(db, voter_id, source_for(user)))
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
