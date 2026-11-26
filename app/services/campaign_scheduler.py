"""Idempotent due-slot runner for daily IST campaign schedules.

Claims a (campaign_id, run_date, run_time) row before sending so concurrent
pollers cannot double-send. Uses Asia/Kolkata wall clock for date and time.

On the campaign's creation date (IST), only slots at or after the creation time
are eligible, so a schedule created at 15:30 IST with a 09:00 slot does not fire
immediately.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import database
from app.campaign_rules import IST_TZ
from app.config import Settings
from app.models import Campaign, CampaignScheduleRun
from app.services.campaigns import process_campaign_slot
from app.validation import dump_dt, parse_dt

logger = logging.getLogger("nathavaram.campaign_scheduler")
IST = ZoneInfo(IST_TZ)


def _ist_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(IST)
    if now.tzinfo is None:
        return now.replace(tzinfo=IST)
    return now.astimezone(IST)


def _parse_times(raw: str) -> list[str]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]


def _creation_hhmm_ist(campaign: Campaign) -> tuple[str, str] | None:
    """Return (YYYY-MM-DD, HH:MM) of campaign.created_at in IST, or None if unparseable."""
    if not campaign.created_at:
        return None
    try:
        created = parse_dt(campaign.created_at).astimezone(IST)
    except Exception:
        return None
    return created.strftime("%Y-%m-%d"), created.strftime("%H:%M")


def due_slots_for_campaign(campaign: Campaign, now: datetime | None = None) -> list[tuple[str, str]]:
    """Return (run_date, run_time) pairs that are due right now for this campaign."""
    moment = _ist_now(now)
    if campaign.send_mode != "daily":
        return []
    if not campaign.schedule_active or campaign.status in {"cancelled", "paused"}:
        return []
    run_date = moment.strftime("%Y-%m-%d")
    if campaign.start_date and run_date < campaign.start_date:
        return []
    if campaign.end_date and run_date > campaign.end_date:
        return []
    current_hhmm = moment.strftime("%H:%M")
    created = _creation_hhmm_ist(campaign)
    due: list[tuple[str, str]] = []
    for run_time in _parse_times(campaign.daily_times_json):
        if run_time > current_hhmm:
            continue
        # On the creation day, skip slots that were already in the past at create time.
        if created is not None:
            created_date, created_hhmm = created
            if run_date == created_date and run_time < created_hhmm:
                continue
        due.append((run_date, run_time))
    return due


def claim_schedule_run(
    db: Session, campaign_id: int, run_date: str, run_time: str
) -> CampaignScheduleRun | None:
    """Insert a claim row. Returns None when the slot was already claimed (idempotent skip)."""
    existing = db.scalar(
        select(CampaignScheduleRun).where(
            CampaignScheduleRun.campaign_id == campaign_id,
            CampaignScheduleRun.run_date == run_date,
            CampaignScheduleRun.run_time == run_time,
        )
    )
    if existing is not None:
        return None
    run = CampaignScheduleRun(
        campaign_id=campaign_id,
        run_date=run_date,
        run_time=run_time,
        claimed_at=dump_dt(datetime.now(timezone.utc)),
        recipient_count=0,
    )
    db.add(run)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return None
    return run


def process_due_schedules(
    db: Session,
    settings: Settings,
    photo_dir: Path,
    *,
    now: datetime | None = None,
) -> int:
    """Process every due daily slot once. Returns the number of slots processed."""
    campaigns = db.scalars(
        select(Campaign).where(
            Campaign.send_mode == "daily",
            Campaign.schedule_active.is_(True),
            Campaign.status.in_(["scheduled", "sending", "queued"]),
        )
    ).all()
    campaign_ids = [campaign.id for campaign in campaigns]
    processed = 0
    for campaign_id in campaign_ids:
        campaign = db.get(Campaign, campaign_id)
        if campaign is None:
            continue
        for run_date, run_time in due_slots_for_campaign(campaign, now=now):
            run = claim_schedule_run(db, campaign_id, run_date, run_time)
            if run is None:
                continue
            # Persist the claim before sending so a crash still blocks a duplicate.
            db.commit()
            run_id = run.id
            slot_key = f"{run_date}T{run_time}"
            try:
                count = process_campaign_slot(
                    db,
                    campaign_id,
                    slot_key=slot_key,
                    settings=settings,
                    photo_dir=photo_dir,
                )
                run = db.get(CampaignScheduleRun, run_id)
                if run is not None:
                    run.completed_at = dump_dt(datetime.now(timezone.utc))
                    run.recipient_count = count
                    db.commit()
                processed += 1
            except Exception:
                logger.exception(
                    "Failed schedule slot campaign=%s date=%s time=%s",
                    campaign_id,
                    run_date,
                    run_time,
                )
                db.rollback()
                run = db.get(CampaignScheduleRun, run_id)
                if run is not None and run.completed_at is None:
                    run.completed_at = dump_dt(datetime.now(timezone.utc))
                    db.commit()
    return processed


async def scheduler_loop(app, stop_event: asyncio.Event) -> None:
    """Background poller started from the FastAPI lifespan."""
    settings: Settings = app.state.settings
    interval = max(5, int(settings.campaign_scheduler_seconds))
    photo_dir = Path(settings.campaign_photo_dir)
    while not stop_event.is_set():
        try:
            if database.SessionLocal is None:
                await asyncio.sleep(interval)
                continue
            db = database.SessionLocal()
            try:
                process_due_schedules(db, settings, photo_dir)
            finally:
                db.close()
        except Exception:
            logger.exception("Campaign scheduler tick failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue
