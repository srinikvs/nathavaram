"""Message provider abstraction for SMS and WhatsApp.

Credentials come only from Settings / environment variables. When the required
credentials for a channel are missing, the dry-run provider is used: no external
HTTP call is made, and a realistic success result is returned so statuses and
KPIs still advance.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin
from uuid import uuid4

import httpx

from app.config import Settings, sms_configured, whatsapp_configured

logger = logging.getLogger("nathavaram.campaigns")

PROVIDER_DRY_RUN = "dry_run"
PROVIDER_LIVE = "live"


@dataclass(frozen=True)
class SendResult:
    ok: bool
    provider_mode: str
    provider_ref: str | None = None
    detail: str | None = None


class MessageProvider(Protocol):
    mode: str

    def send_sms(
        self,
        *,
        to_mobile: str,
        title: str,
        message: str,
        photo_urls: list[str],
    ) -> SendResult: ...

    def send_whatsapp(
        self,
        *,
        to_mobile: str,
        title: str,
        message: str,
        photo_paths: list[Path],
    ) -> SendResult: ...


def _digits_for_provider(mobile: str) -> str:
    """Strip to digits for provider APIs; keep a leading country code when present."""
    digits = "".join(ch for ch in mobile if ch.isdigit())
    if len(digits) == 10:
        return "91" + digits
    if digits.startswith("0") and len(digits) == 11:
        return "91" + digits[1:]
    return digits


class DryRunProvider:
    """Records a synthetic success without calling any external API."""

    mode = PROVIDER_DRY_RUN

    def send_sms(
        self,
        *,
        to_mobile: str,
        title: str,
        message: str,
        photo_urls: list[str],
    ) -> SendResult:
        ref = f"dry-sms-{uuid4().hex[:12]}"
        extra = f"; photos={len(photo_urls)}" if photo_urls else ""
        return SendResult(
            ok=True,
            provider_mode=PROVIDER_DRY_RUN,
            provider_ref=ref,
            detail=f"dry-run SMS to {_digits_for_provider(to_mobile)}{extra}",
        )

    def send_whatsapp(
        self,
        *,
        to_mobile: str,
        title: str,
        message: str,
        photo_paths: list[Path],
    ) -> SendResult:
        ref = f"dry-wa-{uuid4().hex[:12]}"
        extra = f"; media={len(photo_paths)}" if photo_paths else ""
        return SendResult(
            ok=True,
            provider_mode=PROVIDER_DRY_RUN,
            provider_ref=ref,
            detail=f"dry-run WhatsApp to {_digits_for_provider(to_mobile)}{extra}",
        )


class WhatsAppCloudProvider:
    """WhatsApp Business Cloud API text (+ optional image) sender."""

    mode = PROVIDER_LIVE

    def __init__(self, settings: Settings) -> None:
        self._token = (settings.whatsapp_token or "").strip()
        self._phone_id = (settings.whatsapp_phone_number_id or "").strip()
        self._version = (settings.whatsapp_api_version or "v21.0").strip()
        self._fallback = DryRunProvider()

    def send_sms(
        self,
        *,
        to_mobile: str,
        title: str,
        message: str,
        photo_urls: list[str],
    ) -> SendResult:
        return self._fallback.send_sms(
            to_mobile=to_mobile, title=title, message=message, photo_urls=photo_urls
        )

    def send_whatsapp(
        self,
        *,
        to_mobile: str,
        title: str,
        message: str,
        photo_paths: list[Path],
    ) -> SendResult:
        to = _digits_for_provider(to_mobile)
        body = f"{title}\n\n{message}" if title else message
        url = f"https://graph.facebook.com/{self._version}/{self._phone_id}/messages"
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            with httpx.Client(timeout=30.0) as client:
                if photo_paths:
                    # Upload first image as media, then send an image message with caption.
                    media_id = self._upload_media(client, headers, photo_paths[0])
                    payload = {
                        "messaging_product": "whatsapp",
                        "to": to,
                        "type": "image",
                        "image": {"id": media_id, "caption": body[:1024]},
                    }
                else:
                    payload = {
                        "messaging_product": "whatsapp",
                        "to": to,
                        "type": "text",
                        "text": {"body": body[:4096]},
                    }
                response = client.post(url, headers=headers, json=payload)
                if response.status_code >= 400:
                    return SendResult(
                        ok=False,
                        provider_mode=PROVIDER_LIVE,
                        detail=f"WhatsApp API {response.status_code}: {response.text[:300]}",
                    )
                data = response.json()
                messages = data.get("messages") or []
                ref = messages[0].get("id") if messages else None
                return SendResult(ok=True, provider_mode=PROVIDER_LIVE, provider_ref=ref)
        except Exception as exc:  # network / parse errors become rejected deliveries
            logger.warning("WhatsApp send failed: %s", exc)
            return SendResult(ok=False, provider_mode=PROVIDER_LIVE, detail=str(exc)[:300])

    def _upload_media(self, client: httpx.Client, headers: dict, path: Path) -> str:
        url = f"https://graph.facebook.com/{self._version}/{self._phone_id}/media"
        with path.open("rb") as handle:
            response = client.post(
                url,
                headers=headers,
                files={"file": (path.name, handle, "image/jpeg")},
                data={"messaging_product": "whatsapp", "type": "image/jpeg"},
            )
        response.raise_for_status()
        return str(response.json()["id"])


class SmsHttpProvider:
    """Generic DLT/HTTP SMS provider. Posts JSON; expects HTTP 2xx for success."""

    mode = PROVIDER_LIVE

    def __init__(self, settings: Settings) -> None:
        self._url = (settings.sms_api_url or "").strip()
        self._key = (settings.sms_api_key or "").strip()
        self._sender = (settings.sms_sender_id or "").strip() or None
        self._fallback = DryRunProvider()

    def send_whatsapp(
        self,
        *,
        to_mobile: str,
        title: str,
        message: str,
        photo_paths: list[Path],
    ) -> SendResult:
        return self._fallback.send_whatsapp(
            to_mobile=to_mobile, title=title, message=message, photo_paths=photo_paths
        )

    def send_sms(
        self,
        *,
        to_mobile: str,
        title: str,
        message: str,
        photo_urls: list[str],
    ) -> SendResult:
        to = _digits_for_provider(to_mobile)
        body = f"{title}\n\n{message}" if title else message
        if photo_urls:
            body = body + "\n\n" + "\n".join(photo_urls)
        payload = {
            "to": to,
            "message": body,
            "api_key": self._key,
        }
        if self._sender:
            payload["sender_id"] = self._sender
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(self._url, json=payload)
                if response.status_code >= 400:
                    return SendResult(
                        ok=False,
                        provider_mode=PROVIDER_LIVE,
                        detail=f"SMS API {response.status_code}: {response.text[:300]}",
                    )
                ref = None
                try:
                    data = response.json()
                    ref = str(data.get("id") or data.get("message_id") or "") or None
                except Exception:
                    ref = None
                return SendResult(ok=True, provider_mode=PROVIDER_LIVE, provider_ref=ref)
        except Exception as exc:
            logger.warning("SMS send failed: %s", exc)
            return SendResult(ok=False, provider_mode=PROVIDER_LIVE, detail=str(exc)[:300])


class ChannelRouter:
    """Routes SMS and WhatsApp to the live provider when configured, else dry-run."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._dry = DryRunProvider()
        self._whatsapp: MessageProvider = (
            WhatsAppCloudProvider(settings) if whatsapp_configured(settings) else self._dry
        )
        self._sms: MessageProvider = (
            SmsHttpProvider(settings) if sms_configured(settings) else self._dry
        )

    @property
    def whatsapp_mode(self) -> str:
        return self._whatsapp.mode

    @property
    def sms_mode(self) -> str:
        return self._sms.mode

    def mode_for(self, channel: str) -> str:
        return self.whatsapp_mode if channel == "whatsapp" else self.sms_mode

    def send(
        self,
        *,
        channel: str,
        to_mobile: str,
        title: str,
        message: str,
        photo_paths: list[Path],
        photo_urls: list[str],
    ) -> SendResult:
        if channel == "whatsapp":
            return self._whatsapp.send_whatsapp(
                to_mobile=to_mobile,
                title=title,
                message=message,
                photo_paths=photo_paths,
            )
        return self._sms.send_sms(
            to_mobile=to_mobile,
            title=title,
            message=message,
            photo_urls=photo_urls,
        )


def build_photo_urls(settings: Settings, photo_ids: list[int]) -> list[str]:
    """Absolute-ish links for SMS bodies. Relative paths when PUBLIC_BASE_URL is unset."""
    base = (settings.public_base_url or "").strip().rstrip("/")
    urls = []
    for photo_id in photo_ids:
        path = f"/api/campaigns/photos/{photo_id}/file"
        urls.append(urljoin(base + "/", path.lstrip("/")) if base else path)
    return urls


def get_provider(settings: Settings) -> ChannelRouter:
    return ChannelRouter(settings)
