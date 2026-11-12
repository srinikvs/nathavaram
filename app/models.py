"""ORM models. See docs/SCHEMA.md for the column contract."""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, Integer, String, false, true
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
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)


class SyncState(Base):
    """Single-row counter. Every voter mutation takes the next revision."""

    __tablename__ = "sync_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Voter(Base):
    __tablename__ = "voters"
    __table_args__ = (
        CheckConstraint(
            "gender IN ('Male', 'Female', 'Other', 'Unknown')",
            name="ck_voters_gender",
        ),
        CheckConstraint("age IS NULL OR (age >= 1 AND age <= 130)", name="ck_voters_age"),
        CheckConstraint(
            "party IS NULL OR party IN ("
            "'TDP', 'YSRCP', 'Jana Sena', 'BJP', 'INC', 'CPI', "
            "'Independent', 'Unknown', 'Prefer not to say'"
            ")",
            name="ck_voters_party",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    booth_2024: Mapped[str | None] = mapped_column(String(64))
    bs_no_2024: Mapped[str | None] = mapped_column(String(64))
    ward_2026: Mapped[str | None] = mapped_column(String(64))
    ward_s_no: Mapped[str | None] = mapped_column(String(64))
    voter_id: Mapped[str] = mapped_column(String(32), nullable=False)
    first_name: Mapped[str] = mapped_column(String(128), nullable=False)
    surname: Mapped[str | None] = mapped_column(String(128))
    relative_name: Mapped[str | None] = mapped_column(String(128))
    house_no: Mapped[str | None] = mapped_column(String(64))
    gender: Mapped[str] = mapped_column(String(16), nullable=False, default="Unknown")
    age: Mapped[int | None] = mapped_column(Integer)
    caste: Mapped[str | None] = mapped_column(String(128))
    mobile: Mapped[str | None] = mapped_column(String(20))
    c_city: Mapped[str | None] = mapped_column(String(128))
    profession: Mapped[str | None] = mapped_column(String(128))
    party: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    deleted_at: Mapped[str | None] = mapped_column(String(32))
    source_id: Mapped[str | None] = mapped_column(String(64))
