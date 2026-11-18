"""Add voters.constituency, the last voter column.

Existing rows get null. Downgrade drops the column and its data.

Revision ID: 0008_add_constituency
Revises: 0007_voter_id_primary_key
Create Date: 2026-11-18
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_add_constituency"
down_revision: Union[str, None] = "0007_voter_id_primary_key"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("voters", sa.Column("constituency", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("voters", "constituency")
