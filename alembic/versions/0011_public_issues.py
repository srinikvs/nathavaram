"""Add the Public Issues tables. Additive only: no existing table or row is changed.

- public_issues: one row per issue. voter_id is a nullable snapshot; name and mobile are copied.
- public_issue_status_events: append-only status history.
- public_issue_photos: metadata for each photo file kept under the photo directory.
- public_issue_notes: notes thread. kind is 'note' (user text) or 'system' (status change).

Deleting an issue removes its child rows (ON DELETE CASCADE). The application also deletes the
photo files, because the database cannot delete files.

Revision ID: 0011_public_issues
Revises: 0010_manager_role
Create Date: 2026-11-24
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_public_issues"
down_revision: Union[str, None] = "0010_manager_role"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STATUS_CHECK = "status IN ('Initiated', 'In Process', 'Resolved', 'Rejected')"
KIND_CHECK = "kind IN ('note', 'system')"


def upgrade() -> None:
    op.create_table(
        "public_issues",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("voter_id", sa.String(length=32), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("mobile", sa.String(length=20), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("issue", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
        sa.CheckConstraint(STATUS_CHECK, name="ck_public_issues_status"),
    )
    op.create_index("ix_public_issues_voter_id", "public_issues", ["voter_id"])
    op.create_index("ix_public_issues_status", "public_issues", ["status"])

    op.create_table(
        "public_issue_status_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "issue_id",
            sa.Integer(),
            sa.ForeignKey("public_issues.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_status", sa.String(length=16), nullable=True),
        sa.Column("to_status", sa.String(length=16), nullable=False),
        sa.Column("changed_by", sa.String(length=64), nullable=False),
        sa.Column("changed_at", sa.String(length=32), nullable=False),
    )
    op.create_index(
        "ix_public_issue_status_events_issue_id", "public_issue_status_events", ["issue_id"]
    )

    op.create_table(
        "public_issue_photos",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "issue_id",
            sa.Integer(),
            sa.ForeignKey("public_issues.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("storage_name", sa.String(length=255), nullable=False, unique=True),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("uploaded_by", sa.String(length=64), nullable=False),
        sa.Column("uploaded_at", sa.String(length=32), nullable=False),
    )
    op.create_index("ix_public_issue_photos_issue_id", "public_issue_photos", ["issue_id"])

    op.create_table(
        "public_issue_notes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "issue_id",
            sa.Integer(),
            sa.ForeignKey("public_issues.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("author", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.CheckConstraint(KIND_CHECK, name="ck_public_issue_notes_kind"),
    )
    op.create_index("ix_public_issue_notes_issue_id", "public_issue_notes", ["issue_id"])


def downgrade() -> None:
    op.drop_index("ix_public_issue_notes_issue_id", table_name="public_issue_notes")
    op.drop_table("public_issue_notes")
    op.drop_index("ix_public_issue_photos_issue_id", table_name="public_issue_photos")
    op.drop_table("public_issue_photos")
    op.drop_index(
        "ix_public_issue_status_events_issue_id", table_name="public_issue_status_events"
    )
    op.drop_table("public_issue_status_events")
    op.drop_index("ix_public_issues_status", table_name="public_issues")
    op.drop_index("ix_public_issues_voter_id", table_name="public_issues")
    op.drop_table("public_issues")
