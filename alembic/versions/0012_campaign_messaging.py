"""Add Campaign Messaging tables for SMS and WhatsApp.

Additive only: campaigns, photos, recipients, deliveries, and schedule-run claims.
No existing table or row is changed.

Revision ID: 0012_campaign_messaging
Revises: 0011_public_issues
Create Date: 2026-11-26
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_campaign_messaging"
down_revision: Union[str, None] = "0011_public_issues"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CHANNEL_CHECK = "channel IN ('sms', 'whatsapp')"
TARGET_CHECK = "target_type IN ('ward', 'booth', 'panchayath', 'mandal', 'constituency')"
SEND_MODE_CHECK = "send_mode IN ('now', 'daily')"
CAMPAIGN_STATUS_CHECK = (
    "status IN ('queued', 'sending', 'scheduled', 'paused', 'completed', 'cancelled')"
)
RECIPIENT_STATUS_CHECK = "status IN ('initiated', 'processed', 'sent', 'rejected')"


def upgrade() -> None:
    op.create_table(
        "campaigns",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_values_json", sa.Text(), nullable=False),
        sa.Column("send_mode", sa.String(length=16), nullable=False),
        sa.Column("daily_times_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("start_date", sa.String(length=10), nullable=True),
        sa.Column("end_date", sa.String(length=10), nullable=True),
        sa.Column("schedule_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("provider_mode", sa.String(length=16), nullable=False, server_default="dry_run"),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.Column("cancelled_at", sa.String(length=32), nullable=True),
        sa.CheckConstraint(CHANNEL_CHECK, name="ck_campaigns_channel"),
        sa.CheckConstraint(TARGET_CHECK, name="ck_campaigns_target_type"),
        sa.CheckConstraint(SEND_MODE_CHECK, name="ck_campaigns_send_mode"),
        sa.CheckConstraint(CAMPAIGN_STATUS_CHECK, name="ck_campaigns_status"),
    )
    op.create_index("ix_campaigns_channel", "campaigns", ["channel"])
    op.create_index("ix_campaigns_status", "campaigns", ["status"])

    op.create_table(
        "campaign_photos",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("storage_name", sa.String(length=255), nullable=False, unique=True),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("uploaded_by", sa.String(length=64), nullable=False),
        sa.Column("uploaded_at", sa.String(length=32), nullable=False),
    )
    op.create_index("ix_campaign_photos_campaign_id", "campaign_photos", ["campaign_id"])

    op.create_table(
        "campaign_recipients",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("voter_id", sa.String(length=32), nullable=False),
        sa.Column("mobile", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.CheckConstraint(RECIPIENT_STATUS_CHECK, name="ck_campaign_recipients_status"),
    )
    op.create_index("ix_campaign_recipients_campaign_id", "campaign_recipients", ["campaign_id"])
    op.create_index("ix_campaign_recipients_voter_id", "campaign_recipients", ["voter_id"])
    op.create_index("ix_campaign_recipients_status", "campaign_recipients", ["status"])

    op.create_table(
        "campaign_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recipient_id",
            sa.Integer(),
            sa.ForeignKey("campaign_recipients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slot_key", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("provider_mode", sa.String(length=16), nullable=False),
        sa.Column("provider_ref", sa.String(length=128), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.CheckConstraint(RECIPIENT_STATUS_CHECK, name="ck_campaign_deliveries_status"),
        sa.UniqueConstraint(
            "campaign_id", "recipient_id", "slot_key", name="uq_campaign_delivery_slot"
        ),
    )
    op.create_index("ix_campaign_deliveries_campaign_id", "campaign_deliveries", ["campaign_id"])
    op.create_index("ix_campaign_deliveries_recipient_id", "campaign_deliveries", ["recipient_id"])
    op.create_index("ix_campaign_deliveries_slot_key", "campaign_deliveries", ["slot_key"])

    op.create_table(
        "campaign_schedule_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_date", sa.String(length=10), nullable=False),
        sa.Column("run_time", sa.String(length=5), nullable=False),
        sa.Column("claimed_at", sa.String(length=32), nullable=False),
        sa.Column("completed_at", sa.String(length=32), nullable=True),
        sa.Column("recipient_count", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint(
            "campaign_id", "run_date", "run_time", name="uq_campaign_schedule_run_slot"
        ),
    )
    op.create_index(
        "ix_campaign_schedule_runs_campaign_id", "campaign_schedule_runs", ["campaign_id"]
    )


def downgrade() -> None:
    op.drop_table("campaign_schedule_runs")
    op.drop_table("campaign_deliveries")
    op.drop_table("campaign_recipients")
    op.drop_table("campaign_photos")
    op.drop_table("campaigns")
