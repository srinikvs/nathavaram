"""Pydantic bodies for campaign messaging APIs."""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.campaign_rules import (
    DATE_ISO_RE,
    MAX_DAILY_TIMES,
    MESSAGE_MAX_LENGTH,
    SEND_MODES,
    TARGET_TYPES,
    TIME_HHMM_RE,
    TITLE_MAX_LENGTH,
    TOO_MANY_TIMES_MESSAGE,
)

Channel = Literal["sms", "whatsapp"]
TargetType = Literal["ward", "booth", "panchayath", "mandal", "constituency"]
SendMode = Literal["now", "daily"]


class RecipientPreview(BaseModel):
    target_type: TargetType
    target_values: list[str] = Field(min_length=1, max_length=200)

    @field_validator("target_values")
    @classmethod
    def _clean_values(cls, values: list[str]) -> list[str]:
        cleaned = []
        seen: set[str] = set()
        for raw in values:
            text = (raw or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            cleaned.append(text)
        if not cleaned:
            raise ValueError("Select at least one target value")
        return cleaned


class CampaignSearch(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    q: str | None = Field(default=None, max_length=100)


class CampaignCreate(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX_LENGTH)
    message: str = Field(min_length=1, max_length=MESSAGE_MAX_LENGTH)
    target_type: TargetType
    target_values: list[str] = Field(min_length=1, max_length=200)
    send_mode: SendMode = "now"
    daily_times: list[str] = Field(default_factory=list)
    start_date: str | None = None
    end_date: str | None = None
    schedule_active: bool = True

    @field_validator("title", "message")
    @classmethod
    def _trim_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("This field is required")
        return text

    @field_validator("target_values")
    @classmethod
    def _clean_values(cls, values: list[str]) -> list[str]:
        return RecipientPreview._clean_values(values)

    @field_validator("daily_times")
    @classmethod
    def _clean_times(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in values:
            text = (raw or "").strip()
            if not text:
                continue
            if not re.fullmatch(TIME_HHMM_RE, text):
                raise ValueError("Daily times must be HH:MM in 24-hour format")
            if text in seen:
                continue
            seen.add(text)
            cleaned.append(text)
        if len(cleaned) > MAX_DAILY_TIMES:
            raise ValueError(TOO_MANY_TIMES_MESSAGE)
        return sorted(cleaned)

    @field_validator("start_date", "end_date")
    @classmethod
    def _clean_date(cls, value: str | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        text = str(value).strip()
        if not re.fullmatch(DATE_ISO_RE, text):
            raise ValueError("Dates must be YYYY-MM-DD")
        try:
            date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("Dates must be a real calendar day (YYYY-MM-DD)") from exc
        return text

    @model_validator(mode="after")
    def _check_schedule(self) -> CampaignCreate:
        if self.send_mode not in SEND_MODES:
            raise ValueError("send_mode must be now or daily")
        if self.target_type not in TARGET_TYPES:
            raise ValueError("Unsupported target type")
        if self.send_mode == "daily":
            if not self.daily_times:
                raise ValueError("Choose at least one daily send time")
            if self.start_date and self.end_date and self.end_date < self.start_date:
                raise ValueError("end_date must be on or after start_date")
        else:
            # Send-now ignores schedule fields.
            self.daily_times = []
            self.start_date = None
            self.end_date = None
            self.schedule_active = True
        return self
