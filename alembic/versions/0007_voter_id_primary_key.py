"""Make voter_id the primary key and reshape the voter columns.

Column changes (old -> new):

  id (uuid pk)         -> removed; voter_id is the primary key
  bs_no_2024           -> booth_s_no
  surname              -> last_name
  address              -> street, with a leading "H.No <n>," split out into house_no
  c_city               -> current_city
  (none)               -> panchayath, mandal
  gender, permanent_address, profession -> dropped (their data is NOT kept)

The partial unique index on voter_id is no longer needed: the primary key is unique for
every row. Soft-deleted rows keep a voter_id. Rows that were already soft-deleted hold a
placeholder voter_id (the old row id without dashes) because their real VoterId was erased
when they were deleted; they keep that placeholder as their key.

The table is rebuilt (copy, drop, rename) because SQLite cannot change a primary key in
place. The same steps work on PostgreSQL.

Downgrade rebuilds the old shape: a fresh uuid id per row, house_no folded back into
address, gender "Unknown", and permanent_address / profession empty.

Revision ID: 0007_voter_id_primary_key
Revises: 0006_fold_house_no_into_address
Create Date: 2026-11-18
"""

from __future__ import annotations

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_voter_id_primary_key"
down_revision: Union[str, None] = "0006_fold_house_no_into_address"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

HOUSE_PREFIX = "H.No "
HOUSE_NO_MAX = 64
CHUNK = 1000
PARTY_CHECK = (
    "party IS NULL OR party IN ("
    "'TDP', 'YSRCP', 'Jana Sena', 'BJP', 'INC', 'CPI', "
    "'Independent', 'Unknown', 'Prefer not to say')"
)
AGE_CHECK = "age IS NULL OR (age >= 1 AND age <= 130)"
GENDER_CHECK = "gender IN ('Male', 'Female', 'Other', 'Unknown')"

NEW_COLUMNS = (
    "voter_id", "booth", "booth_s_no", "ward", "ward_s_no", "first_name", "last_name",
    "relative_name", "house_no", "age", "street", "mobile", "caste", "village", "panchayath",
    "mandal", "current_city", "is_local", "party", "created_at", "updated_at", "revision",
    "deleted_at", "source_id",
)
OLD_COLUMNS = (
    "id", "booth", "bs_no_2024", "ward", "ward_s_no", "voter_id", "first_name", "surname",
    "relative_name", "address", "village", "permanent_address", "is_local", "gender", "age",
    "caste", "mobile", "c_city", "profession", "party", "created_at", "updated_at",
    "revision", "deleted_at", "source_id",
)


def split_house_no(address: str | None) -> tuple[str | None, str | None]:
    """'H.No 12, Main St' -> ('12', 'Main St'); 'H.No 12' -> ('12', None); else (None, address)."""
    if address is None or not address.startswith(HOUSE_PREFIX):
        return None, address
    house, sep, rest = address[len(HOUSE_PREFIX) :].partition(", ")
    house = house.strip()
    # A house number is short and has no spaces. Anything else is free text, so it stays in
    # street whole rather than overflowing house_no (64 characters) or being cut in the wrong place.
    if not house or len(house) > HOUSE_NO_MAX or any(ch.isspace() for ch in house):
        return None, address
    return house, ((rest.strip() or None) if sep else None)


def fold_house_no(house_no: str | None, street: str | None) -> str | None:
    number = (house_no or "").strip()
    rest = (street or "").strip()
    if not number:
        return street
    return f"{HOUSE_PREFIX}{number}, {rest}" if rest else f"{HOUSE_PREFIX}{number}"


def _start_rebuild(backup: str, drop_indexes: tuple[str, ...]) -> None:
    """Move voters aside to `backup`, or resume after an earlier run died half way.

    SQLite cannot roll DDL back, so a crash after the rename leaves `backup` holding the data
    and a partly built `voters`. In that case drop the partial table and rebuild from `backup`.
    """
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if backup in tables:
        if "voters" in tables:
            op.drop_table("voters")
        return
    _drop_voter_indexes(*drop_indexes)
    op.rename_table("voters", backup)


def _drop_voter_indexes(*names: str) -> None:
    for name in names:
        op.drop_index(name, table_name="voters")


def _insert_all(bind, table_name: str, columns: tuple[str, ...], rows: list[dict]) -> None:
    table = sa.table(table_name, *[sa.column(name) for name in columns])
    for start in range(0, len(rows), CHUNK):
        bind.execute(sa.insert(table), rows[start : start + CHUNK])


