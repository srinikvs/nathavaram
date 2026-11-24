"""ORM models. See docs/SCHEMA.md for the column contract."""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, false, text, true
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.issue_rules import ISSUE_STATUSES, NOTE_KIND, SYSTEM_KIND


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'user')", name="ck_users_role"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    must_change_password: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default=true()
    )
    is_owner: Mapped[bool] = mapped_column(nullable=False, default=False, server_default=false())
    token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    deleted_at: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)


class SyncState(Base):
    """Single-row counter. Every voter mutation takes the next revision."""

    __tablename__ = "sync_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Voter(Base):
    """One row per voter. voter_id (EPIC number) is the primary key.

    Soft delete sets deleted_at and keeps every field (restorable). Hard delete strips the
    personal fields, sets hard_deleted_at and keeps the row so sync can propagate the wipe.
    The same voter_id can be added again: that revives the row in place.
    """

    __tablename__ = "voters"
    __table_args__ = (
        CheckConstraint("age IS NULL OR (age >= 1 AND age <= 130)", name="ck_voters_age"),
        CheckConstraint(
            "party IS NULL OR party IN ("
            "'TDP', 'YSRCP', 'Jana Sena', 'BJP', 'INC', 'CPI', "
            "'Independent', 'Unknown', 'Prefer not to say'"
            ")",
            name="ck_voters_party",
        ),
        CheckConstraint(
            "gender IS NULL OR gender IN ('Male', 'Female', 'Other')",
            name="ck_voters_gender",
        ),
    )

    voter_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    booth: Mapped[str | None] = mapped_column(String(64), index=True)
    booth_s_no: Mapped[str | None] = mapped_column(String(64))
    ward: Mapped[str | None] = mapped_column(String(64), index=True)
    ward_s_no: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str] = mapped_column(String(128), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(128))
    relative_name: Mapped[str | None] = mapped_column(String(128))
    house_no: Mapped[str | None] = mapped_column(String(64))
    gender: Mapped[str | None] = mapped_column(String(16))
    age: Mapped[int | None] = mapped_column(Integer)
    street: Mapped[str | None] = mapped_column(Text)
    mobile: Mapped[str | None] = mapped_column(String(20))
    caste: Mapped[str | None] = mapped_column(String(128))
    village: Mapped[str | None] = mapped_column(String(128))
    panchayath: Mapped[str | None] = mapped_column(String(128))
    mandal: Mapped[str | None] = mapped_column(String(128))
    current_city: Mapped[str | None] = mapped_column(String(128))
    # New inserts default to Local (Python-side). server_default stays false() so rows that
    # existed before the column was added keep migrating as Non-Local; no backfill.
    is_local: Mapped[bool] = mapped_column(nullable=False, default=True, server_default=false())
    party: Mapped[str | None] = mapped_column(String(32))
    constituency: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    deleted_at: Mapped[str | None] = mapped_column(String(32))
    # Set only by hard delete. A row with hard_deleted_at can never be restored.
    hard_deleted_at: Mapped[str | None] = mapped_column(String(32))
    source_id: Mapped[str | None] = mapped_column(String(64))


_STATUS_SQL = ", ".join(f"'{status}'" for status in ISSUE_STATUSES)


class PublicIssue(Base):
    """One public issue. voter_id is a snapshot: it is set only when it matched an active voter.

    Name and mobile are copied at creation and never follow later voter edits.
    """

    __tablename__ = "public_issues"
    __table_args__ = (
        CheckConstraint(f"status IN ({_STATUS_SQL})", name="ck_public_issues_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    voter_id: Mapped[str | None] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    mobile: Mapped[str] = mapped_column(String(20), nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    issue: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False)


class PublicIssueStatusEvent(Base):
    """Append-only status history. from_status is NULL only for the first (creation) row."""

    __tablename__ = "public_issue_status_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    issue_id: Mapped[int] = mapped_column(
        ForeignKey("public_issues.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str] = mapped_column(String(16), nullable=False)
    changed_by: Mapped[str] = mapped_column(String(64), nullable=False)
    changed_at: Mapped[str] = mapped_column(String(32), nullable=False)


class PublicIssuePhoto(Base):
    """Metadata for one stored photo. The file lives outside static/ under the photo directory."""

    __tablename__ = "public_issue_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    issue_id: Mapped[int] = mapped_column(
        ForeignKey("public_issues.id", ondelete="CASCADE"), nullable=False, index=True
    )
    storage_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_by: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_at: Mapped[str] = mapped_column(String(32), nullable=False)


class PublicIssueNote(Base):
    """Notes thread. kind 'note' is written by a user and is never edited; 'system' records a
    status change. Only an admin may delete a 'note' row."""

    __tablename__ = "public_issue_notes"
    __table_args__ = (
        CheckConstraint(
            f"kind IN ('{NOTE_KIND}', '{SYSTEM_KIND}')", name="ck_public_issue_notes_kind"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    issue_id: Mapped[int] = mapped_column(
        ForeignKey("public_issues.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
