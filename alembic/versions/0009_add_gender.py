"""Add voters.gender, an optional column between house_no and age.

Existing rows stay null: no gender is invented or backfilled, and there is no server
default. Allowed values are Male, Female, Other (or null). Downgrade drops the column
and its data.

Revision ID: 0009_add_gender
Revises: 0008_add_constituency
Create Date: 2026-11-20
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0009_add_gender"
down_revision: Union[str, None] = "0008_add_constituency"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # One plain ALTER TABLE: SQLite cannot add a table constraint afterwards, but it accepts a
    # column-level CHECK here, and so does PostgreSQL. Existing rows get NULL, which passes it.
    op.execute(
        "ALTER TABLE voters ADD COLUMN gender VARCHAR(16) "
        "CONSTRAINT ck_voters_gender CHECK (gender IS NULL OR gender IN ('Male', 'Female', 'Other'))"
    )


def downgrade() -> None:
    op.drop_column("voters", "gender")
