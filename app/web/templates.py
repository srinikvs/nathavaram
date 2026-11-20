"""Jinja environment shared by the HTML routes."""

from __future__ import annotations

from datetime import timedelta, timezone

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.config import ROOT
from app.constants import GENDERS, PARTIES
from app.security import csrf_token_for
from app.validation import parse_dt
from app.web.nav import sections_for

templates = Jinja2Templates(directory=str(ROOT / "app" / "templates"))
IST = timezone(timedelta(hours=5, minutes=30))

templates.env.globals["parties"] = PARTIES
templates.env.globals["genders"] = GENDERS
templates.env.globals["nav_sections"] = sections_for


def format_ist(value: str | None) -> str:
    if not value:
        return "—"
    moment = parse_dt(value).astimezone(IST)
    return moment.strftime("%d %b %Y, %H:%M IST")


def display(value: object) -> str:
    if value is None or value == "":
        return "—"
    return str(value)


templates.env.filters["ist"] = format_ist
templates.env.filters["display"] = display


def render(request: Request, name: str, context: dict, status_code: int = 200):
    settings = request.app.state.settings
    session_token = request.cookies.get(settings.cookie_name)
    csrf_token = csrf_token_for(session_token, settings) if session_token else ""
    payload = {"request": request, "csrf_token": csrf_token, **context}
    # Starlette 0.41 names the request argument first. Fall back for older builds.
    try:
        return templates.TemplateResponse(request, name, payload, status_code=status_code)
    except TypeError:
        return templates.TemplateResponse(name, payload, status_code=status_code)
