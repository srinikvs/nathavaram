"""Campaign messaging: recipient resolution, create/list, send processing, and controls.

Permissions: any signed-in role may use campaigns (same Campaign section as Public Issues).
Mobile numbers are stored in full and masked in every payload helper.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.campaign_rules import (
    CHANNELS,
    CHANNEL_LABELS,
    KPI_LABELS,
    MAX_PHOTOS_PER_CAMPAIGN,
    PHOTO_CAP_MESSAGE,
    RECIPIENT_STATUSES,
    TARGET_LABELS,
    TARGET_TYPES,
    TARGET_VOTER_FIELDS,
    TOO_MANY_PHOTOS_MESSAGE,
)
from app.campaign_schemas import CampaignCreate, CampaignSearch, RecipientPreview
from app.config import Settings
from app.errors import ServiceError
from app.models import (
    Campaign,
    CampaignDelivery,
    CampaignPhoto,
    CampaignRecipient,
    CampaignScheduleRun,
    User,
    Voter,
)
from app.services import issue_photos
from app.services.campaign_providers import (
    PROVIDER_DRY_RUN,
    build_photo_urls,
    get_provider,
)
from app.services.issues import mask_issue_mobile
from app.validation import dump_dt, is_loose_indian_mobile

CAMPAIGN_NOT_FOUND = "Campaign not found"
PHOTO_NOT_FOUND = "Photo not found"
MOBILE_VISIBLE_CHARS = 4


def _now() -> str:
    return dump_dt(datetime.now(timezone.utc))


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


def _channel(channel: str) -> str:
    value = (channel or "").strip().casefold()
    if value not in CHANNELS:
        raise ServiceError("Channel must be sms or whatsapp", 422, "channel")
    return value


def _require_campaign(db: Session, campaign_id: int, channel: str | None = None) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise ServiceError(CAMPAIGN_NOT_FOUND, 404)
    if channel is not None and campaign.channel != channel:
        raise ServiceError(CAMPAIGN_NOT_FOUND, 404)
    return campaign


def _active_voters_query(db: Session):
    return select(Voter).where(Voter.deleted_at.is_(None), Voter.hard_deleted_at.is_(None))


def _target_column(target_type: str):
    field = TARGET_VOTER_FIELDS.get(target_type)
    if field is None:
        raise ServiceError("Unsupported target type", 422, "target_type")
    return getattr(Voter, field)


def resolve_recipients(db: Session, target_type: str, target_values: list[str]) -> list[Voter]:
    """Active voters matching the target geo values who have a valid Indian mobile."""
    if target_type not in TARGET_TYPES:
        raise ServiceError("Unsupported target type", 422, "target_type")
    if not target_values:
        raise ServiceError("Select at least one target value", 422, "target_values")
    column = _target_column(target_type)
    rows = db.scalars(_active_voters_query(db).where(column.in_(target_values))).all()
    matched: list[Voter] = []
    seen_voters: set[str] = set()
    for voter in rows:
        if voter.voter_id in seen_voters:
            continue
        mobile = (voter.mobile or "").strip()
        if not mobile or not is_loose_indian_mobile(mobile):
            continue
        seen_voters.add(voter.voter_id)
        matched.append(voter)
    return matched


def preview_recipients(db: Session, body: RecipientPreview) -> dict:
    voters = resolve_recipients(db, body.target_type, body.target_values)
    return {
        "target_type": body.target_type,
        "target_values": body.target_values,
        "matched_voters": len(voters),
        "with_mobile": len(voters),
    }


def list_target_options(db: Session, target_type: str) -> list[str]:
    if target_type not in TARGET_TYPES:
        raise ServiceError("Unsupported target type", 422, "target_type")
    column = _target_column(target_type)
    rows = db.scalars(
        select(column)
        .where(
            Voter.deleted_at.is_(None),
            Voter.hard_deleted_at.is_(None),
            column.is_not(None),
            column != "",
        )
        .distinct()
        .order_by(column)
    ).all()
    return [str(value) for value in rows if value]


def store_campaign_photo(data: bytes, photo_dir: Path, campaign_id: int) -> issue_photos.StoredPhoto:
    payload, width, height = issue_photos.normalize_image(data)
    folder = photo_dir / str(campaign_id)
    folder.mkdir(parents=True, exist_ok=True)
    import uuid

    storage_name = f"{campaign_id}/{uuid.uuid4().hex}.jpg"
    (photo_dir / storage_name).write_bytes(payload)
    return issue_photos.StoredPhoto(storage_name=storage_name, width=width, height=height)


def _attach_photos(
    db: Session,
    campaign: Campaign,
    uploads: list[bytes],
    user: User,
    photo_dir: Path,
) -> list[CampaignPhoto]:
    if len(uploads) > MAX_PHOTOS_PER_CAMPAIGN:
        raise ServiceError(TOO_MANY_PHOTOS_MESSAGE, 422, "photos")
    existing = db.scalar(
        select(func.count()).select_from(CampaignPhoto).where(CampaignPhoto.campaign_id == campaign.id)
    ) or 0
    if existing + len(uploads) > MAX_PHOTOS_PER_CAMPAIGN:
        raise ServiceError(PHOTO_CAP_MESSAGE, 422, "photos")
    now = _now()
    photos: list[CampaignPhoto] = []
    for data in uploads:
        stored = store_campaign_photo(data, photo_dir, campaign.id)
        photo = CampaignPhoto(
            campaign_id=campaign.id,
            storage_name=stored.storage_name,
            width=stored.width,
            height=stored.height,
            uploaded_by=user.username,
            uploaded_at=now,
        )
        db.add(photo)
        photos.append(photo)
    db.flush()
    return photos


def create_campaign(
    db: Session,
    channel: str,
    body: CampaignCreate,
    user: User,
    settings: Settings,
    photo_dir: Path,
    photo_uploads: list[bytes] | None = None,
) -> Campaign:
    channel = _channel(channel)
    uploads = photo_uploads or []
    if len(uploads) > MAX_PHOTOS_PER_CAMPAIGN:
        raise ServiceError(TOO_MANY_PHOTOS_MESSAGE, 422, "photos")

    voters = resolve_recipients(db, body.target_type, body.target_values)
    if not voters:
        raise ServiceError(
            "No voters with a valid mobile match the selected target",
            422,
            "target_values",
        )

    provider = get_provider(settings)
    provider_mode = provider.mode_for(channel)
    now = _now()
    status = "scheduled" if body.send_mode == "daily" else "queued"
    campaign = Campaign(
        channel=channel,
        title=body.title,
        message=body.message,
        target_type=body.target_type,
        target_values_json=json.dumps(body.target_values, ensure_ascii=False),
        send_mode=body.send_mode,
        daily_times_json=json.dumps(body.daily_times),
        start_date=body.start_date,
        end_date=body.end_date,
        schedule_active=bool(body.schedule_active) if body.send_mode == "daily" else True,
        status=status if body.send_mode == "daily" and body.schedule_active else (
            "paused" if body.send_mode == "daily" and not body.schedule_active else status
        ),
        provider_mode=provider_mode,
        created_by=user.username,
        created_at=now,
        updated_at=now,
    )
    if body.send_mode == "daily" and not body.schedule_active:
        campaign.status = "paused"
    db.add(campaign)
    db.flush()

    for voter in voters:
        db.add(
            CampaignRecipient(
                campaign_id=campaign.id,
                voter_id=voter.voter_id,
                mobile=(voter.mobile or "").strip(),
                status="initiated",
                created_at=now,
                updated_at=now,
            )
        )
    db.flush()
    _attach_photos(db, campaign, uploads, user, photo_dir)
    db.commit()
    db.refresh(campaign)

    if body.send_mode == "now":
        process_campaign_slot(db, campaign.id, slot_key="now", settings=settings, photo_dir=photo_dir)
        db.refresh(campaign)
    return campaign


def process_campaign_slot(
    db: Session,
    campaign_id: int,
    *,
    slot_key: str,
    settings: Settings,
    photo_dir: Path,
) -> int:
    """Send (or dry-run) every recipient for a slot. Idempotent per campaign/recipient/slot."""
    campaign = _require_campaign(db, campaign_id)
    if campaign.status == "cancelled":
        return 0
    if campaign.send_mode == "daily" and not campaign.schedule_active and slot_key != "now":
        return 0

    recipients = db.scalars(
        select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign.id)
    ).all()
    photos = db.scalars(
        select(CampaignPhoto).where(CampaignPhoto.campaign_id == campaign.id)
    ).all()
    photo_paths = [issue_photos.photo_path(photo_dir, photo.storage_name) for photo in photos]
    photo_ids = [photo.id for photo in photos]
    photo_urls = build_photo_urls(settings, photo_ids)
    provider = get_provider(settings)
    now = _now()
    sent_count = 0

    if campaign.status in {"queued", "scheduled", "paused"} and slot_key == "now":
        campaign.status = "sending"
        campaign.updated_at = now
        db.flush()
    elif campaign.status == "scheduled" and slot_key != "now":
        campaign.status = "sending"
        campaign.updated_at = now
        db.flush()

    for recipient in recipients:
        existing = db.scalar(
            select(CampaignDelivery).where(
                CampaignDelivery.campaign_id == campaign.id,
                CampaignDelivery.recipient_id == recipient.id,
                CampaignDelivery.slot_key == slot_key,
            )
        )
        if existing is not None:
            continue

        recipient.status = "processed"
        recipient.updated_at = now
        delivery = CampaignDelivery(
            campaign_id=campaign.id,
            recipient_id=recipient.id,
            slot_key=slot_key,
            status="processed",
            provider_mode=provider.mode_for(campaign.channel),
            created_at=now,
            updated_at=now,
        )
        db.add(delivery)
        db.flush()

        result = provider.send(
            channel=campaign.channel,
            to_mobile=recipient.mobile,
            title=campaign.title,
            message=campaign.message,
            photo_paths=photo_paths,
            photo_urls=photo_urls,
        )
        stamp = _now()
        if result.ok:
            recipient.status = "sent"
            delivery.status = "sent"
            sent_count += 1
        else:
            recipient.status = "rejected"
            recipient.last_error = result.detail
            delivery.status = "rejected"
            delivery.detail = result.detail
        delivery.provider_mode = result.provider_mode
        delivery.provider_ref = result.provider_ref
        delivery.updated_at = stamp
        recipient.updated_at = stamp
        campaign.provider_mode = result.provider_mode

    if campaign.send_mode == "now":
        campaign.status = "completed"
    elif campaign.schedule_active and campaign.status != "cancelled":
        campaign.status = "scheduled"
    campaign.updated_at = _now()
    db.commit()
    return sent_count


def status_counts(db: Session, channel: str) -> dict[str, int]:
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


def search_campaigns(
    db: Session, channel: str, query: CampaignSearch
) -> tuple[list[Campaign], int]:
    channel = _channel(channel)
    filters = [Campaign.channel == channel]
    if query.q and query.q.strip():
        needle = f"%{query.q.strip()}%"
        filters.append(
            or_(
                Campaign.title.ilike(needle),
                Campaign.message.ilike(needle),
                Campaign.created_by.ilike(needle),
            )
        )
    total = db.scalar(select(func.count()).select_from(Campaign).where(*filters)) or 0
    rows = db.scalars(
        select(Campaign)
        .where(*filters)
        .order_by(Campaign.id.desc())
        .offset((query.page - 1) * query.page_size)
        .limit(query.page_size)
    ).all()
    return list(rows), int(total)


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
        "last_error": recipient.last_error,
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
        "detail": delivery.detail,
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


def campaign_detail(db: Session, campaign_id: int, channel: str | None = None) -> dict:
    campaign = _require_campaign(db, campaign_id, channel)
    recipients = db.scalars(
        select(CampaignRecipient)
        .where(CampaignRecipient.campaign_id == campaign.id)
        .order_by(CampaignRecipient.id)
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
    payload = campaign_payload(campaign, recipient_total=len(recipients))
    payload["recipients"] = [recipient_payload(row) for row in recipients]
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


def pause_campaign(db: Session, campaign_id: int, channel: str, user: User) -> Campaign:
    campaign = _require_campaign(db, campaign_id, channel)
    if campaign.send_mode != "daily":
        raise ServiceError("Only daily schedules can be paused", 422, "status")
    if campaign.status == "cancelled":
        raise ServiceError("Cancelled campaigns cannot be paused", 409, "status")
    campaign.schedule_active = False
    campaign.status = "paused"
    campaign.updated_at = _now()
    db.commit()
    db.refresh(campaign)
    return campaign


def resume_campaign(db: Session, campaign_id: int, channel: str, user: User) -> Campaign:
    campaign = _require_campaign(db, campaign_id, channel)
    if campaign.send_mode != "daily":
        raise ServiceError("Only daily schedules can be resumed", 422, "status")
    if campaign.status == "cancelled":
        raise ServiceError("Cancelled campaigns cannot be resumed", 409, "status")
    campaign.schedule_active = True
    campaign.status = "scheduled"
    campaign.updated_at = _now()
    db.commit()
    db.refresh(campaign)
    return campaign


def cancel_campaign(db: Session, campaign_id: int, channel: str, user: User) -> Campaign:
    campaign = _require_campaign(db, campaign_id, channel)
    if campaign.status == "cancelled":
        raise ServiceError("Campaign is already cancelled", 409, "status")
    now = _now()
    campaign.status = "cancelled"
    campaign.schedule_active = False
    campaign.cancelled_at = now
    campaign.updated_at = now
    db.commit()
    db.refresh(campaign)
    return campaign


def delete_campaign(db: Session, campaign_id: int, channel: str, photo_dir: Path) -> None:
    campaign = _require_campaign(db, campaign_id, channel)
    photos = db.scalars(
        select(CampaignPhoto).where(CampaignPhoto.campaign_id == campaign.id)
    ).all()
    for photo in photos:
        issue_photos.delete_photo_file(photo_dir, photo.storage_name)
    db.delete(campaign)
    db.commit()


def get_photo(db: Session, photo_id: int) -> CampaignPhoto:
    photo = db.get(CampaignPhoto, photo_id)
    if photo is None:
        raise ServiceError(PHOTO_NOT_FOUND, 404)
    return photo


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
