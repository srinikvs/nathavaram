"""Campaign messaging (SMS / WhatsApp): targeting, photos, schedules, dry-run, masking, auth.

Synthetic voters and generated JPEGs only. Provider credentials stay unset unless a test
monkeypatches them; sends default to dry-run.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from PIL import Image
from sqlalchemy import select

from app.campaign_rules import MAX_PHOTOS_PER_CAMPAIGN
from app.models import Campaign, CampaignDelivery, CampaignRecipient, CampaignScheduleRun
from app.campaign_rules import MISSING_MOBILE_MESSAGE, VOTER_REMOVED_MESSAGE
from app.services.campaign_providers import (
    SmsHttpProvider,
    WhatsAppCloudProvider,
    build_photo_urls,
    scrub_phone_text,
    sign_photo_token,
    verify_photo_token,
)
from app.services.campaign_scheduler import due_slots_for_campaign, process_due_schedules
from app.services.campaigns import SCRUBBED_MOBILE, mask_campaign_mobile
from app.validation import dump_dt
from tests.conftest import make_user, voter_payload

IST = ZoneInfo("Asia/Kolkata")
FULL_MOBILES = ("9000000101", "9000000102", "9000000103")


@pytest.fixture
def campaigns(ctx, tmp_path, monkeypatch):
    photo_dir = tmp_path / "campaign-photos-test"
    photo_dir.mkdir(parents=True, exist_ok=True)
    ctx.client.app.state.settings.campaign_photo_dir = str(photo_dir)
    monkeypatch.setattr(ctx.client.app.state.settings, "campaign_scheduler_seconds", 3600)
    clerk = make_user(ctx, "camp.clerk", "clerk-pass-123", role="user")
    manager = make_user(ctx, "camp.mgr", "mgr-pass-1234", role="manager")
    return SimpleNamespace(
        ctx=ctx,
        client=ctx.client,
        photo_dir=photo_dir,
        admin=ctx.auth,
        clerk=clerk,
        manager=manager,
        settings=ctx.client.app.state.settings,
    )


def _jpeg_bytes(color=(40, 120, 200)) -> bytes:
    image = Image.new("RGB", (32, 32), color)
    buf = BytesIO()
    image.save(buf, format="JPEG")
    return buf.getvalue()


def _assert_no_full_mobiles(payload) -> None:
    text = json.dumps(payload)
    for mobile in FULL_MOBILES:
        assert mobile not in text
    assert not re.search(r"90000001\d{2}", text)


def _seed_voters(env):
    client = env.client
    auth = env.admin
    rows = [
        voter_payload(
            voter_id="ZZZ4000001",
            mobile="9000000101",
            ward="1",
            booth="10",
            panchayath="Nathavaram",
            mandal="Nathavaram",
            constituency="Narsipatnam",
        ),
        voter_payload(
            voter_id="ZZZ4000002",
            mobile="9000000102",
            ward="1",
            booth="10",
            panchayath="Nathavaram",
            mandal="Nathavaram",
            constituency="Narsipatnam",
        ),
        voter_payload(
            voter_id="ZZZ4000003",
            mobile="9000000103",
            ward="2",
            booth="11",
            panchayath="Otherpanch",
            mandal="Othermandal",
            constituency="Otherconst",
        ),
    ]
    for row in rows:
        response = client.post("/api/voters", headers=auth, json=row)
        assert response.status_code == 201, response.text

    from app import database
    from app.models import Voter

    now = dump_dt(datetime.now(timezone.utc))
    db = database.SessionLocal()
    try:
        for voter_id, mobile, ward in (
            ("ZZZ4000004", "12345", "1"),
            ("ZZZ4000005", None, "1"),
        ):
            if db.get(Voter, voter_id) is None:
                db.add(
                    Voter(
                        voter_id=voter_id,
                        first_name="Excluded",
                        booth="10",
                        ward=ward,
                        mobile=mobile,
                        panchayath="Nathavaram",
                        mandal="Nathavaram",
                        constituency="Narsipatnam",
                        is_local=True,
                        created_at=now,
                        updated_at=now,
                        revision=1,
                    )
                )
        db.commit()
    finally:
        db.close()


def _create_campaign(env, channel="whatsapp", headers=None, photos=None, **fields):
    headers = dict(headers or env.admin)
    data = {
        "title": fields.pop("title", "ప్రచారం Hello 🙏"),
        "message": fields.pop("message", "Meeting at 6pm ★"),
        "target_type": fields.pop("target_type", "ward"),
        "target_values": fields.pop("target_values", '["1"]'),
        "send_mode": fields.pop("send_mode", "now"),
        "daily_times": fields.pop("daily_times", "[]"),
        "schedule_active": fields.pop("schedule_active", "true"),
    }
    for key in ("start_date", "end_date"):
        if key in fields and fields[key] is not None:
            data[key] = fields.pop(key)
    data.update({k: v for k, v in fields.items() if v is not None and k != "idempotency_key"})
    idem = fields.pop("idempotency_key", None) if "idempotency_key" in fields else None
    # idempotency may already have been popped via kwargs above
    if "Idempotency-Key" not in headers and idem:
        headers["Idempotency-Key"] = idem
    files = None
    if photos:
        files = [("photos", (f"p{i}.jpg", blob, "image/jpeg")) for i, blob in enumerate(photos)]
    return env.client.post(
        f"/api/campaigns/{channel}",
        headers=headers,
        data=data,
        files=files,
    )


def _detail(env, channel, campaign_id, headers=None):
    return env.client.get(
        f"/api/campaigns/{channel}/{campaign_id}",
        headers=headers or env.admin,
    )


def _web_login(env, username="admin", password="admin-pass-123"):
    env.client.cookies.clear()
    response = env.client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


# --- masking -----------------------------------------------------------------


def test_mask_campaign_mobile_keeps_only_last_four_characters():
    assert mask_campaign_mobile("9000000101") == "******0101"
    assert mask_campaign_mobile("+91 90000 00102") == "***********0102"
    assert mask_campaign_mobile("12") == "**"
    assert mask_campaign_mobile("") == ""
    assert mask_campaign_mobile(None) is None


def test_scrub_phone_text_masks_embedded_numbers():
    assert "9000000101" not in (scrub_phone_text("fail for 9000000101 please") or "")
    assert scrub_phone_text(None) is None


def test_create_and_detail_never_leak_full_mobile(campaigns):
    _seed_voters(campaigns)
    created = _create_campaign(campaigns, channel="whatsapp")
    assert created.status_code == 201, created.text
    _assert_no_full_mobiles(created.json())
    detail = _detail(campaigns, "whatsapp", created.json()["id"])
    assert detail.status_code == 200
    body = detail.json()
    _assert_no_full_mobiles(body)
    assert all(row["mobile"].startswith("*") for row in body["recipients"])
    assert all(
        row.get("detail") is None or "900000" not in (row.get("detail") or "")
        for row in body["deliveries"]
    )


# --- auth / pages ------------------------------------------------------------


def test_campaign_pages_require_login(ctx):
    ctx.client.cookies.clear()
    for path in ("/campaign/sms", "/campaign/whatsapp"):
        response = ctx.client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/login")


def test_campaign_api_requires_auth(ctx):
    ctx.client.cookies.clear()
    assert ctx.client.get("/api/campaigns/whatsapp/summary").status_code == 401


def test_clerk_cannot_create_or_mutate_campaigns(campaigns):
    _seed_voters(campaigns)
    denied = _create_campaign(campaigns, headers=campaigns.clerk)
    assert denied.status_code == 403

    created = _create_campaign(campaigns, send_mode="daily", daily_times='["08:00"]')
    assert created.status_code == 201
    campaign_id = created.json()["id"]
    assert (
        campaigns.client.post(
            f"/api/campaigns/whatsapp/{campaign_id}/pause", headers=campaigns.clerk
        ).status_code
        == 403
    )
    assert (
        campaigns.client.post(
            "/api/campaigns/scheduler/run", headers=campaigns.clerk
        ).status_code
        == 403
    )
    assert (
        campaigns.client.post(
            "/api/campaigns/scheduler/run", headers=campaigns.manager
        ).status_code
        == 403
    )


def test_manager_can_create_and_admin_runs_scheduler(campaigns):
    _seed_voters(campaigns)
    created = _create_campaign(campaigns, headers=campaigns.manager)
    assert created.status_code == 201, created.text
    tick = campaigns.client.post("/api/campaigns/scheduler/run", headers=campaigns.admin)
    assert tick.status_code == 200


def test_sms_and_whatsapp_pages_render_for_signed_in_user(campaigns):
    _seed_voters(campaigns)
    _web_login(campaigns)
    for path, label in (("/campaign/sms", "SMS"), ("/campaign/whatsapp", "WhatsApp")):
        page = campaigns.client.get(path)
        assert page.status_code == 200, page.text
        assert label in page.text
        assert "Quick Message" in page.text
        assert "INITIATED" in page.text
        assert "Dry-run mode" in page.text


def test_sidebar_links_sms_and_whatsapp_without_coming_soon(campaigns):
    _web_login(campaigns)
    html = campaigns.client.get("/voters").text
    assert 'href="/campaign/sms"' in html
    assert 'href="/campaign/whatsapp"' in html
    assert "Coming soon" in html


def test_cross_channel_detail_is_404(campaigns):
    _seed_voters(campaigns)
    created = _create_campaign(campaigns, channel="sms").json()
    assert (
        campaigns.client.get(
            f"/api/campaigns/whatsapp/{created['id']}", headers=campaigns.admin
        ).status_code
        == 404
    )


# --- recipient resolution ----------------------------------------------------


def test_preview_resolves_ward_and_booth_and_skips_invalid_mobiles(campaigns):
    _seed_voters(campaigns)
    ward = campaigns.client.post(
        "/api/campaigns/whatsapp/preview",
        headers=campaigns.admin,
        json={"target_type": "ward", "target_values": ["1"]},
    )
    assert ward.status_code == 200, ward.text
    body = ward.json()
    assert body["matched_voters"] == 4  # includes invalid/missing mobile rows
    assert body["with_mobile"] == 2

    booth = campaigns.client.post(
        "/api/campaigns/sms/preview",
        headers=campaigns.clerk,
        json={"target_type": "booth", "target_values": ["10"]},
    )
    assert booth.status_code == 200
    assert booth.json()["with_mobile"] == 2


# --- send now dry-run + KPIs + masking ---------------------------------------


def test_send_now_whatsapp_dry_run_updates_kpis_and_masks_mobiles(campaigns):
    _seed_voters(campaigns)
    created = _create_campaign(campaigns, channel="whatsapp")
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "completed"
    assert body["provider_mode"] == "dry_run"
    assert body["recipient_total"] == 2
    assert "recipients" not in body  # create returns summary only
    _assert_no_full_mobiles(body)

    detail = _detail(campaigns, "whatsapp", body["id"]).json()
    assert all(row["status"] == "sent" for row in detail["recipients"])
    _assert_no_full_mobiles(detail)

    summary = campaigns.client.get("/api/campaigns/whatsapp/summary", headers=campaigns.admin)
    counts = summary.json()["counts"]
    assert counts["sent"] == 2
    assert counts["initiated"] == 0

    from app import database

    db = database.SessionLocal()
    try:
        mobiles = db.scalars(select(CampaignRecipient.mobile)).all()
        assert mobiles
        assert all(mobile.startswith("*") for mobile in mobiles)
        assert "9000000101" not in mobiles
        assert "9000000102" not in mobiles
    finally:
        db.close()


def test_send_now_sms_omits_photo_links_without_public_base_url(campaigns):
    _seed_voters(campaigns)
    created = _create_campaign(
        campaigns, channel="sms", photos=[_jpeg_bytes(), _jpeg_bytes((10, 200, 10))]
    )
    assert created.status_code == 201, created.text
    assert created.json()["photo_count"] == 2
    assert build_photo_urls(campaigns.settings, [1, 2]) == []


def test_signed_photo_urls_when_public_base_configured(campaigns, monkeypatch):
    monkeypatch.setattr(campaigns.settings, "public_base_url", "https://example.test")
    urls = build_photo_urls(campaigns.settings, [7])
    assert len(urls) == 1
    assert urls[0].startswith("https://example.test/api/campaigns/photos/7/public?")
    assert "/file" not in urls[0]


# --- photo limits ------------------------------------------------------------


def test_photo_upload_rejects_more_than_five_with_422(campaigns):
    _seed_voters(campaigns)
    photos = [_jpeg_bytes((i * 20, 50, 80)) for i in range(MAX_PHOTOS_PER_CAMPAIGN + 1)]
    response = _create_campaign(campaigns, photos=photos)
    assert response.status_code == 422, response.text
    assert "5" in response.json()["detail"]


def test_photo_upload_accepts_five_photos(campaigns):
    _seed_voters(campaigns)
    photos = [_jpeg_bytes((i * 30, 40, 90)) for i in range(MAX_PHOTOS_PER_CAMPAIGN)]
    response = _create_campaign(campaigns, photos=photos)
    assert response.status_code == 201, response.text
    assert response.json()["photo_count"] == 5


# --- daily schedule, pause/resume, idempotency -------------------------------


def test_daily_schedule_up_to_three_times_and_rejects_four(campaigns):
    _seed_voters(campaigns)
    ok = _create_campaign(
        campaigns,
        send_mode="daily",
        daily_times='["09:00", "14:30", "18:15"]',
        start_date="2026-01-01",
        end_date="2026-12-31",
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["status"] == "scheduled"
    assert ok.json()["daily_times"] == ["09:00", "14:30", "18:15"]

    bad = _create_campaign(
        campaigns,
        send_mode="daily",
        daily_times='["09:00", "10:00", "11:00", "12:00"]',
    )
    assert bad.status_code == 422


def test_pause_resume_and_cancel_daily_campaign(campaigns):
    _seed_voters(campaigns)
    created = _create_campaign(
        campaigns,
        send_mode="daily",
        daily_times='["08:00"]',
        start_date="2026-01-01",
        end_date="2026-12-31",
    )
    campaign_id = created.json()["id"]

    paused = campaigns.client.post(
        f"/api/campaigns/whatsapp/{campaign_id}/pause", headers=campaigns.admin
    )
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    resumed = campaigns.client.post(
        f"/api/campaigns/whatsapp/{campaign_id}/resume", headers=campaigns.admin
    )
    assert resumed.status_code == 200
    assert resumed.json()["schedule_active"] is True

    cancelled = campaigns.client.post(
        f"/api/campaigns/whatsapp/{campaign_id}/cancel", headers=campaigns.admin
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_scheduler_is_idempotent_for_due_ist_slot(campaigns):
    _seed_voters(campaigns)
    created = _create_campaign(
        campaigns,
        send_mode="daily",
        daily_times='["09:00", "15:00"]',
        start_date="2026-11-26",
        end_date="2026-11-26",
    )
    assert created.status_code == 201, created.text
    campaign_id = created.json()["id"]

    from app import database

    db = database.SessionLocal()
    try:
        # Pretend the campaign was created early enough that both slots are eligible.
        campaign = db.get(Campaign, campaign_id)
        campaign.created_at = dump_dt(datetime(2026, 11, 26, 2, 0, tzinfo=timezone.utc))
        db.commit()

        now = datetime(2026, 11, 26, 15, 30, tzinfo=IST)
        campaign = db.get(Campaign, campaign_id)
        assert due_slots_for_campaign(campaign, now=now) == [
            ("2026-11-26", "09:00"),
            ("2026-11-26", "15:00"),
        ]
        first = process_due_schedules(
            db, campaigns.settings, Path(campaigns.photo_dir), now=now
        )
        assert first == 2
        second = process_due_schedules(
            db, campaigns.settings, Path(campaigns.photo_dir), now=now
        )
        assert second == 0
        deliveries = db.scalars(
            select(CampaignDelivery).where(CampaignDelivery.campaign_id == campaign_id)
        ).all()
        assert len(deliveries) == 4
        assert all(row.status == "sent" for row in deliveries)
        assert all(row.provider_mode == "dry_run" for row in deliveries)
        _assert_no_full_mobiles([delivery_payload_safe(row) for row in deliveries])
    finally:
        db.close()


def delivery_payload_safe(row):
    return {"detail": row.detail, "provider_ref": row.provider_ref, "status": row.status}


def test_scheduler_skips_past_slots_on_creation_day(campaigns):
    _seed_voters(campaigns)
    created = _create_campaign(
        campaigns,
        send_mode="daily",
        daily_times='["09:00", "16:00"]',
        start_date="2026-11-26",
        end_date="2026-11-26",
    )
    campaign_id = created.json()["id"]
    from app import database

    db = database.SessionLocal()
    try:
        campaign = db.get(Campaign, campaign_id)
        # Created at 15:30 IST on the same day: 09:00 must not fire; 16:00 may when due.
        campaign.created_at = dump_dt(datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc))  # 15:30 IST
        db.commit()
        campaign = db.get(Campaign, campaign_id)
        at_1531 = datetime(2026, 11, 26, 15, 31, tzinfo=IST)
        assert due_slots_for_campaign(campaign, now=at_1531) == []
        at_1601 = datetime(2026, 11, 26, 16, 1, tzinfo=IST)
        assert due_slots_for_campaign(campaign, now=at_1601) == [("2026-11-26", "16:00")]
    finally:
        db.close()


def test_paused_schedule_is_not_processed(campaigns):
    _seed_voters(campaigns)
    created = _create_campaign(
        campaigns,
        send_mode="daily",
        daily_times='["10:00"]',
        start_date="2026-11-26",
        end_date="2026-11-26",
        schedule_active="false",
    )
    assert created.json()["status"] == "paused"
    now = datetime(2026, 11, 26, 10, 5, tzinfo=IST)
    from app import database

    db = database.SessionLocal()
    try:
        assert (
            process_due_schedules(db, campaigns.settings, Path(campaigns.photo_dir), now=now)
            == 0
        )
    finally:
        db.close()


def test_create_idempotency_key_returns_same_campaign(campaigns):
    _seed_voters(campaigns)
    headers = dict(campaigns.admin)
    headers["Idempotency-Key"] = "campaign-retry-1"
    first = _create_campaign(campaigns, headers=headers)
    second = _create_campaign(campaigns, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_mid_loop_provider_exception_keeps_prior_deliveries(campaigns):
    _seed_voters(campaigns)
    created = _create_campaign(campaigns, channel="sms", send_mode="now")
    # Force a second send-now-like slot with a provider that fails mid-way.
    campaign_id = created.json()["id"]
    from app import database
    from app.services import campaign_send

    class BoomRouter:
        mode = "dry_run"

        def mode_for(self, channel):
            return "dry_run"

        def send(self, **kwargs):
            if not getattr(self, "_once", False):
                self._once = True
                return SimpleNamespace(
                    ok=True, provider_mode="dry_run", provider_ref="ok-1", detail="ok"
                )
            raise RuntimeError("provider crashed for 9000000102")

    db = database.SessionLocal()
    try:
        with patch.object(campaign_send, "get_provider", return_value=BoomRouter()):
            sent = campaign_send.process_campaign_slot(
                db,
                campaign_id,
                slot_key="retry-slot",
                settings=campaigns.settings,
                photo_dir=Path(campaigns.photo_dir),
            )
        assert sent == 1
        campaign = db.get(Campaign, campaign_id)
        assert campaign.status == "completed"
        deliveries = db.scalars(
            select(CampaignDelivery).where(
                CampaignDelivery.campaign_id == campaign_id,
                CampaignDelivery.slot_key == "retry-slot",
            )
        ).all()
        assert len(deliveries) == 2
        assert any(row.status == "sent" for row in deliveries)
        assert any(row.status == "rejected" for row in deliveries)
        for row in deliveries:
            assert row.detail is None or "9000000102" not in (row.detail or "")
    finally:
        db.close()


def test_live_whatsapp_failure_scrubs_mobile_from_detail(campaigns, monkeypatch):
    _seed_voters(campaigns)
    monkeypatch.setattr(campaigns.settings, "whatsapp_token", "token-value")
    monkeypatch.setattr(campaigns.settings, "whatsapp_phone_number_id", "12345")
    provider = WhatsAppCloudProvider(campaigns.settings)

    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = "bad number 9000000101"

    with patch("app.services.campaign_providers.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.post.return_value = mock_response
        result = provider.send_whatsapp(
            to_mobile="9000000101",
            title="T",
            message="M",
            photo_paths=[],
        )
    assert result.ok is False
    assert "9000000101" not in (result.detail or "")


def test_live_sms_sends_api_key_in_header(campaigns, monkeypatch):
    monkeypatch.setattr(campaigns.settings, "sms_api_url", "https://sms.example/send")
    monkeypatch.setattr(campaigns.settings, "sms_api_key", "secret-key")
    provider = SmsHttpProvider(campaigns.settings)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "m1"}

    with patch("app.services.campaign_providers.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.post.return_value = mock_response
        result = provider.send_sms(
            to_mobile="9000000101", title="T", message="M", photo_urls=[]
        )
        assert result.ok is True
        kwargs = client.post.call_args.kwargs
        assert "api_key" not in kwargs["json"]
        assert kwargs["headers"]["Authorization"] == "Bearer secret-key"
        assert "X-API-Key" not in kwargs["headers"]


def test_web_detail_shows_masked_mobile_only(campaigns):
    _seed_voters(campaigns)
    created = _create_campaign(campaigns, channel="sms").json()
    _web_login(campaigns)
    page = campaigns.client.get(f"/campaign/sms/{created['id']}")
    assert page.status_code == 200
    assert "******0101" in page.text or "******0102" in page.text
    assert "9000000101" not in page.text


def test_web_action_failure_shows_error_not_success(campaigns):
    _seed_voters(campaigns)
    created = _create_campaign(campaigns, channel="whatsapp", send_mode="now").json()
    _web_login(campaigns)
    page = campaigns.client.get("/campaign/whatsapp")
    token = re.search(r'name="csrf_token" value="([0-9a-f]+)"', page.text).group(1)
    # Pausing a send-now campaign should fail.
    response = campaigns.client.post(
        f"/campaign/whatsapp/{created['id']}/pause",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert "done=paused" not in response.headers["location"]
    landed = campaigns.client.get(response.headers["location"])
    assert "Only daily schedules can be paused" in landed.text
    assert "Schedule paused." not in landed.text


def test_web_action_requires_csrf(campaigns):
    _seed_voters(campaigns)
    created = _create_campaign(
        campaigns, send_mode="daily", daily_times='["08:00"]'
    ).json()
    _web_login(campaigns)
    response = campaigns.client.post(
        f"/campaign/whatsapp/{created['id']}/pause",
        data={},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_delete_campaign_removes_rows(campaigns):
    _seed_voters(campaigns)
    created = _create_campaign(campaigns).json()
    campaign_id = created["id"]
    deleted = campaigns.client.delete(
        f"/api/campaigns/whatsapp/{campaign_id}", headers=campaigns.admin
    )
    assert deleted.status_code == 200
    assert (
        campaigns.client.get(
            f"/api/campaigns/whatsapp/{campaign_id}", headers=campaigns.admin
        ).status_code
        == 404
    )


def test_search_lists_campaigns_with_pagination_fields(campaigns):
    _seed_voters(campaigns)
    _create_campaign(campaigns, title="One")
    _create_campaign(campaigns, title="Two", target_values='["2"]')
    listed = campaigns.client.post(
        "/api/campaigns/whatsapp/search",
        headers=campaigns.admin,
        json={"page": 1, "page_size": 10},
    )
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    assert "kpis" in body


def test_hard_delete_scrubs_campaign_recipient_mobile(campaigns):
    _seed_voters(campaigns)
    created = _create_campaign(campaigns, send_mode="daily", daily_times='["20:00"]').json()
    voter_id = "ZZZ4000001"
    assert (
        campaigns.client.delete(f"/api/voters/{voter_id}", headers=campaigns.admin).status_code
        == 200
    )
    hard = campaigns.client.post(
        f"/api/voters/{voter_id}/hard-delete", headers=campaigns.admin, json={}
    )
    assert hard.status_code == 200, hard.text

    from app import database

    db = database.SessionLocal()
    try:
        mobiles = db.scalars(
            select(CampaignRecipient.mobile).where(CampaignRecipient.voter_id == voter_id)
        ).all()
        assert mobiles
        assert all(mobile == SCRUBBED_MOBILE for mobile in mobiles)
    finally:
        db.close()
    detail = _detail(campaigns, "whatsapp", created["id"]).json()
    _assert_no_full_mobiles(detail)


# --- security / send-time resolution (ECC #61) -------------------------------


def test_soft_deleted_voter_is_not_messaged_on_scheduled_slot(campaigns):
    _seed_voters(campaigns)
    created = _create_campaign(
        campaigns,
        send_mode="daily",
        daily_times='["09:00"]',
        start_date="2026-11-26",
        end_date="2026-11-26",
    )
    assert created.status_code == 201, created.text
    campaign_id = created.json()["id"]
    assert (
        campaigns.client.delete("/api/voters/ZZZ4000001", headers=campaigns.admin).status_code
        == 200
    )

    from app import database
    from app.services import campaign_send

    db = database.SessionLocal()
    try:
        campaign = db.get(Campaign, campaign_id)
        campaign.created_at = dump_dt(datetime(2026, 11, 26, 2, 0, tzinfo=timezone.utc))
        db.commit()
        now = datetime(2026, 11, 26, 9, 5, tzinfo=IST)
        assert process_due_schedules(db, campaigns.settings, Path(campaigns.photo_dir), now=now) == 1
        recipients = {
            row.voter_id: row
            for row in db.scalars(
                select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign_id)
            ).all()
        }
        assert recipients["ZZZ4000001"].status == "rejected"
        assert recipients["ZZZ4000001"].last_error == VOTER_REMOVED_MESSAGE
        assert recipients["ZZZ4000002"].status == "sent"
        deliveries = db.scalars(
            select(CampaignDelivery).where(CampaignDelivery.campaign_id == campaign_id)
        ).all()
        by_recipient = {row.recipient_id: row for row in deliveries}
        assert by_recipient[recipients["ZZZ4000001"].id].status == "rejected"
        assert by_recipient[recipients["ZZZ4000002"].id].status == "sent"
        # live_mobile_for_send never falls back to stored recipient.mobile
        mobile, reason = campaign_send.live_mobile_for_send(db, recipients["ZZZ4000001"])
        assert mobile is None and reason == VOTER_REMOVED_MESSAGE
    finally:
        db.close()


def test_invalidated_voter_mobile_is_not_messaged_on_scheduled_slot(campaigns):
    _seed_voters(campaigns)
    created = _create_campaign(
        campaigns,
        send_mode="daily",
        daily_times='["09:00"]',
        start_date="2026-11-26",
        end_date="2026-11-26",
    )
    campaign_id = created.json()["id"]

    from app import database
    from app.models import Voter

    db = database.SessionLocal()
    try:
        voter = db.get(Voter, "ZZZ4000002")
        voter.mobile = "12"
        db.commit()
        campaign = db.get(Campaign, campaign_id)
        campaign.created_at = dump_dt(datetime(2026, 11, 26, 2, 0, tzinfo=timezone.utc))
        db.commit()
        now = datetime(2026, 11, 26, 9, 5, tzinfo=IST)
        assert process_due_schedules(db, campaigns.settings, Path(campaigns.photo_dir), now=now) == 1
        recipients = {
            row.voter_id: row
            for row in db.scalars(
                select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign_id)
            ).all()
        }
        assert recipients["ZZZ4000001"].status == "sent"
        assert recipients["ZZZ4000002"].status == "rejected"
        assert recipients["ZZZ4000002"].last_error == MISSING_MOBILE_MESSAGE
    finally:
        db.close()


def test_cancel_mid_send_stops_remaining_recipients(campaigns):
    _seed_voters(campaigns)
    created = _create_campaign(
        campaigns, channel="sms", send_mode="daily", daily_times='["10:00"]'
    )
    campaign_id = created.json()["id"]
    from app import database
    from app.services import campaign_send

    db = database.SessionLocal()

    class CancelAfterFirst:
        def mode_for(self, channel):
            return "dry_run"

        def send(self, **kwargs):
            # Cancel on the same Session so the next loop iteration sees it.
            campaign = db.get(Campaign, campaign_id)
            campaign.status = "cancelled"
            campaign.schedule_active = False
            db.commit()
            return SimpleNamespace(
                ok=True, provider_mode="dry_run", provider_ref="ok", detail="ok"
            )

    try:
        with patch.object(campaign_send, "get_provider", return_value=CancelAfterFirst()):
            sent = campaign_send.process_campaign_slot(
                db,
                campaign_id,
                slot_key="cancel-slot",
                settings=campaigns.settings,
                photo_dir=Path(campaigns.photo_dir),
            )
        assert sent == 1
        campaign = db.get(Campaign, campaign_id)
        assert campaign.status == "cancelled"
        deliveries = db.scalars(
            select(CampaignDelivery).where(
                CampaignDelivery.campaign_id == campaign_id,
                CampaignDelivery.slot_key == "cancel-slot",
            )
        ).all()
        assert len(deliveries) == 1
        assert deliveries[0].status == "sent"
    finally:
        db.close()


def test_non_creator_manager_cannot_pause_cancel_or_delete(campaigns):
    _seed_voters(campaigns)
    other = make_user(campaigns.ctx, "camp.mgr2", "mgr2-pass-1234", role="manager")
    created = _create_campaign(
        campaigns,
        headers=campaigns.manager,
        send_mode="daily",
        daily_times='["08:00"]',
    )
    assert created.status_code == 201, created.text
    campaign_id = created.json()["id"]
    assert (
        campaigns.client.post(
            f"/api/campaigns/whatsapp/{campaign_id}/pause", headers=other
        ).status_code
        == 403
    )
    assert (
        campaigns.client.post(
            f"/api/campaigns/whatsapp/{campaign_id}/cancel", headers=other
        ).status_code
        == 403
    )
    assert (
        campaigns.client.delete(
            f"/api/campaigns/whatsapp/{campaign_id}", headers=other
        ).status_code
        == 403
    )


def test_create_rate_limit_and_max_recipients_cap(campaigns, monkeypatch):
    _seed_voters(campaigns)
    monkeypatch.setattr(campaigns.settings, "campaign_create_rate_limit", 1)
    first = _create_campaign(campaigns, title="rate-one")
    assert first.status_code == 201, first.text
    second = _create_campaign(campaigns, title="rate-two")
    assert second.status_code == 429

    monkeypatch.setattr(campaigns.settings, "campaign_create_rate_limit", 100)
    monkeypatch.setattr(campaigns.settings, "campaign_max_recipients", 1)
    capped = _create_campaign(campaigns, title="too-many", target_values='["1"]')
    assert capped.status_code == 422
    assert "at most 1" in capped.json()["detail"]


def test_photo_file_public_rejects_bad_sig_expired_and_cancelled(campaigns, monkeypatch):
    _seed_voters(campaigns)
    monkeypatch.setattr(campaigns.settings, "public_base_url", "https://example.test")
    created = _create_campaign(
        campaigns, channel="sms", photos=[_jpeg_bytes()], send_mode="daily", daily_times='["20:00"]'
    )
    assert created.status_code == 201, created.text
    campaign_id = created.json()["id"]
    detail = _detail(campaigns, "sms", campaign_id).json()
    photo_id = detail["photos"][0]["id"]
    urls = build_photo_urls(campaigns.settings, [photo_id])
    assert urls
    good_path = urls[0].split("example.test", 1)[1]

    assert campaigns.client.get(good_path).status_code == 200
    assert (
        campaigns.client.get(
            f"/api/campaigns/photos/{photo_id}/public?exp=9999999999&sig=deadbeef"
        ).status_code
        == 403
    )
    expired = int(datetime.now(timezone.utc).timestamp()) - 10
    bad_sig = sign_photo_token(campaigns.settings, photo_id, expired)
    assert (
        campaigns.client.get(
            f"/api/campaigns/photos/{photo_id}/public?exp={expired}&sig={bad_sig}"
        ).status_code
        == 403
    )
    assert verify_photo_token(campaigns.settings, photo_id, expired, bad_sig) is False

    cancelled = campaigns.client.post(
        f"/api/campaigns/sms/{campaign_id}/cancel", headers=campaigns.admin
    )
    assert cancelled.status_code == 200
    assert campaigns.client.get(good_path).status_code == 410


def test_sms_api_url_over_http_is_rejected_by_config(monkeypatch):
    from app.config import load_settings

    monkeypatch.setenv("SECRET_KEY", "test-secret-key-0123456789")
    monkeypatch.setenv("ADMIN_USERNAME", "")
    monkeypatch.setenv("ADMIN_PASSWORD", "")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("SMS_API_URL", "http://insecure.example/send")
    monkeypatch.setenv("SMS_API_KEY", "key")
    with pytest.raises(RuntimeError, match="SMS_API_URL must use HTTPS"):
        load_settings()
