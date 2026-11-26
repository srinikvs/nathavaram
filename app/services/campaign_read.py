"""Campaign read models: payloads, KPI cards, and detail assembly."""

from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.campaign_rules import (
    CHANNEL_LABELS,
    KPI_LABELS,
    RECIPIENTS_PAGE_SIZE_DEFAULT,
    RECIPIENTS_PAGE_SIZE_MAX,
    RECIPIENT_STATUSES,
    TARGET_LABELS,
)
from app.models import (
    Campaign,
    CampaignDelivery,
    CampaignPhoto,
    CampaignRecipient,
    CampaignScheduleRun,
)
from app.services.campaign_providers import scrub_phone_text
from app.services.issues import mask_issue_mobile


def mask_campaign_mobile(value: str | None) -> str | None:
    """Same masking rule as Public Issues: last 4 visible, shorter values fully masked."""
    return mask_issue_mobile(value)


def _parse_json_list(raw: str) -> list[str]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]


def photo_payload(photo: CampaignPhoto) -> dict:
    return {
        "id": photo.id,
        "width": photo.width,
        "height": photo.height,
        "uploaded_by": photo.uploaded_by,
        "uploaded_at": photo.uploaded_at,
        "url": f"/api/campaigns/photos/{photo.id}/file",
    }


def recipient_payload(recipient: CampaignRecipient) -> dict:
    return {
        "id": recipient.id,
        "voter_id": recipient.voter_id,
        "mobile": mask_campaign_mobile(recipient.mobile),
        "status": recipient.status,
        "last_error": scrub_phone_text(recipient.last_error),
        "created_at": recipient.created_at,
        "updated_at": recipient.updated_at,
    }


def delivery_payload(delivery: CampaignDelivery) -> dict:
    return {
        "id": delivery.id,
        "recipient_id": delivery.recipient_id,
        "slot_key": delivery.slot_key,
        "status": delivery.status,
        "provider_mode": delivery.provider_mode,
        "provider_ref": delivery.provider_ref,
        "detail": scrub_phone_text(delivery.detail),
        "created_at": delivery.created_at,
        "updated_at": delivery.updated_at,
    }


def _target_summary(campaign: Campaign) -> str:
    values = _parse_json_list(campaign.target_values_json)
    label = TARGET_LABELS.get(campaign.target_type, campaign.target_type)
    if not values:
        return label
    shown = ", ".join(values[:5])
    if len(values) > 5:
        shown += f" (+{len(values) - 5})"
    return f"{label}: {shown}"


def _schedule_summary(campaign: Campaign) -> str:
    if campaign.send_mode == "now":
        return "Send now"
    times = _parse_json_list(campaign.daily_times_json)
    parts = ["Daily " + ", ".join(times) + " IST" if times else "Daily"]
    if campaign.start_date or campaign.end_date:
        parts.append(f"{campaign.start_date or '…'} → {campaign.end_date or '…'}")
    if campaign.status == "paused" or not campaign.schedule_active:
        parts.append("Paused")
    return " · ".join(parts)


def campaign_payload(campaign: Campaign, *, recipient_total: int | None = None) -> dict:
    return {
        "id": campaign.id,
        "channel": campaign.channel,
        "channel_label": CHANNEL_LABELS.get(campaign.channel, campaign.channel),
        "title": campaign.title,
        "message": campaign.message,
        "from": campaign.created_by,
        "to": _target_summary(campaign),
        "target_type": campaign.target_type,
        "target_values": _parse_json_list(campaign.target_values_json),
        "send_mode": campaign.send_mode,
        "daily_times": _parse_json_list(campaign.daily_times_json),
        "start_date": campaign.start_date,
        "end_date": campaign.end_date,
        "schedule_active": campaign.schedule_active,
        "schedule": _schedule_summary(campaign),
        "status": campaign.status,
        "provider_mode": campaign.provider_mode,
        "created_by": campaign.created_by,
        "created_at": campaign.created_at,
        "updated_at": campaign.updated_at,
        "cancelled_at": campaign.cancelled_at,
        "recipient_total": recipient_total,
    }


