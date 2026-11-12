"""CSV import with a validation report, plus CSV and Excel export."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timezone

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import CSV_HEADERS, DATA_FIELDS, FIELD_BY_LABEL, IMPORT_ROW_LIMIT, LABEL_BY_FIELD
from app.models import Voter
from app.schemas import ImportErrorItem, ImportReport, VoterWrite
from app.services.revision import allocate_revision
from app.validation import csv_safe, csv_unsafe, dump_dt
from pydantic import ValidationError


class ImportRejected(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _clean_message(message: str) -> str:
    prefix = "Value error, "
    if message.startswith(prefix):
        return message[len(prefix) :]
    return message


def _decode(payload: bytes) -> str:
    if payload.startswith(b"\xff\xfe") or payload.startswith(b"\xfe\xff"):
        raise ImportRejected("CSV must be UTF-8. UTF-16 files are not accepted.")
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportRejected("CSV must be UTF-8 so Telugu text is preserved.") from exc


def import_csv(db: Session, payload: bytes, source_id: str) -> ImportReport:
    text = _decode(payload)
    if not text.strip():
        raise ImportRejected("CSV is empty")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ImportRejected("CSV has no header row")
    headers = [name.strip() if name is not None else "" for name in reader.fieldnames]
    if any(not name for name in headers):
        raise ImportRejected("CSV header contains an empty column name")
    if len(set(headers)) != len(headers):
        raise ImportRejected("CSV header contains duplicate column names")
    missing = [name for name in CSV_HEADERS if name not in headers]
    extra = [name for name in headers if name not in FIELD_BY_LABEL]
    if missing or extra:
        parts = []
        if missing:
            parts.append("missing " + ", ".join(missing))
        if extra:
            parts.append("unexpected " + ", ".join(extra))
        raise ImportRejected("CSV columns must match the voter list exactly: " + "; ".join(parts))

    errors: list[ImportErrorItem] = []
    accepted: list[tuple[int, VoterWrite]] = []
    rows_read = 0
    seen_ids: set[str] = set()
    for offset, raw in enumerate(reader, start=2):
        rows_read += 1
        if rows_read > IMPORT_ROW_LIMIT:
            raise ImportRejected(f"CSV exceeds {IMPORT_ROW_LIMIT} data rows")
        payload_row = {
            FIELD_BY_LABEL[key.strip()]: csv_unsafe(value or "")
            for key, value in raw.items()
            if key is not None
        }
        try:
            parsed = VoterWrite.model_validate(payload_row)
        except ValidationError as exc:
            first = exc.errors()[0]
            loc = first.get("loc") or ()
            field = str(loc[-1]) if loc else None
            display = LABEL_BY_FIELD.get(field, field) if field else None
            errors.append(
                ImportErrorItem(row=offset, field=display, message=_clean_message(first["msg"]))
            )
            continue
        if parsed.voter_id in seen_ids:
            errors.append(
                ImportErrorItem(
                    row=offset,
                    field="VoterId",
                    message=f"Duplicate VoterId {parsed.voter_id} earlier in this file",
                )
            )
            continue
        seen_ids.add(parsed.voter_id)
        accepted.append((offset, parsed))

    if accepted:
        existing = set(
            db.scalars(
                select(Voter.voter_id).where(
                    Voter.deleted_at.is_(None),
                    Voter.voter_id.in_([item.voter_id for _, item in accepted]),
                )
            ).all()
        )
        fresh: list[tuple[int, VoterWrite]] = []
        for row_number, item in accepted:
            if item.voter_id in existing:
                errors.append(
                    ImportErrorItem(
                        row=row_number,
                        field="VoterId",
                        message=f"VoterId {item.voter_id} already exists",
                    )
                )
                continue
            fresh.append((row_number, item))
        accepted = fresh

    now = dump_dt(datetime.now(timezone.utc))
    for _, item in accepted:
        voter = Voter(
            id=str(uuid.uuid4()),
            created_at=now,
            updated_at=now,
            source_id=source_id,
            revision=0,
            deleted_at=None,
        )
        for field in DATA_FIELDS:
            setattr(voter, field, getattr(item, field))
        voter.revision = allocate_revision(db)
        db.add(voter)
    if accepted:
        db.commit()
    errors.sort(key=lambda item: item.row)
    return ImportReport(
        rows_read=rows_read,
        imported=len(accepted),
        skipped=len(errors),
        errors=errors,
    )


def voters_to_csv(voters: list[Voter]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(list(CSV_HEADERS))
    for voter in voters:
        writer.writerow([csv_safe(getattr(voter, name)) for name, _label in _columns()])
    return buffer.getvalue()


def voters_to_xlsx(voters: list[Voter]) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "Voters"
    sheet.append(list(CSV_HEADERS))
    for voter in voters:
        sheet.append([csv_safe(getattr(voter, name)) for name, _label in _columns()])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _columns():
    from app.constants import VOTER_COLUMNS

    return VOTER_COLUMNS
