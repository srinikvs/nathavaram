"""Admin master data lists (read-only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin
from app.models import User
from app.schemas import MasterTable
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
