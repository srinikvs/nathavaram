"""Add campaign create idempotency keys.

Additive only: campaign_idempotency_keys maps (user, channel, key) to a campaign id
so retries with the same Idempotency-Key return the existing campaign.

Revision ID: 0013_campaign_idempotency
Revises: 0012_campaign_messaging
Create Date: 2026-11-26
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_campaign_idempotency"
down_revision: Union[str, None] = "0012_campaign_messaging"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "campaign_idempotency_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("idem_key", sa.String(length=128), nullable=False),
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.UniqueConstraint(
            "user_id", "channel", "idem_key", name="uq_campaign_idempotency_user_channel_key"
        ),
    )
    op.create_index(
        "ix_campaign_idempotency_keys_user_id", "campaign_idempotency_keys", ["user_id"]
    )
    op.create_index(
        "ix_campaign_idempotency_keys_campaign_id",
        "campaign_idempotency_keys",
        ["campaign_id"],
    )


def downgrade() -> None:
    op.drop_table("campaign_idempotency_keys")
