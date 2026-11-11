"""Add users.must_change_password and users.is_owner.

Existing rows get must_change_password=true (forced change on next login) and
is_owner=false. The env-seeded admin is promoted to owner at startup.

Revision ID: 0002_user_security_flags
Revises: 0001_initial
Create Date: 2026-11-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_user_security_flags"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch.add_column(sa.Column("is_owner", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("is_owner")
        batch.drop_column("must_change_password")
