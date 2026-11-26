"""Campaign messaging: recipient resolution, create/list, send processing, and controls.

Manager/admin may create and mutate campaigns. Mobile numbers are stored in full (until a
voter is hard-deleted) and masked in every payload helper. Provider detail text is scrubbed.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.campaign_rules import (
    CHANNELS,
    CHANNEL_LABELS,
    IDEMPOTENCY_KEY_MAX_LENGTH,
    KPI_LABELS,
    MAX_PHOTOS_PER_CAMPAIGN,
    OWNERSHIP_REFUSED_MESSAGE,
    PHOTO_CAP_MESSAGE,
    RATE_LIMIT_MESSAGE,
    RECIPIENTS_PAGE_SIZE_DEFAULT,
    RECIPIENTS_PAGE_SIZE_MAX,
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
    CampaignIdempotencyKey,
    CampaignPhoto,
    CampaignRecipient,
    CampaignScheduleRun,
    User,
    Voter,
)
from app.permissions import can_manage_campaigns, is_admin
from app.services import issue_photos
from app.services.campaign_providers import (
    build_photo_urls,
    get_provider,
    scrub_phone_text,
)
from app.services.issues import mask_issue_mobile
from app.validation import dump_dt, is_loose_indian_mobile, parse_dt

CAMPAIGN_NOT_FOUND = "Campaign not found"
PHOTO_NOT_FOUND = "Photo not found"
SCRUBBED_MOBILE = "****"


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


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _require_campaign(db: Session, campaign_id: int, channel: str | None = None) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise ServiceError(CAMPAIGN_NOT_FOUND, 404)
    if channel is not None and campaign.channel != channel:
        raise ServiceError(CAMPAIGN_NOT_FOUND, 404)
    return campaign


def _assert_can_mutate(campaign: Campaign, user: User) -> None:
    if is_admin(user) or campaign.created_by == user.username:
        return
    raise ServiceError(OWNERSHIP_REFUSED_MESSAGE, 403, "campaign")


def _assert_can_manage(user: User) -> None:
    if not can_manage_campaigns(user):
        raise ServiceError("Manager or admin role required", 403)


def _active_voters_query():
    return select(Voter).where(Voter.deleted_at.is_(None), Voter.hard_deleted_at.is_(None))


def _target_column(target_type: str):
    field = TARGET_VOTER_FIELDS.get(target_type)
    if field is None:
        raise ServiceError("Unsupported target type", 422, "target_type")
    return getattr(Voter, field)


def match_active_voters(db: Session, target_type: str, target_values: list[str]) -> list[Voter]:
    """Active voters matching the target geo values (mobile may be missing or invalid)."""
    if target_type not in TARGET_TYPES:
        raise ServiceError("Unsupported target type", 422, "target_type")
    if not target_values:
        raise ServiceError("Select at least one target value", 422, "target_values")
    column = _target_column(target_type)
    rows = db.scalars(_active_voters_query().where(column.in_(target_values))).all()
    matched: list[Voter] = []
    seen: set[str] = set()
    for voter in rows:
        if voter.voter_id in seen:
            continue
        seen.add(voter.voter_id)
        matched.append(voter)
    return matched


def voters_with_valid_mobile(voters: list[Voter]) -> list[Voter]:
    result: list[Voter] = []
    for voter in voters:
        mobile = (voter.mobile or "").strip()
        if mobile and is_loose_indian_mobile(mobile):
            result.append(voter)
    return result


def resolve_recipients(db: Session, target_type: str, target_values: list[str]) -> list[Voter]:
    """Active voters matching the target geo values who have a valid Indian mobile."""
    return voters_with_valid_mobile(match_active_voters(db, target_type, target_values))


def preview_recipients(db: Session, body: RecipientPreview) -> dict:
    matched = match_active_voters(db, body.target_type, body.target_values)
    with_mobile = voters_with_valid_mobile(matched)
    return {
        "target_type": body.target_type,
        "target_values": body.target_values,
        "matched_voters": len(matched),
        "with_mobile": len(with_mobile),
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
        select(func.count())
        .select_from(CampaignPhoto)
        .where(CampaignPhoto.campaign_id == campaign.id)
    ) or 0
    if existing + len(uploads) > MAX_PHOTOS_PER_CAMPAIGN:
        raise ServiceError(PHOTO_CAP_MESSAGE, 422, "photos")
    now = _now()
    photos: list[CampaignPhoto] = []
    written: list[str] = []
    try:
        for data in uploads:
            stored = store_campaign_photo(data, photo_dir, campaign.id)
            written.append(stored.storage_name)
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
    except Exception:
        for name in written:
            issue_photos.delete_photo_file(photo_dir, name)
        raise
    return photos


def _check_create_rate_limit(db: Session, user: User, settings: Settings) -> None:
    since = dump_dt(datetime.now(timezone.utc) - timedelta(hours=1))
    count = db.scalar(
        select(func.count())
        .select_from(Campaign)
        .where(Campaign.created_by == user.username, Campaign.created_at >= since)
    ) or 0
    if count >= settings.campaign_create_rate_limit:
        raise ServiceError(RATE_LIMIT_MESSAGE, 429)


def find_idempotent_campaign(
    db: Session, user: User, channel: str, idem_key: str | None
) -> Campaign | None:
    if not idem_key:
        return None
    key = idem_key.strip()[:IDEMPOTENCY_KEY_MAX_LENGTH]
    if not key:
        return None
    row = db.scalar(
        select(CampaignIdempotencyKey).where(
            CampaignIdempotencyKey.user_id == user.id,
            CampaignIdempotencyKey.channel == channel,
            CampaignIdempotencyKey.idem_key == key,
        )
    )
    if row is None:
        return None
    return db.get(Campaign, row.campaign_id)


def create_campaign(
    db: Session,
    channel: str,
    body: CampaignCreate,
    user: User,
    settings: Settings,
    photo_dir: Path,
    photo_uploads: list[bytes] | None = None,
    *,
    idempotency_key: str | None = None,
) -> Campaign:
    _assert_can_manage(user)
    channel = _channel(channel)
    existing = find_idempotent_campaign(db, user, channel, idempotency_key)
    if existing is not None:
        return existing

    uploads = photo_uploads or []
    if len(uploads) > MAX_PHOTOS_PER_CAMPAIGN:
        raise ServiceError(TOO_MANY_PHOTOS_MESSAGE, 422, "photos")

    _check_create_rate_limit(db, user, settings)

    voters = resolve_recipients(db, body.target_type, body.target_values)
    if not voters:
        raise ServiceError(
            "No voters with a valid mobile match the selected target",
            422,
            "target_values",
        )
    if len(voters) > settings.campaign_max_recipients:
        raise ServiceError(
            f"A campaign can target at most {settings.campaign_max_recipients} recipients",
            422,
            "target_values",
        )

    provider = get_provider(settings)
    provider_mode = provider.mode_for(channel)
    now = _now()
    status = "queued"
    if body.send_mode == "daily":
        status = "scheduled" if body.schedule_active else "paused"
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
        status=status,
        provider_mode=provider_mode,
        created_by=user.username,
        created_at=now,
        updated_at=now,
    )
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

    if idempotency_key and idempotency_key.strip():
        db.add(
            CampaignIdempotencyKey(
                user_id=user.id,
                channel=channel,
                idem_key=idempotency_key.strip()[:IDEMPOTENCY_KEY_MAX_LENGTH],
                campaign_id=campaign.id,
                created_at=now,
            )
        )
    db.commit()
    db.refresh(campaign)

    if body.send_mode == "now":
        process_campaign_slot(
            db, campaign.id, slot_key="now", settings=settings, photo_dir=photo_dir
        )
        db.refresh(campaign)
    return campaign


def _mobile_for_send(db: Session, recipient: CampaignRecipient) -> str | None:
    """Prefer the live voter mobile when the voter is still active; else stored copy."""
    voter = db.get(Voter, recipient.voter_id)
    if (
        voter is not None
        and voter.deleted_at is None
        and voter.hard_deleted_at is None
        and voter.mobile
        and is_loose_indian_mobile(voter.mobile.strip())
    ):
        return voter.mobile.strip()
    stored = (recipient.mobile or "").strip()
    if stored and stored != SCRUBBED_MOBILE and is_loose_indian_mobile(stored):
        return stored
    return None


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
    ``sent`` / ``rejected`` and committed again so a mid-loop failure keeps prior results.
    """
    campaign = _require_campaign(db, campaign_id)
    if campaign.status == "cancelled":
        return 0
    if campaign.send_mode == "daily" and not campaign.schedule_active and slot_key != "now":
        return 0

    recipients = db.scalars(
        select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign.id)
    ).all()
    already = set(
        db.scalars(
            select(CampaignDelivery.recipient_id).where(
                CampaignDelivery.campaign_id == campaign.id,
                CampaignDelivery.slot_key == slot_key,
            )
        ).all()
    )
    photos = db.scalars(
        select(CampaignPhoto).where(CampaignPhoto.campaign_id == campaign.id)
    ).all()
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
        if recipient.id in already:
            continue

        mobile = _mobile_for_send(db, recipient)
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
            delivery.detail = "No valid mobile available for recipient"
            recipient.status = "rejected"
            recipient.last_error = delivery.detail
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
            raise

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
    if campaign.send_mode == "now":
        campaign.status = "completed"
    elif campaign.schedule_active and campaign.status != "cancelled":
        campaign.status = "scheduled"
    campaign.updated_at = _now()
    db.commit()
    return sent_count


