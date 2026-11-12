"""Add voters.address, village, permanent_address and is_local.

Every existing row is kept. The three text columns are nullable, so old rows get NULL
(shown as blank). is_local is NOT NULL with a server default of false: rows that
existed before the column was added migrate as Local = No.

Revision ID: 0005_add_address_village_local
Revises: 0004_rename_booth_ward
Create Date: 2026-11-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_add_address_village_local"
down_revision: Union[str, None] = "0004_rename_booth_ward"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("voters", sa.Column("address", sa.Text(), nullable=True))
    op.add_column("voters", sa.Column("village", sa.Text(), nullable=True))
    op.add_column("voters", sa.Column("permanent_address", sa.Text(), nullable=True))
    op.add_column(
        "voters",
        sa.Column("is_local", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    # DROP COLUMN needs SQLite >= 3.35 (PostgreSQL has always had it).
    for column in ("is_local", "permanent_address", "village", "address"):
        op.execute(f"ALTER TABLE voters DROP COLUMN {column}")
