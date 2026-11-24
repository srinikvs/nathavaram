"""CSV and Excel import with a validation report, plus CSV and Excel export."""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterable
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path, PurePath

from openpyxl import Workbook, load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import (
    CSV_HEADERS,
    DATA_FIELDS,
    NORMALIZED_DROPPED_HEADERS,
    NORMALIZED_HEADER_FIELDS,
    normalize_header,
    IMPORT_ROW_LIMIT,
    LABEL_BY_FIELD,
    OPTIONAL_IMPORT_FIELDS,
    VOTER_COLUMNS,
)
from app.models import Voter
from app.schemas import ImportErrorItem, ImportReport, VoterWrite
from app.services.revision import allocate_revision
from app.services.voters import is_unrecoverable_soft_delete
from app.validation import csv_safe, csv_unsafe, dump_dt, local_label
from pydantic import ValidationError


XLSX_LABEL = "Excel file"
ACCEPTED_SUFFIXES = frozenset({".csv", ".xlsx"})
ZIP_MAGIC = b"PK"
OLE_MAGIC = b"\xd0\xcf\x11\xe0"
XLSX_UNPACKED_LIMIT = 100 * 1024 * 1024
LEGACY_OR_LOCKED_MESSAGE = (
    "This looks like a legacy .xls or password-protected Excel file. Only .csv and .xlsx "
    "are accepted; remove the password and save as .xlsx"
)


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
    rows = (
        (
            offset,
            {
                key.strip(): (value or "")
                for key, value in raw.items()
                if key is not None and isinstance(value, (str, type(None)))
            },
        )
        for offset, raw in enumerate(reader, start=2)
    )
    return _import_table(db, headers, rows, source_id, "CSV")


def import_xlsx(db: Session, payload: bytes, source_id: str) -> ImportReport:
    """Import the first sheet of an .xlsx workbook through the same path as import_csv."""
    headers, rows = _read_xlsx(payload)
    return _import_table(db, headers, iter(rows), source_id, XLSX_LABEL)


def import_upload(
    db: Session, filename: str, payload: bytes, source_id: str, only: str | None = None
) -> ImportReport:
    """Import an uploaded CSV or .xlsx, chosen by file name and, for xlsx, the ZIP signature."""
    kind = detect_upload_kind(filename, payload)
    if only is not None and kind != only:
        raise ImportRejected(
            "This endpoint takes an Excel (.xlsx) file; use /api/voters/import.csv for CSV"
            if only == "xlsx"
            else "This endpoint takes a CSV file; use /api/voters/import.xlsx for Excel"
        )
    if kind == "xlsx":
        return import_xlsx(db, payload, source_id)
    return import_csv(db, payload, source_id)


def detect_upload_kind(filename: str, payload: bytes) -> str:
    """Return "csv" or "xlsx", or raise ImportRejected for any other file type."""
    suffix = PurePath(filename or "").suffix.lower()
    if suffix not in ACCEPTED_SUFFIXES:
        hint = " (legacy .xls is not supported; save as .xlsx)" if suffix == ".xls" else ""
        raise ImportRejected("Only .csv and .xlsx files are accepted" + hint)
    if payload.startswith(OLE_MAGIC):
        raise ImportRejected(LEGACY_OR_LOCKED_MESSAGE)
    if suffix == ".xlsx" or payload.startswith(ZIP_MAGIC):
        return "xlsx"
    return "csv"


def oversize_message(filename: str) -> str:
    if PurePath(filename or "").suffix.lower() == ".csv":
        return "CSV larger than 5 MB"
    return "File larger than 5 MB (CSV and Excel uploads are limited to 5 MB)"


