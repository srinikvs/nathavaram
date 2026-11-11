"""Aggregate counts for the printable report."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.constants import AGE_BANDS, GENDERS, PARTIES
from app.models import Voter
from app.schemas import CountRow, ReportSummary
from app.validation import age_band, csv_safe


def _label(value: str | None) -> str:
    if value is None or value.strip() == "":
        return "(blank)"
    return value


def _grouped(db: Session, column) -> list[CountRow]:
    stmt = (
        select(column, func.count())
        .where(Voter.deleted_at.is_(None))
        .group_by(column)
        .order_by(func.count().desc(), column)
    )
    return [CountRow(label=_label(name), count=int(count)) for name, count in db.execute(stmt).all()]


def _ordered(rows: list[CountRow], order: tuple[str, ...], *, include_blank: bool) -> list[CountRow]:
    found = {row.label: row.count for row in rows}
    ordered = [CountRow(label=label, count=found.get(label, 0)) for label in order]
    if include_blank:
        ordered.append(CountRow(label="(blank)", count=found.get("(blank)", 0)))
    extras = [row for row in rows if row.label not in set(order) and row.label != "(blank)"]
    return ordered + extras


def build_report(db: Session) -> ReportSummary:
    total = int(
        db.scalar(select(func.count()).select_from(Voter).where(Voter.deleted_at.is_(None))) or 0
    )
    ages = list(db.scalars(select(Voter.age).where(Voter.deleted_at.is_(None))).all())
    band_counts = {band: 0 for band in AGE_BANDS}
    for age in ages:
        band_counts[age_band(age)] += 1
    return ReportSummary(
        total=total,
        by_booth=_grouped(db, Voter.booth_2024),
        by_ward=_grouped(db, Voter.ward_2026),
        by_gender=_ordered(_grouped(db, Voter.gender), GENDERS, include_blank=False),
        by_age_band=[CountRow(label=band, count=band_counts[band]) for band in AGE_BANDS],
        by_caste=_grouped(db, Voter.caste),
        by_party=_ordered(_grouped(db, Voter.party), PARTIES, include_blank=True),
        by_profession=_grouped(db, Voter.profession),
    )


def report_csv(report: ReportSummary) -> str:
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Section", "Label", "Count"])
    writer.writerow(["Total", "(all)", report.total])
    sections = (
        ("2024 Booth", report.by_booth),
        ("2026 Ward", report.by_ward),
        ("Gender", report.by_gender),
        ("Age band", report.by_age_band),
        ("Caste", report.by_caste),
        ("Party", report.by_party),
        ("Profession", report.by_profession),
    )
    for name, rows in sections:
        for row in rows:
            writer.writerow([csv_safe(name), csv_safe(row.label), row.count])
    return buffer.getvalue()