def status_counts(db: Session, channel: str) -> dict[str, int]:
    from app.services.campaigns import _channel

    channel = _channel(channel)
    counts = {status: 0 for status in RECIPIENT_STATUSES}
    rows = db.execute(
        select(CampaignRecipient.status, func.count())
        .join(Campaign, Campaign.id == CampaignRecipient.campaign_id)
        .where(Campaign.channel == channel)
        .group_by(CampaignRecipient.status)
    ).all()
    for status, count in rows:
        if status in counts:
            counts[status] = int(count)
    return counts


def kpi_cards(db: Session, channel: str) -> list[dict]:
    counts = status_counts(db, channel)
    icons = {
        "initiated": "≡",
        "processed": "⌛",
        "sent": "✓",
        "rejected": "👎",
    }
    return [
        {
            "key": key,
            "label": KPI_LABELS[key],
            "count": counts[key],
            "icon": icons[key],
        }
        for key in RECIPIENT_STATUSES
    ]


def campaign_summary_payload(db: Session, campaign: Campaign) -> dict:
    total = db.scalar(
        select(func.count())
        .select_from(CampaignRecipient)
        .where(CampaignRecipient.campaign_id == campaign.id)
    ) or 0
    status_rows = db.execute(
        select(CampaignRecipient.status, func.count())
        .where(CampaignRecipient.campaign_id == campaign.id)
        .group_by(CampaignRecipient.status)
    ).all()
    by_status = {status: 0 for status in RECIPIENT_STATUSES}
    for status, count in status_rows:
        if status in by_status:
            by_status[status] = int(count)
    photo_count = db.scalar(
        select(func.count())
        .select_from(CampaignPhoto)
        .where(CampaignPhoto.campaign_id == campaign.id)
    ) or 0
    payload = campaign_payload(campaign, recipient_total=int(total))
    payload["recipient_counts"] = by_status
    payload["photo_count"] = int(photo_count)
    return payload


def campaign_detail(
    db: Session,
    campaign_id: int,
    channel: str | None = None,
    *,
    recipients_page: int = 1,
    recipients_page_size: int = RECIPIENTS_PAGE_SIZE_DEFAULT,
) -> dict:
    from app.services.campaigns import _require_campaign

    campaign = _require_campaign(db, campaign_id, channel)
    page = max(1, recipients_page)
    page_size = min(max(1, recipients_page_size), RECIPIENTS_PAGE_SIZE_MAX)
    recipient_total = db.scalar(
        select(func.count())
        .select_from(CampaignRecipient)
        .where(CampaignRecipient.campaign_id == campaign.id)
    ) or 0
    recipients = db.scalars(
        select(CampaignRecipient)
        .where(CampaignRecipient.campaign_id == campaign.id)
        .order_by(CampaignRecipient.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    photos = db.scalars(
        select(CampaignPhoto)
        .where(CampaignPhoto.campaign_id == campaign.id)
        .order_by(CampaignPhoto.id)
    ).all()
    deliveries = db.scalars(
        select(CampaignDelivery)
        .where(CampaignDelivery.campaign_id == campaign.id)
        .order_by(CampaignDelivery.id.desc())
        .limit(200)
    ).all()
    runs = db.scalars(
        select(CampaignScheduleRun)
        .where(CampaignScheduleRun.campaign_id == campaign.id)
        .order_by(CampaignScheduleRun.id.desc())
        .limit(50)
    ).all()
    payload = campaign_payload(campaign, recipient_total=int(recipient_total))
    payload["recipients"] = [recipient_payload(row) for row in recipients]
    payload["recipients_page"] = page
    payload["recipients_page_size"] = page_size
    payload["recipients_total"] = int(recipient_total)
    payload["photos"] = [photo_payload(row) for row in photos]
    payload["deliveries"] = [delivery_payload(row) for row in deliveries]
    payload["schedule_runs"] = [
        {
            "id": run.id,
            "run_date": run.run_date,
            "run_time": run.run_time,
            "claimed_at": run.claimed_at,
            "completed_at": run.completed_at,
            "recipient_count": run.recipient_count,
        }
        for run in runs
    ]
    return payload
