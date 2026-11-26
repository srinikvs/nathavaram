"""Campaign send-slot processing: resolve live mobiles and deliver per recipient."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.campaign_rules import MISSING_MOBILE_MESSAGE, VOTER_REMOVED_MESSAGE
from app.config import Settings
from app.models import Campaign, CampaignDelivery, CampaignPhoto, CampaignRecipient, Voter
from app.services import issue_photos
from app.services.campaign_providers import (
    build_photo_urls,
    get_provider,
    scrub_phone_text,
)
from app.validation import dump_dt, is_loose_indian_mobile


def _now() -> str:
    return dump_dt(datetime.now(timezone.utc))


def live_mobile_for_send(db: Session, recipient: CampaignRecipient) -> tuple[str | None, str | None]:
    """Return (mobile, reject_reason). Mobile comes only from an active voter row.

    Soft-deleted, hard-deleted, missing, or invalid-mobile voters never receive a message.
    Stored recipient.mobile is display-only (masked) and is never used for sending.
    """
    voter = db.get(Voter, recipient.voter_id)
    if voter is None or voter.deleted_at is not None or voter.hard_deleted_at is not None:
        return None, VOTER_REMOVED_MESSAGE
    mobile = (voter.mobile or "").strip()
    if not mobile or not is_loose_indian_mobile(mobile):
        return None, MISSING_MOBILE_MESSAGE
    return mobile, None


def process_campaign_slot(
    db: Session,
    campaign_id: int,
    *,
    slot_key: str,
    settings: Settings,
    photo_dir: Path,
) -> int:
    """Send (or dry-run) every recipient for a slot. Idempotent per campaign/recipient/slot.

    Each delivery is committed as ``initiated`` before the provider call, then updated to
    ``sent`` / ``rejected``. Soft-deleted voters and cancel mid-send stop further sends.
    Provider exceptions reject that recipient and continue so the campaign never stays
    stuck in ``sending``.
    """
    from app.services.campaigns import _require_campaign

    campaign = _require_campaign(db, campaign_id)
    if campaign.status == "cancelled":
        return 0
    if campaign.send_mode == "daily" and not campaign.schedule_active and slot_key != "now":
        return 0

    recipients = list(
        db.scalars(
            select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign.id)
        ).all()
    )
    already = set(
        db.scalars(
            select(CampaignDelivery.recipient_id).where(
                CampaignDelivery.campaign_id == campaign.id,
                CampaignDelivery.slot_key == slot_key,
            )
        ).all()
    )
    photos = list(
        db.scalars(select(CampaignPhoto).where(CampaignPhoto.campaign_id == campaign.id)).all()
    )
    photo_paths = [issue_photos.photo_path(photo_dir, photo.storage_name) for photo in photos]
    photo_ids = [photo.id for photo in photos]
    photo_urls = build_photo_urls(settings, photo_ids) if campaign.channel == "sms" else []
    provider = get_provider(settings)
    now = _now()
    sent_count = 0

    if campaign.status in {"queued", "scheduled", "paused"} and slot_key == "now":
        campaign.status = "sending"
        campaign.updated_at = now
        db.commit()
    elif campaign.status == "scheduled" and slot_key != "now":
        campaign.status = "sending"
        campaign.updated_at = now
        db.commit()

    for recipient in recipients:
        db.expire_all()
        campaign = _require_campaign(db, campaign_id)
        if campaign.status == "cancelled":
            break
        if recipient.id in already:
            continue

        mobile, reject_reason = live_mobile_for_send(db, recipient)
        stamp = _now()
        delivery = CampaignDelivery(
            campaign_id=campaign.id,
            recipient_id=recipient.id,
            slot_key=slot_key,
            status="initiated",
            provider_mode=provider.mode_for(campaign.channel),
            created_at=stamp,
            updated_at=stamp,
        )
        db.add(delivery)
        recipient.status = "processed"
        recipient.updated_at = stamp
        db.commit()
        db.refresh(delivery)

        if mobile is None:
            delivery.status = "rejected"
            delivery.detail = reject_reason
            recipient.status = "rejected"
            recipient.last_error = reject_reason
            delivery.updated_at = _now()
            recipient.updated_at = delivery.updated_at
            db.commit()
            continue

        try:
            result = provider.send(
                channel=campaign.channel,
                to_mobile=mobile,
                title=campaign.title,
                message=campaign.message,
                photo_paths=photo_paths,
                photo_urls=photo_urls,
            )
        except Exception as exc:
            detail = scrub_phone_text(str(exc)[:300]) or "Provider error"
            delivery.status = "rejected"
            delivery.detail = detail
            recipient.status = "rejected"
            recipient.last_error = detail
            delivery.updated_at = _now()
            recipient.updated_at = delivery.updated_at
            db.commit()
            continue

        stamp = _now()
        safe_detail = scrub_phone_text(result.detail)
        if result.ok:
            recipient.status = "sent"
            delivery.status = "sent"
            sent_count += 1
        else:
            recipient.status = "rejected"
            recipient.last_error = safe_detail
            delivery.status = "rejected"
            delivery.detail = safe_detail
        delivery.provider_mode = result.provider_mode
        delivery.provider_ref = result.provider_ref
        delivery.updated_at = stamp
        recipient.updated_at = stamp
        campaign.provider_mode = result.provider_mode
        campaign.updated_at = stamp
        db.commit()

    campaign = _require_campaign(db, campaign_id)
    if campaign.status == "cancelled":
        campaign.updated_at = _now()
        db.commit()
        return sent_count
    if campaign.send_mode == "now":
        campaign.status = "completed"
    elif campaign.schedule_active:
        campaign.status = "scheduled"
    campaign.updated_at = _now()
    db.commit()
    return sent_count
