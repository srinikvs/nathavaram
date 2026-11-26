"""Campaign messaging: create, mutate, targeting, and scrub helpers.

Manager/admin may create and mutate campaigns. ``campaign_recipients.mobile`` stores only a
masked display value; send-time resolution always loads the live voter mobile.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.campaign_rules import (
    CHANNELS,
    IDEMPOTENCY_KEY_MAX_LENGTH,
    MAX_PHOTOS_PER_CAMPAIGN,
    OWNERSHIP_REFUSED_MESSAGE,
    PHOTO_CAP_MESSAGE,
    RATE_LIMIT_MESSAGE,
    SCRUBBED_MOBILE,
    TARGET_TYPES,
    TARGET_VOTER_FIELDS,
    TOO_MANY_PHOTOS_MESSAGE,
)
from app.campaign_schemas import CampaignCreate, CampaignSearch, RecipientPreview
from app.config import Settings
from app.errors import ServiceError
from app.models import (
    Campaign,
    CampaignIdempotencyKey,
    CampaignPhoto,
    CampaignRecipient,
    User,
    Voter,
)
from app.permissions import can_manage_campaigns, is_admin
from app.services import issue_photos
from app.services.campaign_providers import get_provider
from app.services.campaign_read import (
    campaign_detail,
    campaign_payload,
    campaign_summary_payload,
    kpi_cards,
    mask_campaign_mobile,
    status_counts,
)
from app.services.campaign_send import process_campaign_slot
from app.validation import dump_dt, is_loose_indian_mobile

CAMPAIGN_NOT_FOUND = "Campaign not found"
PHOTO_NOT_FOUND = "Photo not found"

# Re-exports for routers and tests.
__all__ = [
    "CAMPAIGN_NOT_FOUND",
    "PHOTO_NOT_FOUND",
    "SCRUBBED_MOBILE",
    "campaign_detail",
    "campaign_payload",
    "campaign_summary_payload",
    "cancel_campaign",
    "create_campaign",
    "delete_campaign",
    "find_idempotent_campaign",
    "get_photo",
    "kpi_cards",
    "list_target_options",
    "mask_campaign_mobile",
    "match_active_voters",
    "pause_campaign",
    "preview_recipients",
    "process_campaign_slot",
    "resolve_recipients",
    "resume_campaign",
    "scrub_recipient_mobiles_for_voter",
    "scrub_recipient_mobiles_for_voters",
    "search_campaigns",
    "status_counts",
    "voters_with_valid_mobile",
]


def _now() -> str:
    return dump_dt(datetime.now(timezone.utc))


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
) -> list[str]:
    """Attach photos; return storage names written (for cleanup on commit failure)."""
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
    written: list[str] = []
    try:
        for data in uploads:
            stored = store_campaign_photo(data, photo_dir, campaign.id)
            written.append(stored.storage_name)
            db.add(
                CampaignPhoto(
                    campaign_id=campaign.id,
                    storage_name=stored.storage_name,
                    width=stored.width,
                    height=stored.height,
                    uploaded_by=user.username,
                    uploaded_at=now,
                )
            )
        db.flush()
    except Exception:
        for name in written:
            issue_photos.delete_photo_file(photo_dir, name)
        raise
    return written


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
        # Store masked display only; send-time always resolves the live voter mobile.
        db.add(
            CampaignRecipient(
                campaign_id=campaign.id,
                voter_id=voter.voter_id,
                mobile=mask_campaign_mobile((voter.mobile or "").strip()) or SCRUBBED_MOBILE,
                status="initiated",
                created_at=now,
                updated_at=now,
            )
        )
    db.flush()
    written = _attach_photos(db, campaign, uploads, user, photo_dir)

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
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        for name in written:
            issue_photos.delete_photo_file(photo_dir, name)
        raced = find_idempotent_campaign(db, user, channel, idempotency_key)
        if raced is not None:
            return raced
        raise ServiceError("Could not create campaign (conflict)", 409)
    db.refresh(campaign)

    if body.send_mode == "now":
        process_campaign_slot(
            db, campaign.id, slot_key="now", settings=settings, photo_dir=photo_dir
        )
        db.refresh(campaign)
    return campaign


def scrub_recipient_mobiles_for_voters(db: Session, voter_ids: list[str]) -> int:
    """Batch-scrub stored campaign recipient mobiles when voters are hard-deleted."""
    if not voter_ids:
        return 0
    result = db.execute(
        update(CampaignRecipient)
        .where(CampaignRecipient.voter_id.in_(voter_ids))
        .values(mobile=SCRUBBED_MOBILE, updated_at=_now())
    )
    return int(result.rowcount or 0)


def scrub_recipient_mobiles_for_voter(db: Session, voter_id: str) -> int:
    return scrub_recipient_mobiles_for_voters(db, [voter_id])


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
