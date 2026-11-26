"""Campaign messaging (SMS / WhatsApp): targeting, photos, schedules, dry-run, masking.

Synthetic voters and generated JPEGs only. No real provider credentials are set, so
sends stay in dry-run mode.
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from PIL import Image
from sqlalchemy import select

from app.campaign_rules import MAX_PHOTOS_PER_CAMPAIGN
from app.models import Campaign, CampaignDelivery, CampaignRecipient, CampaignScheduleRun
from app.services.campaign_scheduler import due_slots_for_campaign, process_due_schedules
from app.services.campaigns import mask_campaign_mobile
from tests.conftest import csrf_from_page, make_user, voter_payload

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def campaigns(ctx, tmp_path, monkeypatch):
    photo_dir = tmp_path / "campaign-photos-test"
    photo_dir.mkdir(parents=True, exist_ok=True)
    ctx.client.app.state.settings.campaign_photo_dir = str(photo_dir)
    # Keep the background scheduler from racing tests; manual /scheduler/run is used instead.
    monkeypatch.setattr(ctx.client.app.state.settings, "campaign_scheduler_seconds", 3600)
    clerk = make_user(ctx, "camp.clerk", "clerk-pass-123", role="user")
    return SimpleNamespace(
        ctx=ctx,
        client=ctx.client,
        photo_dir=photo_dir,
        admin=ctx.auth,
        clerk=clerk,
        settings=ctx.client.app.state.settings,
    )


def _jpeg_bytes(color=(40, 120, 200)) -> bytes:
    image = Image.new("RGB", (32, 32), color)
    buf = BytesIO()
    image.save(buf, format="JPEG")
    return buf.getvalue()


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
        # Invalid mobile — must be excluded from recipient resolution.
        voter_payload(
            voter_id="ZZZ4000004",
            mobile="12345",
            ward="1",
            booth="10",
            panchayath="Nathavaram",
            mandal="Nathavaram",
            constituency="Narsipatnam",
        ),
        # Missing mobile — excluded.
        voter_payload(
            voter_id="ZZZ4000005",
            mobile=None,
            ward="1",
            booth="10",
            panchayath="Nathavaram",
            mandal="Nathavaram",
            constituency="Narsipatnam",
        ),
    ]
    # Bypass API validation for the invalid-mobile row by patching after a valid create.
    for row in rows:
        if row["mobile"] in {None, "12345"}:
            continue
        response = client.post("/api/voters", headers=auth, json=row)
        assert response.status_code == 201, response.text

    # Insert invalid / blank mobile voters through the API only when accepted; otherwise
    # create valid then rewrite in DB for exclusion tests.
    from app import database
    from app.models import Voter
    from app.validation import dump_dt
    from datetime import timezone

    now = dump_dt(datetime.now(timezone.utc))
    db = database.SessionLocal()
    try:
        for voter_id, mobile, ward in (
            ("ZZZ4000004", "12345", "1"),
            ("ZZZ4000005", None, "1"),
        ):
            existing = db.get(Voter, voter_id)
            if existing is None:
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
    headers = headers or env.admin
    data = {
        "title": fields.pop("title", "ప్రచారం Hello 🙏"),
        "message": fields.pop("message", "Meeting at 6pm ★"),
        "target_type": fields.pop("target_type", "ward"),
        "target_values": fields.pop("target_values", '["1"]'),
        "send_mode": fields.pop("send_mode", "now"),
        "daily_times": fields.pop("daily_times", "[]"),
        "schedule_active": fields.pop("schedule_active", "true"),
    }
    data.update({k: v for k, v in fields.items() if v is not None})
    files = None
    if photos:
        files = [("photos", (f"p{i}.jpg", blob, "image/jpeg")) for i, blob in enumerate(photos)]
    return env.client.post(
        f"/api/campaigns/{channel}",
        headers=headers,
        data=data,
        files=files,
    )


# --- masking -----------------------------------------------------------------


def test_mask_campaign_mobile_keeps_only_last_four_characters():
    assert mask_campaign_mobile("9000000101") == "******0101"
    assert mask_campaign_mobile("+91 90000 00102") == "***********0102"
    assert mask_campaign_mobile("12") == "**"
    assert mask_campaign_mobile("") == ""
    assert mask_campaign_mobile(None) is None


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


def _web_login(env, username="admin", password="admin-pass-123"):
    env.client.cookies.clear()
    response = env.client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


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
        assert "data-open-quick-message" in page.text


def test_sidebar_links_sms_and_whatsapp_without_coming_soon(campaigns):
    _web_login(campaigns)
    html = campaigns.client.get("/voters").text
    assert 'href="/campaign/sms"' in html
    assert 'href="/campaign/whatsapp"' in html
    assert "Coming soon" in html  # other stubs remain


# --- recipient resolution ----------------------------------------------------


def test_preview_resolves_ward_and_booth_and_skips_invalid_mobiles(campaigns):
    _seed_voters(campaigns)
    ward = campaigns.client.post(
        "/api/campaigns/whatsapp/preview",
        headers=campaigns.admin,
        json={"target_type": "ward", "target_values": ["1"]},
    )
    assert ward.status_code == 200, ward.text
    assert ward.json()["with_mobile"] == 2

    booth = campaigns.client.post(
        "/api/campaigns/sms/preview",
        headers=campaigns.clerk,
        json={"target_type": "booth", "target_values": ["10"]},
    )
    assert booth.status_code == 200
    assert booth.json()["with_mobile"] == 2

    options = campaigns.client.get(
        "/api/campaigns/whatsapp/targets/ward", headers=campaigns.admin
    )
    assert options.status_code == 200
    assert "1" in options.json()["options"]
    assert "2" in options.json()["options"]


# --- send now dry-run + KPIs + masking ---------------------------------------


def test_send_now_whatsapp_dry_run_updates_kpis_and_masks_mobiles(campaigns):
    _seed_voters(campaigns)
    created = _create_campaign(campaigns, channel="whatsapp")
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "completed"
    assert body["provider_mode"] == "dry_run"
    assert body["recipient_total"] == 2
    assert all(row["mobile"].endswith("0101") or row["mobile"].endswith("0102") for row in body["recipients"])
    assert all("900000" not in row["mobile"] for row in body["recipients"])
    assert all(row["status"] == "sent" for row in body["recipients"])

    summary = campaigns.client.get("/api/campaigns/whatsapp/summary", headers=campaigns.admin)
    assert summary.status_code == 200
    counts = summary.json()["counts"]
    assert counts["sent"] == 2
    assert counts["initiated"] == 0
    assert summary.json()["provider_mode"] == "dry_run"

    # Stored mobiles remain full.
    from app import database

    db = database.SessionLocal()
    try:
        mobiles = db.scalars(select(CampaignRecipient.mobile)).all()
        assert "9000000101" in mobiles
        assert "9000000102" in mobiles
    finally:
        db.close()


def test_send_now_sms_includes_photo_links_in_dry_run_detail(campaigns):
    _seed_voters(campaigns)
    created = _create_campaign(
        campaigns, channel="sms", photos=[_jpeg_bytes(), _jpeg_bytes((10, 200, 10))]
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert len(body["photos"]) == 2
    assert body["status"] == "completed"
    detail = campaigns.client.get(
        f"/api/campaigns/sms/{body['id']}", headers=campaigns.admin
    ).json()
    assert detail["photos"][0]["url"].startswith("/api/campaigns/photos/")


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
    assert len(response.json()["photos"]) == 5


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
    assert created.status_code == 201
    campaign_id = created.json()["id"]

    paused = campaigns.client.post(
        f"/api/campaigns/whatsapp/{campaign_id}/pause", headers=campaigns.admin
    )
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    assert paused.json()["schedule_active"] is False

    resumed = campaigns.client.post(
        f"/api/campaigns/whatsapp/{campaign_id}/resume", headers=campaigns.admin
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "scheduled"
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

    now = datetime(2026, 11, 26, 15, 30, tzinfo=IST)
    from app import database

    db = database.SessionLocal()
    try:
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
        runs = db.scalars(
            select(CampaignScheduleRun).where(CampaignScheduleRun.campaign_id == campaign_id)
        ).all()
        assert len(runs) == 2
        deliveries = db.scalars(
            select(CampaignDelivery).where(CampaignDelivery.campaign_id == campaign_id)
        ).all()
        # 2 recipients × 2 slots
        assert len(deliveries) == 4
        assert all(row.status == "sent" for row in deliveries)
        assert all(row.provider_mode == "dry_run" for row in deliveries)
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
    assert created.status_code == 201
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


def test_manual_scheduler_endpoint(campaigns):
    _seed_voters(campaigns)
    _create_campaign(
        campaigns,
        send_mode="daily",
        daily_times='["00:00"]',
        start_date="2000-01-01",
        end_date="2099-01-01",
    )
    response = campaigns.client.post(
        "/api/campaigns/scheduler/run", headers=campaigns.admin
    )
    assert response.status_code == 200
    assert "processed_slots" in response.json()


def test_web_detail_shows_masked_mobile_only(campaigns):
    _seed_voters(campaigns)
    created = _create_campaign(campaigns, channel="sms").json()
    _web_login(campaigns)
    page = campaigns.client.get(f"/campaign/sms/{created['id']}")
    assert page.status_code == 200
    assert "******0101" in page.text or "******0102" in page.text
    assert "9000000101" not in page.text


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
