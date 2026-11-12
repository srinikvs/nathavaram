"""Add users.token_version (session revocation) and users.deleted_at (soft delete).

Existing JWTs carry no version claim and stop working after this migration;
users sign in again.

Revision ID: 0003_token_version
Revises: 0002_user_security_flags
Create Date: 2026-11-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_token_version"
down_revision: Union[str, None] = "0002_user_security_flags"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("token_version", sa.Integer(), nullable=False, server_default=sa.text("0"))
        )
        batch.add_column(sa.Column("deleted_at", sa.String(32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("deleted_at")
        batch.drop_column("token_version")
