"""Initial voter, user, and sync tables.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.CheckConstraint("role IN ('admin', 'user')", name="ck_users_role"),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    op.create_table(
        "sync_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("current_revision", sa.Integer(), nullable=False),
    )
    op.execute("INSERT INTO sync_state (id, current_revision) VALUES (1, 0)")
    op.create_table(
        "voters",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("booth_2024", sa.String(length=64)),
        sa.Column("bs_no_2024", sa.String(length=64)),
        sa.Column("ward_2026", sa.String(length=64)),
        sa.Column("ward_s_no", sa.String(length=64)),
        sa.Column("voter_id", sa.String(length=32), nullable=False),
        sa.Column("first_name", sa.String(length=128), nullable=False),
        sa.Column("surname", sa.String(length=128)),
        sa.Column("relative_name", sa.String(length=128)),
        sa.Column("house_no", sa.String(length=64)),
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
        sa.CheckConstraint(
            "gender IN ('Male', 'Female', 'Other', 'Unknown')",
            name="ck_voters_gender",
        ),
        sa.CheckConstraint("age IS NULL OR (age >= 1 AND age <= 130)", name="ck_voters_age"),
        sa.CheckConstraint(
            "party IS NULL OR party IN ("
            "'TDP', 'YSRCP', 'Jana Sena', 'BJP', 'INC', 'CPI', "
            "'Independent', 'Unknown', 'Prefer not to say')",
            name="ck_voters_party",
        ),
    )
    op.create_index("ix_voters_revision", "voters", ["revision"])
    op.create_index("ix_voters_booth_2024", "voters", ["booth_2024"])
    op.create_index("ix_voters_ward_2026", "voters", ["ward_2026"])
    bind = op.get_bind()
    where = sa.text("deleted_at IS NULL")
    where_kw = (
        {"postgresql_where": where} if bind.dialect.name == "postgresql" else {"sqlite_where": where}
    )
    op.create_index("uq_voters_voter_id_active", "voters", ["voter_id"], unique=True, **where_kw)


def downgrade() -> None:
    op.drop_index("uq_voters_voter_id_active", table_name="voters")
    op.drop_index("ix_voters_ward_2026", table_name="voters")
    op.drop_index("ix_voters_booth_2024", table_name="voters")
    op.drop_index("ix_voters_revision", table_name="voters")
    op.drop_table("voters")
    op.drop_table("sync_state")
    op.drop_table("users")
