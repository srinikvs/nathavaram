"""Add the manager role and voters.hard_deleted_at.

- users.role CHECK is widened from ('admin', 'user') to ('admin', 'manager', 'user').
  No existing role value changes and no user row is rewritten or remapped.
- voters.hard_deleted_at is a new nullable column. It stays NULL for every existing row:
  rows that were already soft-deleted are not flagged as hard-deleted here. Voter rows are
  not otherwise touched.

SQLite cannot alter a CHECK constraint in place, so the users table is rebuilt: every row is
copied with its id, username, password_hash, timestamps and flags unchanged, then the old
table is dropped and the new one renamed. The copy is verified by row count before the drop.
PostgreSQL replaces the named constraint.

Downgrade refuses to run while any manager account exists, because the old CHECK would
reject them. It never changes roles on its own.

Revision ID: 0010_manager_role
Revises: 0009_add_gender
Create Date: 2026-11-24
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_manager_role"
down_revision: Union[str, None] = "0009_add_gender"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROLE_CHECK_NEW = "role IN ('admin', 'manager', 'user')"
ROLE_CHECK_OLD = "role IN ('admin', 'user')"
USER_COLUMNS = (
    "id",
    "username",
    "password_hash",
    "role",
    "is_active",
    "must_change_password",
    "is_owner",
    "token_version",
    "deleted_at",
    "created_at",
    "updated_at",
)


def _rebuild_users_sqlite(role_check: str) -> None:
    """Recreate `users` with a new role CHECK, copying every row."""
    bind = op.get_bind()
    before = bind.execute(sa.text("SELECT COUNT(*) FROM users")).scalar_one()
    columns = ", ".join(USER_COLUMNS)
    op.execute(
        "CREATE TABLE users_new ("
        " id VARCHAR(36) NOT NULL PRIMARY KEY,"
        " username VARCHAR(32) NOT NULL,"
        " password_hash VARCHAR(255) NOT NULL,"
        " role VARCHAR(16) NOT NULL,"
        " is_active BOOLEAN NOT NULL,"
        " must_change_password BOOLEAN NOT NULL DEFAULT 1,"
        " is_owner BOOLEAN NOT NULL DEFAULT 0,"
        " token_version INTEGER NOT NULL DEFAULT 0,"
        " deleted_at VARCHAR(32),"
        " created_at VARCHAR(32) NOT NULL,"
        " updated_at VARCHAR(32) NOT NULL,"
        f" CONSTRAINT ck_users_role CHECK ({role_check}),"
        " CONSTRAINT uq_users_username UNIQUE (username)"
        ")"
    )
    op.execute(f"INSERT INTO users_new ({columns}) SELECT {columns} FROM users")
    after = bind.execute(sa.text("SELECT COUNT(*) FROM users_new")).scalar_one()
    if after != before:
        raise RuntimeError(f"users rebuild copied {after} of {before} rows; aborting")
    op.execute("DROP TABLE users")
    op.execute("ALTER TABLE users_new RENAME TO users")


def _replace_role_check(role_check: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        _rebuild_users_sqlite(role_check)
        return
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.create_check_constraint("ck_users_role", "users", role_check)


def upgrade() -> None:
    _replace_role_check(ROLE_CHECK_NEW)
    op.add_column("voters", sa.Column("hard_deleted_at", sa.String(length=32), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    managers = bind.execute(
        sa.text("SELECT COUNT(*) FROM users WHERE role = 'manager'")
    ).scalar_one()
    if managers:
        raise RuntimeError(
            f"Downgrade refused: {managers} manager account(s) exist and the old role CHECK "
            "would reject them. Change their role first."
        )
    op.drop_column("voters", "hard_deleted_at")
    _replace_role_check(ROLE_CHECK_OLD)
