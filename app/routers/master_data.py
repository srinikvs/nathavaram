"""Admin master data lists, plus rename and guarded delete of their values."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin, source_for
from app.errors import ServiceError
from app.models import User
from app.schemas import MasterRename, MasterRows, MasterTable
from app.services.master_bulk import check_delete, rename_values
from app.services.master_data import build_master_table, tab_list

router = APIRouter(prefix="/api/master-data", tags=["master-data"])


@router.get("")
def tabs(_: User = Depends(require_admin)) -> dict[str, list[dict[str, str]]]:
    return {"tabs": tab_list()}


@router.get("/{entity}", response_model=MasterTable)
def table(
    entity: str, db: Session = Depends(get_db), _: User = Depends(require_admin)
) -> MasterTable:
    try:
        return build_master_table(db, entity)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown master data list") from None


@router.post("/{entity}/rename")
def rename(
    entity: str,
    body: MasterRename,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> dict[str, int]:
    """Replace the tab's primary value on every active voter that has the selected value(s)."""
    try:
        return rename_values(db, entity, body.items, body.new_value, source_for(user))
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/{entity}/delete")
def delete(
    entity: str,
    body: MasterRows,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> dict[str, int]:
    """Blocked (409) while any active voter uses a selected value; otherwise a no-op."""
    try:
        return check_delete(db, entity, body.items)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
