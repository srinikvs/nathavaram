"""Rename voters.booth_2024 -> booth and voters.ward_2026 -> ward.

A column rename keeps every stored value. The two lookup indexes are recreated under
names that match the new columns. The partial unique index on voter_id is untouched.

Revision ID: 0004_rename_booth_ward
Revises: 0003_token_version
Create Date: 2026-11-12
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0004_rename_booth_ward"
down_revision: Union[str, None] = "0003_token_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _rename(old_booth: str, new_booth: str, old_ward: str, new_ward: str) -> None:
    # RENAME COLUMN is supported by SQLite >= 3.25 and PostgreSQL, and keeps row data as is.
    op.drop_index(f"ix_voters_{old_booth}", table_name="voters")
    op.drop_index(f"ix_voters_{old_ward}", table_name="voters")
    op.execute(f"ALTER TABLE voters RENAME COLUMN {old_booth} TO {new_booth}")
    op.execute(f"ALTER TABLE voters RENAME COLUMN {old_ward} TO {new_ward}")
    op.create_index(f"ix_voters_{new_booth}", "voters", [new_booth])
    op.create_index(f"ix_voters_{new_ward}", "voters", [new_ward])


def upgrade() -> None:
    _rename("booth_2024", "booth", "ward_2026", "ward")


def downgrade() -> None:
    _rename("booth", "booth_2024", "ward", "ward_2026")
