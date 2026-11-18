"""Fold voters.house_no into the start of voters.address, then drop house_no.

  house_no "12", address "Main St"  ->  address "H.No 12, Main St"
  house_no "12", address empty      ->  address "H.No 12"
  house_no empty                    ->  address unchanged

No value is lost. Tombstoned (soft-deleted) rows have house_no NULL already, so they
are untouched. Downgrade re-creates an empty house_no column; the folded text stays in
address because the original split cannot be recovered reliably.

Revision ID: 0006_fold_house_no_into_address
Revises: 0005_add_address_village_local
Create Date: 2026-11-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_fold_house_no_into_address"
down_revision: Union[str, None] = "0005_add_address_village_local"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PREFIX = "H.No "


def _folded(house_no: str, address: str | None) -> str:
    number = house_no.strip()
    rest = (address or "").strip()
    return f"{PREFIX}{number}, {rest}" if rest else f"{PREFIX}{number}"


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, house_no, address FROM voters WHERE house_no IS NOT NULL")
    ).all()
    update = sa.text("UPDATE voters SET address = :address WHERE id = :id")
    for row_id, house_no, address in rows:
        if not house_no or not house_no.strip():
            continue
        bind.execute(update, {"address": _folded(house_no, address), "id": row_id})
    # DROP COLUMN needs SQLite >= 3.35 (PostgreSQL has always had it). house_no has no
    # index or constraint, so a plain drop is safe.
    op.execute("ALTER TABLE voters DROP COLUMN house_no")


def downgrade() -> None:
    op.add_column("voters", sa.Column("house_no", sa.String(length=64), nullable=True))
