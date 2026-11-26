"""Campaign messaging JSON API (SMS and WhatsApp).

Signed-in roles may list, preview and view. Manager/admin may create, schedule,
pause/resume, cancel and delete (creator or admin for mutations). Scheduler tick
is admin-only. Filters and target values travel in POST bodies.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.campaign_rules import CHANNELS, MAX_PHOTOS_PER_CAMPAIGN, TOO_MANY_PHOTOS_MESSAGE
from app.campaign_schemas import CampaignCreate, CampaignSearch, RecipientPreview
from app.config import sms_configured, whatsapp_configured
from app.database import get_db
from app.deps import require_admin, require_manager, require_user
from app.errors import ServiceError
from app.models import User
from app.services import issue_photos
from app.services.campaign_providers import verify_photo_token
from app.services.campaign_scheduler import process_due_schedules
from app.services.campaigns import (
    campaign_detail,
    campaign_payload,
    campaign_summary_payload,
    cancel_campaign,
    create_campaign,
    delete_campaign,
    get_photo,
    kpi_cards,
    list_target_options,
    pause_campaign,
    preview_recipients,
    resume_campaign,
    search_campaigns,
    status_counts,
)

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


def _photo_dir(request: Request) -> Path:
    return Path(request.app.state.settings.campaign_photo_dir)


def _run(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _parse_channel(channel: str) -> str:
    value = (channel or "").strip().casefold()
    if value not in CHANNELS:
        raise HTTPException(status_code=404, detail="Not found")
    return value


def _provider_mode(request: Request, channel: str) -> str:
    settings = request.app.state.settings
    if channel == "whatsapp":
        return "live" if whatsapp_configured(settings) else "dry_run"
    return "live" if sms_configured(settings) else "dry_run"


# Static paths registered before /{channel}/... so "scheduler" / "photos" are not captured.


@router.get("/photos/{photo_id}/file")
def photo_file(
    request: Request,
    photo_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    photo = _run(get_photo, db, photo_id)
    path = _run(issue_photos.photo_path, _photo_dir(request), photo.storage_name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Photo not found")
    return FileResponse(path, media_type="image/jpeg", filename=f"campaign-{photo_id}.jpg")


@router.get("/photos/{photo_id}/public")
def photo_file_public(
    request: Request,
    photo_id: int,
    exp: int,
    sig: str,
    db: Session = Depends(get_db),
):
    """Signed, expiring photo download for SMS bodies. No session required."""
    if not verify_photo_token(request.app.state.settings, photo_id, exp, sig):
        raise HTTPException(status_code=403, detail="Photo link expired or invalid")
    photo = _run(get_photo, db, photo_id)
    path = _run(issue_photos.photo_path, _photo_dir(request), photo.storage_name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Photo not found")
    return FileResponse(path, media_type="image/jpeg", filename=f"campaign-{photo_id}.jpg")


@router.post("/scheduler/run")
def run_scheduler(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> dict:
    """Manually tick the due-slot runner (admin only)."""
    count = process_due_schedules(db, request.app.state.settings, _photo_dir(request))
    return {"processed_slots": count}


@router.get("/{channel}/summary")
def summary(
    request: Request,
    channel: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    channel = _parse_channel(channel)
    return {
        "channel": channel,
        "counts": status_counts(db, channel),
        "kpis": kpi_cards(db, channel),
        "provider_mode": _provider_mode(request, channel),
    }


@router.get("/{channel}/targets/{target_type}")
def targets(
    channel: str,
    target_type: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    _parse_channel(channel)
    options = _run(list_target_options, db, target_type)
    return {"target_type": target_type, "options": options}


@router.post("/{channel}/preview")
def preview(
    channel: str,
    body: RecipientPreview,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    _parse_channel(channel)
    return _run(preview_recipients, db, body)


@router.post("/{channel}/search")
def search(
    channel: str,
    body: CampaignSearch,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    channel = _parse_channel(channel)
    rows, total, recipient_totals = _run(search_campaigns, db, channel, body)
    items = [
        campaign_payload(row, recipient_total=recipient_totals.get(row.id, 0)) for row in rows
    ]
    return {
        "items": items,
        "total": total,
        "page": body.page,
        "page_size": body.page_size,
        "counts": status_counts(db, channel),
        "kpis": kpi_cards(db, channel),
    }


def _bool_form(value: str | None, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _list_form(raw: str | None) -> list[str]:
    if raw is None or not str(raw).strip():
        return []
    text = str(raw).strip()
    if text.startswith("["):
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return [str(item) for item in data]
        except json.JSONDecodeError:
            pass
    return [part.strip() for part in text.split(",") if part.strip()]


@router.post("/{channel}", status_code=201)
async def create(
    request: Request,
    channel: str,
    title: str = Form(...),
    message: str = Form(...),
    target_type: str = Form(...),
    target_values: str = Form(...),
    send_mode: str = Form("now"),
    daily_times: str = Form(""),
    start_date: str | None = Form(None),
    end_date: str | None = Form(None),
    schedule_active: str | None = Form("true"),
    photos: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(require_manager),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    channel = _parse_channel(channel)
    uploads: list[bytes] = []
    files = photos or []
    if len(files) > MAX_PHOTOS_PER_CAMPAIGN:
        raise HTTPException(status_code=422, detail=TOO_MANY_PHOTOS_MESSAGE)
    for upload in files:
        data = await upload.read(issue_photos.MAX_UPLOAD_BYTES + 1)
        if data:
            uploads.append(data)
    if len(uploads) > MAX_PHOTOS_PER_CAMPAIGN:
        raise HTTPException(status_code=422, detail=TOO_MANY_PHOTOS_MESSAGE)
    try:
        body = CampaignCreate(
            title=title,
            message=message,
            target_type=target_type,  # type: ignore[arg-type]
            target_values=_list_form(target_values),
            send_mode=send_mode,  # type: ignore[arg-type]
            daily_times=_list_form(daily_times),
            start_date=start_date,
            end_date=end_date,
            schedule_active=_bool_form(schedule_active, True),
        )
    except ValidationError as exc:
        message_text = exc.errors()[0]["msg"]
        prefix = "Value error, "
        if message_text.startswith(prefix):
            message_text = message_text[len(prefix) :]
        raise HTTPException(status_code=422, detail=message_text) from exc

    campaign = _run(
        create_campaign,
        db,
        channel,
        body,
        user,
        request.app.state.settings,
        _photo_dir(request),
        uploads,
        idempotency_key=idempotency_key,
    )
    return campaign_summary_payload(db, campaign)


@router.get("/{channel}/{campaign_id}")
def detail(
    channel: str,
    campaign_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    recipients_page: int = 1,
    recipients_page_size: int = 50,
) -> dict:
    channel = _parse_channel(channel)
    return _run(
        campaign_detail,
        db,
        campaign_id,
        channel,
        recipients_page=recipients_page,
        recipients_page_size=recipients_page_size,
    )


@router.post("/{channel}/{campaign_id}/pause")
def pause(
    channel: str,
    campaign_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_manager),
) -> dict:
    channel = _parse_channel(channel)
    campaign = _run(pause_campaign, db, campaign_id, channel, user)
    return campaign_payload(campaign)


@router.post("/{channel}/{campaign_id}/resume")
def resume(
    channel: str,
    campaign_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_manager),
) -> dict:
    channel = _parse_channel(channel)
    campaign = _run(resume_campaign, db, campaign_id, channel, user)
    return campaign_payload(campaign)


@router.post("/{channel}/{campaign_id}/cancel")
def cancel(
    channel: str,
    campaign_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_manager),
) -> dict:
    channel = _parse_channel(channel)
    campaign = _run(cancel_campaign, db, campaign_id, channel, user)
    return campaign_payload(campaign)


@router.delete("/{channel}/{campaign_id}")
def remove(
    request: Request,
    channel: str,
    campaign_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_manager),
) -> dict:
    channel = _parse_channel(channel)
    _run(delete_campaign, db, campaign_id, channel, _photo_dir(request), user)
    return {"deleted": campaign_id}