def _cell_text(value: object) -> str:
    """Render one worksheet cell as the text a CSV cell would have held."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (float, Decimal)):
        if value != value or value in (float("inf"), float("-inf")):
            return ""
        return str(int(value)) if value == int(value) else str(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ") if value.time() != time() else value.date().isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    return str(value)


def _read_xlsx(payload: bytes) -> tuple[list[str], list[tuple[int, dict[str, str]]]]:
    """First sheet as (headers, [(sheet row number, {header: text})]); blank rows are dropped."""
    if payload.startswith(OLE_MAGIC):
        raise ImportRejected(LEGACY_OR_LOCKED_MESSAGE)
    if not payload.strip():
        raise ImportRejected(f"{XLSX_LABEL} is empty")
    corrupt = ImportRejected(
        f"{XLSX_LABEL} could not be read. It may be corrupt or password-protected; "
        "re-save it as .xlsx without a password"
    )
    if not payload.startswith(ZIP_MAGIC):
        raise corrupt
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            if sum(info.file_size for info in archive.infolist()) > XLSX_UNPACKED_LIMIT:
                raise ImportRejected(f"{XLSX_LABEL} is too large when unpacked")
        book = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    except ImportRejected:
        raise
    except Exception as exc:  # openpyxl and zipfile raise many types for bad files
        raise corrupt from exc
    table: list[tuple[int, list[str]]] = []
    try:
        if not book.worksheets:
            raise ImportRejected(f"{XLSX_LABEL} has no sheets")
        for number, raw in enumerate(book.worksheets[0].iter_rows(values_only=True), start=1):
            if number > IMPORT_ROW_LIMIT * 2:
                raise ImportRejected(f"{XLSX_LABEL} exceeds {IMPORT_ROW_LIMIT} data rows")
            cells = [_cell_text(value) for value in raw]
            if any(text.strip() for text in cells):
                table.append((number, cells))
    except ImportRejected:
        raise
    except Exception as exc:
        raise corrupt from exc
    finally:
        book.close()
    if not table:
        raise ImportRejected(f"{XLSX_LABEL} is empty")
    headers = [text.strip() for text in table[0][1]]
    while headers and not headers[-1]:
        headers.pop()
    width = len(headers)
    rows = [
        (number, dict(zip(headers, (cells + [""] * width)[:width]))) for number, cells in table[1:]
    ]
    return headers, rows


def _import_table(
    db: Session,
    headers: list[str],
    rows: Iterable[tuple[int, dict[str, str]]],
    source_id: str,
    label: str,
) -> ImportReport:
    """Validate and store rows. Shared by CSV and Excel so both give the same report."""
    if any(not name for name in headers):
        raise ImportRejected(f"{label} header contains an empty column name")
    if len(set(headers)) != len(headers):
        raise ImportRejected(f"{label} header contains duplicate column names")
    # Old headers ("SurName", "Address", "C_City", "2024 Booth", ...) are accepted alongside the
    # new ones. Permanent address and Profession were removed: their columns are ignored.
    # Gender is a real optional column: a file without it imports with gender left blank.
    # Lookup ignores case, extra whitespace and trailing dots (see normalize_header).
    keys = [normalize_header(name) for name in headers]
    extra = [
        name
        for name, key in zip(headers, keys)
        if key not in NORMALIZED_HEADER_FIELDS and key not in NORMALIZED_DROPPED_HEADERS
    ]
    fields = [NORMALIZED_HEADER_FIELDS[key] for key in keys if key in NORMALIZED_HEADER_FIELDS]
    if len(set(fields)) != len(fields):
        raise ImportRejected(f"{label} header contains the same column twice (old and new names mixed)")
    missing = [
        column_label
        for field, column_label in VOTER_COLUMNS
        if field not in fields and field not in OPTIONAL_IMPORT_FIELDS
    ]
    if missing or extra:
        parts = []
        if missing:
            parts.append("missing " + ", ".join(missing))
        if extra:
            parts.append("unexpected " + ", ".join(extra))
        raise ImportRejected(f"{label} columns must match the voter list exactly: " + "; ".join(parts))

    errors: list[ImportErrorItem] = []
    accepted: list[tuple[int, VoterWrite]] = []
    rows_read = 0
    seen_ids: set[str] = set()
    for offset, raw in rows:
        rows_read += 1
        if rows_read > IMPORT_ROW_LIMIT:
            raise ImportRejected(f"{label} exceeds {IMPORT_ROW_LIMIT} data rows")
        payload_row = {
            NORMALIZED_HEADER_FIELDS[normalize_header(key)]: csv_unsafe(value)
            for key, value in raw.items()
            if normalize_header(key) in NORMALIZED_HEADER_FIELDS
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

    tombstones: dict[str, Voter] = {}
    if accepted:
        stored = db.scalars(
            select(Voter).where(Voter.voter_id.in_([item.voter_id for _, item in accepted]))
        ).all()
        active = {row.voter_id for row in stored if row.deleted_at is None}
        unrecoverable = {row.voter_id for row in stored if is_unrecoverable_soft_delete(row)}
        tombstones = {row.voter_id: row for row in stored if row.deleted_at is not None}
        fresh: list[tuple[int, VoterWrite]] = []
        for row_number, item in accepted:
            if item.voter_id in active:
                errors.append(
                    ImportErrorItem(
                        row=row_number,
                        field="VoterId",
                        message=f"VoterId {item.voter_id} already exists",
                    )
                )
                continue
            if item.voter_id in unrecoverable:
                errors.append(
                    ImportErrorItem(
                        row=row_number,
                        field="VoterId",
                        message=(
                            f"VoterId {item.voter_id} belongs to a deleted record whose details were "
                            "removed and cannot be restored; permanently delete it first"
                        ),
                    )
                )
                continue
            fresh.append((row_number, item))
        accepted = fresh

    now = dump_dt(datetime.now(timezone.utc))
    for _, item in accepted:
        # A soft-deleted voter_id is revived in place: voter_id is the primary key.
        voter = tombstones.get(item.voter_id)
        if voter is None:
            voter = Voter(voter_id=item.voter_id, revision=0)
            db.add(voter)
        voter.created_at = now
        voter.updated_at = now
        voter.source_id = source_id
        voter.deleted_at = None
        voter.hard_deleted_at = None  # a re-import is a new record, never a restore of old data
        for field in DATA_FIELDS:
            setattr(voter, field, getattr(item, field))
        voter.revision = allocate_revision(db)
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
        writer.writerow([_cell(voter, name) for name, _label in VOTER_COLUMNS])
    return buffer.getvalue()


def voters_to_xlsx(voters: list[Voter]) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "Voters"
    sheet.append(list(CSV_HEADERS))
    for voter in voters:
        sheet.append([_cell(voter, name) for name, _label in VOTER_COLUMNS])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def sample_xlsx_bytes(csv_path: Path) -> bytes:
    """Build the fake sample workbook from sample/voters_sample.csv (no .xlsx is stored)."""
    book = Workbook()
    sheet = book.active
    sheet.title = "Voters"
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.reader(handle):
            sheet.append(row)
    sheet.freeze_panes = "A2"
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _cell(voter: Voter, name: str) -> str:
    value = getattr(voter, name)
    if name == "is_local":
        return local_label(value)
    return csv_safe(value)
