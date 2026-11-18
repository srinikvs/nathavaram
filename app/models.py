"""ORM models. See docs/SCHEMA.md for the column contract."""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, Integer, String, Text, false, text, true
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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

    A soft-deleted row keeps its voter_id and loses every other personal field, so the
    same voter_id can be added again: that revives the tombstone row.
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
    age: Mapped[int | None] = mapped_column(Integer)
    street: Mapped[str | None] = mapped_column(Text)
    mobile: Mapped[str | None] = mapped_column(String(20))
    caste: Mapped[str | None] = mapped_column(String(128))
    village: Mapped[str | None] = mapped_column(String(128))
    panchayath: Mapped[str | None] = mapped_column(String(128))
    mandal: Mapped[str | None] = mapped_column(String(128))
    current_city: Mapped[str | None] = mapped_column(String(128))
    # Default No (Non-Local): rows that existed before the column was added migrate as not local.
    is_local: Mapped[bool] = mapped_column(nullable=False, default=False, server_default=false())
    party: Mapped[str | None] = mapped_column(String(32))
    constituency: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    deleted_at: Mapped[str | None] = mapped_column(String(32))
    source_id: Mapped[str | None] = mapped_column(String(64))
