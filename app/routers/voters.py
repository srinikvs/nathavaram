"""Voter CRUD, search, CSV import, and CSV/Excel export."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.constants import IMPORT_BYTE_LIMIT
from app.database import get_db
from app.deps import require_user, source_for
from app.errors import ServiceError
from app.models import User
from app.schemas import VoterPage, VoterPatch, VoterPublic, VoterSearch, VoterWrite
from app.services.impex import ImportRejected, import_csv, voters_to_csv, voters_to_xlsx
from app.services.voters import (
    create_voter,
    export_voters,
    get_active,
    search_voters,
    soft_delete,
    update_voter,
    voter_to_public,
)

router = APIRouter(prefix="/api/voters", tags=["voters"])


def _search(request: Request) -> VoterSearch:
    try:
        return VoterSearch.model_validate(dict(request.query_params))
    except ValidationError as exc:
        first = exc.errors()[0]
        raise HTTPException(status_code=422, detail=first["msg"]) from exc


@router.get("", response_model=VoterPage)
def list_voters(
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_user),
) -> VoterPage:
    query = _search(request)
    rows, total = search_voters(db, query)
    return VoterPage(
        items=[voter_to_public(row) for row in rows],
        total=total,
        page=query.page,
        page_size=query.page_size,
    )


@router.post("", response_model=VoterPublic, status_code=201)
def post_voter(
    body: VoterWrite,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> VoterPublic:
    try:
        voter = create_voter(db, body, source_for(user))
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return voter_to_public(voter)


@router.get("/export.csv")
def export_csv(
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_user),
) -> Response:
    query = _search(request)
    payload = voters_to_csv(export_voters(db, query)).encode("utf-8-sig")
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="nathavaram-voters.csv"'},
    )


@router.get("/export.xlsx")
def export_xlsx(
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_user),
) -> Response:
    query = _search(request)
    payload = voters_to_xlsx(export_voters(db, query))
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="nathavaram-voters.xlsx"'},
    )


@router.post("/import.csv")
def post_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    content = file.file.read(IMPORT_BYTE_LIMIT + 1)
    if len(content) > IMPORT_BYTE_LIMIT:
        raise HTTPException(status_code=413, detail="CSV larger than 5 MB")
    try:
        return import_csv(db, content, source_for(user))
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
        return voter_to_public(update_voter(db, voter_id, body, source_for(user)))
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.delete("/{voter_id}", response_model=VoterPublic)
def delete_voter(
    voter_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> VoterPublic:
    try:
        return voter_to_public(soft_delete(db, voter_id, source_for(user)))
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
