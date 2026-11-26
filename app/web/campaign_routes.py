"""Campaign messaging web pages for SMS and WhatsApp."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
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
from app.permissions import can_manage_campaigns
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
        rows, total, recipient_totals = search_campaigns(db, channel, query)
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
                "can_manage": can_manage_campaigns(user),
                "error": exc.message,
                "notice": "",
            },
            status_code=exc.status_code,
        )

    items = [
        campaign_payload(row, recipient_total=recipient_totals.get(row.id, 0)) for row in rows
    ]
    page_count = max(1, (total + query.page_size - 1) // query.page_size)
    notice = DONE_NOTICES.get(request.query_params.get("done", ""), "")
    query_error = request.query_params.get("error") or error
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
            "can_manage": can_manage_campaigns(user),
            "error": query_error,
            "notice": notice if not query_error else "",
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
            "can_manage": can_manage_campaigns(user),
        },
    )


def _action_redirect(channel: str, *, done: str | None = None, error: str | None = None) -> RedirectResponse:
    if error:
        return RedirectResponse(
            f"/campaign/{channel}?error={quote(error)}", status_code=303
        )
    return RedirectResponse(f"/campaign/{channel}?done={done}", status_code=303)


def _run_action(channel: str, call, *args):
    channel = channel.casefold()
    if channel not in CHANNELS:
        return _action_redirect("whatsapp", error="Unknown campaign channel")
    try:
        call(*args)
    except ServiceError as exc:
        return _action_redirect(channel, error=exc.message)
    return None


@router.post("/campaign/{channel}/{campaign_id}/pause")
def pause_action(
    channel: str,
    campaign_id: int,
    db: Session = Depends(get_db),
    user=Depends(web_user),
):
    failed = _run_action(channel, pause_campaign, db, campaign_id, channel.casefold(), user)
    return failed or _action_redirect(channel.casefold(), done="paused")


@router.post("/campaign/{channel}/{campaign_id}/resume")
def resume_action(
    channel: str,
    campaign_id: int,
    db: Session = Depends(get_db),
    user=Depends(web_user),
):
    failed = _run_action(channel, resume_campaign, db, campaign_id, channel.casefold(), user)
    return failed or _action_redirect(channel.casefold(), done="resumed")


@router.post("/campaign/{channel}/{campaign_id}/cancel")
def cancel_action(
    channel: str,
    campaign_id: int,
    db: Session = Depends(get_db),
    user=Depends(web_user),
):
    failed = _run_action(channel, cancel_campaign, db, campaign_id, channel.casefold(), user)
    return failed or _action_redirect(channel.casefold(), done="cancelled")


@router.post("/campaign/{channel}/{campaign_id}/delete")
def delete_action(
    request: Request,
    channel: str,
    campaign_id: int,
    db: Session = Depends(get_db),
    user=Depends(web_user),
):
    failed = _run_action(
        channel,
        delete_campaign,
        db,
        campaign_id,
        channel.casefold(),
        _photo_dir(request),
        user,
    )
    return failed or _action_redirect(channel.casefold(), done="deleted")
