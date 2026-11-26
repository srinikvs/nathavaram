"""Campaign messaging web pages for SMS and WhatsApp."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.campaign_rules import (
    CHANNELS,
    CHANNEL_LABELS,
    MAX_DAILY_TIMES,
    MAX_PHOTOS_PER_CAMPAIGN,
    TARGET_LABELS,
    TARGET_TYPES,
)
from app.campaign_schemas import CampaignSearch
from app.config import sms_configured, whatsapp_configured
from app.database import get_db
from app.errors import ServiceError
from app.models import CampaignRecipient
from app.services.campaigns import (
    campaign_detail,
    campaign_payload,
    cancel_campaign,
    delete_campaign,
    kpi_cards,
    pause_campaign,
    resume_campaign,
    search_campaigns,
)
from app.web.routes import web_user
from app.web.templates import render

router = APIRouter()

DONE_NOTICES = {
    "paused": "Schedule paused.",
    "resumed": "Schedule resumed.",
    "cancelled": "Campaign cancelled.",
    "deleted": "Campaign deleted.",
}


def _photo_dir(request: Request) -> Path:
    return Path(request.app.state.settings.campaign_photo_dir)


def _provider_mode(request: Request, channel: str) -> str:
    settings = request.app.state.settings
    if channel == "whatsapp":
        return "live" if whatsapp_configured(settings) else "dry_run"
    return "live" if sms_configured(settings) else "dry_run"


def _list_page(
    request: Request,
    db: Session,
    user,
    channel: str,
    page: int = 1,
    error: str | None = None,
    status_code: int = 200,
):
    query = CampaignSearch(page=page, page_size=20)
    try:
        rows, total = search_campaigns(db, channel, query)
    except ServiceError as exc:
        return render(
            request,
            "campaigns/list.html",
            {
                "user": user,
                "channel": channel,
                "channel_label": CHANNEL_LABELS[channel],
                "items": [],
                "total": 0,
                "page": 1,
                "page_size": 20,
                "page_count": 1,
                "kpis": kpi_cards(db, channel) if channel in CHANNELS else [],
                "provider_mode": _provider_mode(request, channel),
                "target_types": TARGET_TYPES,
                "target_labels": TARGET_LABELS,
                "max_photos": MAX_PHOTOS_PER_CAMPAIGN,
                "max_daily_times": MAX_DAILY_TIMES,
                "error": exc.message,
                "notice": "",
            },
            status_code=exc.status_code,
        )

    items = []
    for row in rows:
        recipient_total = db.scalar(
            select(func.count())
            .select_from(CampaignRecipient)
            .where(CampaignRecipient.campaign_id == row.id)
        ) or 0
        items.append(campaign_payload(row, recipient_total=int(recipient_total)))

    page_count = max(1, (total + query.page_size - 1) // query.page_size)
    notice = DONE_NOTICES.get(request.query_params.get("done", ""), "")
    return render(
        request,
        "campaigns/list.html",
        {
            "user": user,
            "channel": channel,
            "channel_label": CHANNEL_LABELS[channel],
            "items": items,
            "total": total,
            "page": query.page,
            "page_size": query.page_size,
            "page_count": page_count,
            "kpis": kpi_cards(db, channel),
            "provider_mode": _provider_mode(request, channel),
            "target_types": TARGET_TYPES,
            "target_labels": TARGET_LABELS,
            "max_photos": MAX_PHOTOS_PER_CAMPAIGN,
            "max_daily_times": MAX_DAILY_TIMES,
            "error": error,
            "notice": notice,
        },
        status_code=status_code,
    )


@router.get("/campaign/sms")
def sms_page(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(web_user),
    page: int = 1,
):
    return _list_page(request, db, user, "sms", page=max(1, page))


@router.get("/campaign/whatsapp")
def whatsapp_page(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(web_user),
    page: int = 1,
):
    return _list_page(request, db, user, "whatsapp", page=max(1, page))


@router.get("/campaign/{channel}/{campaign_id}")
def detail_page(
    request: Request,
    channel: str,
    campaign_id: int,
    db: Session = Depends(get_db),
    user=Depends(web_user),
):
    channel = (channel or "").strip().casefold()
    if channel not in CHANNELS:
        return render(request, "404.html", {"user": user}, status_code=404)
    try:
        detail = campaign_detail(db, campaign_id, channel)
    except ServiceError as exc:
        if exc.status_code == 404:
            return render(request, "404.html", {"user": user}, status_code=404)
        return _list_page(request, db, user, channel, error=exc.message, status_code=exc.status_code)
    return render(
        request,
        "campaigns/detail.html",
        {
            "user": user,
            "channel": channel,
            "channel_label": CHANNEL_LABELS[channel],
            "campaign": detail,
            "provider_mode": _provider_mode(request, channel),
        },
    )


def _action_redirect(channel: str, done: str) -> RedirectResponse:
    return RedirectResponse(f"/campaign/{channel}?done={done}", status_code=303)


@router.post("/campaign/{channel}/{campaign_id}/pause")
def pause_action(
    channel: str,
    campaign_id: int,
    db: Session = Depends(get_db),
    user=Depends(web_user),
):
    channel = channel.casefold()
    try:
        pause_campaign(db, campaign_id, channel, user)
    except ServiceError:
        pass
    return _action_redirect(channel, "paused")


@router.post("/campaign/{channel}/{campaign_id}/resume")
def resume_action(
    channel: str,
    campaign_id: int,
    db: Session = Depends(get_db),
    user=Depends(web_user),
):
    channel = channel.casefold()
    try:
        resume_campaign(db, campaign_id, channel, user)
    except ServiceError:
        pass
    return _action_redirect(channel, "resumed")


@router.post("/campaign/{channel}/{campaign_id}/cancel")
def cancel_action(
    channel: str,
    campaign_id: int,
    db: Session = Depends(get_db),
    user=Depends(web_user),
):
    channel = channel.casefold()
    try:
        cancel_campaign(db, campaign_id, channel, user)
    except ServiceError:
        pass
    return _action_redirect(channel, "cancelled")


@router.post("/campaign/{channel}/{campaign_id}/delete")
def delete_action(
    request: Request,
    channel: str,
    campaign_id: int,
    db: Session = Depends(get_db),
    user=Depends(web_user),
):
    channel = channel.casefold()
    try:
        delete_campaign(db, campaign_id, channel, _photo_dir(request))
    except ServiceError:
        pass
    return _action_redirect(channel, "deleted")
