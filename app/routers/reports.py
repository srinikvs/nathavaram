"""Count reports."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_user
from app.models import User
from app.schemas import ReportSummary
from app.services.reports import build_report, report_csv

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/summary", response_model=ReportSummary)
def summary(db: Session = Depends(get_db), _: User = Depends(require_user)) -> ReportSummary:
    return build_report(db)


@router.get("/export.csv")
def export_summary(db: Session = Depends(get_db), _: User = Depends(require_user)) -> Response:
    payload = report_csv(build_report(db)).encode("utf-8-sig")
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="nathavaram-report.csv"'},
    )
