"""Read-only master data lists derived from the voter geo and demographic fields.

There are no separate master tables: every list is a GROUP BY over active voters, so it
always agrees with the voter list and needs no migration. Profession is not a voter
field yet, so that tab reports itself as unavailable instead of inventing data.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import Voter
from app.schemas import MasterColumn, MasterTable

BLANK = "(blank)"
PROFESSION_NOTE = "Profession is not recorded on voters yet, so there is nothing to list."

_Field = tuple[str, str, object]  # (key, heading, voter column)


def _clean(column):
    """Trimmed value, NULL when empty (so blanks never count as a distinct child)."""
    return func.nullif(func.trim(column), "")


def _label(column):
    return func.coalesce(_clean(column), BLANK)


@dataclass(frozen=True)
class _Spec:
    label: str
    keys: tuple[_Field, ...]
    counts: tuple[_Field, ...] = ()  # distinct child counts
    local_count: bool = False


SPECS: dict[str, _Spec] = {
    "constituency": _Spec(
        "Constituency",
        (("constituency", "Constituency", Voter.constituency),),
        (("mandals", "Mandals", Voter.mandal), ("panchayaths", "Panchayaths", Voter.panchayath)),
    ),
    "mandalam": _Spec(
        "Mandalam",
        (("mandal", "Mandalam", Voter.mandal), ("constituency", "Constituency", Voter.constituency)),
        (("panchayaths", "Panchayaths", Voter.panchayath), ("villages", "Villages", Voter.village)),
    ),
    "panchayathi": _Spec(
        "Panchayathi",
        (("panchayath", "Panchayathi", Voter.panchayath), ("mandal", "Mandalam", Voter.mandal)),
        (("villages", "Villages", Voter.village), ("booths", "Booths", Voter.booth)),
    ),
    "booth": _Spec(
        "Booth",
        (("booth", "Booth", Voter.booth),),
        (("wards", "Wards", Voter.ward), ("streets", "Streets", Voter.street)),
    ),
    "ward": _Spec(
        "Ward",
        (("ward", "Ward", Voter.ward),),
        (("booths", "Booths", Voter.booth), ("streets", "Streets", Voter.street)),
    ),
    "street": _Spec(
        "Street",
        (("street", "Street", Voter.street), ("ward", "Ward", Voter.ward)),
        (("booths", "Booths", Voter.booth),),
    ),
    "caste": _Spec("Caste", (("caste", "Caste", Voter.caste),)),
    "profession": _Spec("Profession", ()),
    "city": _Spec("City", (("city", "City", Voter.current_city),), local_count=True),
}

ENTITIES: tuple[str, ...] = tuple(SPECS)


def tab_list() -> list[dict[str, str]]:
    return [{"entity": name, "label": spec.label} for name, spec in SPECS.items()]


def build_master_table(db: Session, entity: str) -> MasterTable:
    """Rows for one tab. Raises KeyError for an unknown entity."""
    spec = SPECS[entity]
    if entity == "profession":
        return MasterTable(
            entity=entity,
            label=spec.label,
            available=False,
            note=PROFESSION_NOTE,
            columns=[],
            rows=[],
        )
    columns = [MasterColumn(key=k, label=h) for k, h, _ in spec.keys]
    columns += [MasterColumn(key=k, label=h, numeric=True) for k, h, _ in spec.counts]
    if spec.local_count:
        columns.append(MasterColumn(key="local", label="Local voters", numeric=True))
    columns.append(MasterColumn(key="voters", label="Voters", numeric=True))

    key_exprs = [_label(col) for _, _, col in spec.keys]
    count_exprs = [func.count(func.distinct(_clean(col))) for _, _, col in spec.counts]
    extra = []
    if spec.local_count:
        extra.append(func.sum(case((Voter.is_local.is_(True), 1), else_=0)))
    stmt = (
        select(*key_exprs, *count_exprs, *extra, func.count())
        .where(Voter.deleted_at.is_(None))
        .group_by(*key_exprs)
        .order_by(func.count().desc(), *key_exprs)
    )
    names = [c.key for c in columns]
    rows = []
    for record in db.execute(stmt).all():
        values = [v if isinstance(v, str) else int(v or 0) for v in record]
        rows.append(dict(zip(names, values, strict=True)))
    return MasterTable(
        entity=entity, label=spec.label, available=True, columns=columns, rows=rows
    )