def upgrade() -> None:
    bind = op.get_bind()
    _start_rebuild(
        "voters_old",
        ("uq_voters_voter_id_active", "ix_voters_revision", "ix_voters_booth", "ix_voters_ward"),
    )
    op.create_table(
        "voters",
        sa.Column("voter_id", sa.String(length=32), nullable=False),
        sa.Column("booth", sa.String(length=64)),
        sa.Column("booth_s_no", sa.String(length=64)),
        sa.Column("ward", sa.String(length=64)),
        sa.Column("ward_s_no", sa.String(length=64)),
        sa.Column("first_name", sa.String(length=128), nullable=False),
        sa.Column("last_name", sa.String(length=128)),
        sa.Column("relative_name", sa.String(length=128)),
        sa.Column("house_no", sa.String(length=64)),
        sa.Column("age", sa.Integer()),
        sa.Column("street", sa.Text()),
        sa.Column("mobile", sa.String(length=20)),
        sa.Column("caste", sa.String(length=128)),
        sa.Column("village", sa.String(length=128)),
        sa.Column("panchayath", sa.String(length=128)),
        sa.Column("mandal", sa.String(length=128)),
        sa.Column("current_city", sa.String(length=128)),
        sa.Column("is_local", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("party", sa.String(length=32)),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.String(length=32)),
        sa.Column("source_id", sa.String(length=64)),
        # Named so it cannot clash with the old table's "voters_pkey" index on PostgreSQL.
        sa.PrimaryKeyConstraint("voter_id", name="pk_voters"),
        sa.CheckConstraint(AGE_CHECK, name="ck_voters_age"),
        sa.CheckConstraint(PARTY_CHECK, name="ck_voters_party"),
    )
    op.create_index("ix_voters_revision", "voters", ["revision"])
    op.create_index("ix_voters_booth", "voters", ["booth"])
    op.create_index("ix_voters_ward", "voters", ["ward"])

    copied: list[dict] = []
    for row in bind.execute(sa.text("SELECT * FROM voters_old")).mappings().all():
        active = row["deleted_at"] is None
        house_no, street = split_house_no(row["address"]) if active else (None, None)
        copied.append(
            {
                "voter_id": row["voter_id"],
                "booth": row["booth"],
                "booth_s_no": row["bs_no_2024"],
                "ward": row["ward"],
                "ward_s_no": row["ward_s_no"],
                "first_name": row["first_name"],
                "last_name": row["surname"],
                "relative_name": row["relative_name"],
                "house_no": house_no,
                "age": row["age"],
                "street": street,
                "mobile": row["mobile"],
                "caste": row["caste"],
                "village": row["village"],
                "panchayath": None,
                "mandal": None,
                "current_city": row["c_city"],
                "is_local": bool(row["is_local"]),
                "party": row["party"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "revision": row["revision"],
                "deleted_at": row["deleted_at"],
                "source_id": row["source_id"],
            }
        )
    _insert_all(bind, "voters", NEW_COLUMNS, copied)
    op.drop_table("voters_old")


def downgrade() -> None:
    bind = op.get_bind()
    # The new table has no unique voter_id index: the primary key does that job.
    _start_rebuild("voters_new", ("ix_voters_revision", "ix_voters_booth", "ix_voters_ward"))
    op.create_table(
        "voters",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("booth", sa.String(length=64)),
        sa.Column("bs_no_2024", sa.String(length=64)),
        sa.Column("ward", sa.String(length=64)),
        sa.Column("ward_s_no", sa.String(length=64)),
        sa.Column("voter_id", sa.String(length=32), nullable=False),
        sa.Column("first_name", sa.String(length=128), nullable=False),
        sa.Column("surname", sa.String(length=128)),
        sa.Column("relative_name", sa.String(length=128)),
        sa.Column("address", sa.Text()),
        sa.Column("village", sa.Text()),
        sa.Column("permanent_address", sa.Text()),
        sa.Column("is_local", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("gender", sa.String(length=16), nullable=False),
        sa.Column("age", sa.Integer()),
        sa.Column("caste", sa.String(length=128)),
        sa.Column("mobile", sa.String(length=20)),
        sa.Column("c_city", sa.String(length=128)),
        sa.Column("profession", sa.String(length=128)),
        sa.Column("party", sa.String(length=32)),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.String(length=32)),
        sa.Column("source_id", sa.String(length=64)),
        sa.CheckConstraint(GENDER_CHECK, name="ck_voters_gender"),
        sa.CheckConstraint(AGE_CHECK, name="ck_voters_age"),
        sa.CheckConstraint(PARTY_CHECK, name="ck_voters_party"),
    )
    op.create_index("ix_voters_revision", "voters", ["revision"])
    op.create_index("ix_voters_booth", "voters", ["booth"])
    op.create_index("ix_voters_ward", "voters", ["ward"])
    where = sa.text("deleted_at IS NULL")
    where_kw = (
        {"postgresql_where": where} if bind.dialect.name == "postgresql" else {"sqlite_where": where}
    )
    op.create_index("uq_voters_voter_id_active", "voters", ["voter_id"], unique=True, **where_kw)

    restored: list[dict] = []
    for row in bind.execute(sa.text("SELECT * FROM voters_new")).mappings().all():
        row_id = str(uuid.uuid4())
        active = row["deleted_at"] is None
        restored.append(
            {
                "id": row_id,
                "booth": row["booth"],
                "bs_no_2024": row["booth_s_no"],
                "ward": row["ward"],
                "ward_s_no": row["ward_s_no"],
                # A tombstone goes back to the old placeholder form: the row id without dashes.
                "voter_id": row["voter_id"] if active else row_id.replace("-", "")[:32],
                "first_name": row["first_name"],
                "surname": row["last_name"],
                "relative_name": row["relative_name"],
                "address": fold_house_no(row["house_no"], row["street"]),
                "village": row["village"],
                "permanent_address": None,
                "is_local": bool(row["is_local"]),
                "gender": "Unknown",
                "age": row["age"],
                "caste": row["caste"],
                "mobile": row["mobile"],
                "c_city": row["current_city"],
                "profession": None,
                "party": row["party"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "revision": row["revision"],
                "deleted_at": row["deleted_at"],
                "source_id": row["source_id"],
            }
        )
    _insert_all(bind, "voters", OLD_COLUMNS, restored)
    op.drop_table("voters_new")