def scrub_recipient_mobiles_for_voter(db: Session, voter_id: str) -> int:
    """Clear stored campaign recipient mobiles when a voter is hard-deleted."""
    rows = db.scalars(
        select(CampaignRecipient).where(CampaignRecipient.voter_id == voter_id)
    ).all()
    for row in rows:
        row.mobile = SCRUBBED_MOBILE
        row.updated_at = _now()
    return len(rows)


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
) -> tuple[list[Campaign], int, dict[int, int]]:
    channel = _channel(channel)
    filters = [Campaign.channel == channel]
    if query.q and query.q.strip():
        needle = f"%{_escape_like(query.q.strip())}%"
        filters.append(
            or_(
                Campaign.title.ilike(needle, escape="\\"),
                Campaign.message.ilike(needle, escape="\\"),
                Campaign.created_by.ilike(needle, escape="\\"),
            )
        )
    total = db.scalar(select(func.count()).select_from(Campaign).where(*filters)) or 0
    rows = list(
        db.scalars(
            select(Campaign)
            .where(*filters)
            .order_by(Campaign.id.desc())
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        ).all()
    )
    recipient_totals: dict[int, int] = {}
    if rows:
        ids = [row.id for row in rows]
        for campaign_id, count in db.execute(
            select(CampaignRecipient.campaign_id, func.count())
            .where(CampaignRecipient.campaign_id.in_(ids))
            .group_by(CampaignRecipient.campaign_id)
        ).all():
            recipient_totals[int(campaign_id)] = int(count)
    return rows, int(total), recipient_totals


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


def pause_campaign(db: Session, campaign_id: int, channel: str, user: User) -> Campaign:
    _assert_can_manage(user)
    campaign = _require_campaign(db, campaign_id, channel)
    _assert_can_mutate(campaign, user)
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
    _assert_can_manage(user)
    campaign = _require_campaign(db, campaign_id, channel)
    _assert_can_mutate(campaign, user)
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
    _assert_can_manage(user)
    campaign = _require_campaign(db, campaign_id, channel)
    _assert_can_mutate(campaign, user)
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


def delete_campaign(
    db: Session, campaign_id: int, channel: str, photo_dir: Path, user: User
) -> None:
    _assert_can_manage(user)
    campaign = _require_campaign(db, campaign_id, channel)
    _assert_can_mutate(campaign, user)
    photos = list(
        db.scalars(select(CampaignPhoto).where(CampaignPhoto.campaign_id == campaign.id)).all()
    )
    storage_names = [photo.storage_name for photo in photos]
    db.delete(campaign)
    db.commit()
    for name in storage_names:
        issue_photos.delete_photo_file(photo_dir, name)


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
