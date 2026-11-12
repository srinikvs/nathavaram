"""CRUD, validation, and search, including Telugu text."""

from __future__ import annotations

from sqlalchemy import text

from app import database
from app.constants import DATA_FIELDS
from app.models import Voter
from tests.conftest import csrf_from_page, search, voter_payload


def test_create_search_update_and_soft_delete(ctx):
    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload(party="tdp", gender="female"))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["party"] == "TDP"
    assert body["gender"] == "Female"
    assert body["voter_id"] == "ZZZ1000001"
    assert body["revision"] >= 1
    assert body["mobile"] == "9000000021"

    listed = search(ctx, q="testperson")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    by_address = search(ctx, q="12-A Sample")
    assert by_address.json()["total"] == 1
    by_village = search(ctx, village="sampleville")
    assert by_village.json()["total"] == 1
    by_booth = search(ctx, booth="12")
    assert by_booth.json()["total"] == 1
    by_party = search(ctx, party="jana sena")
    assert by_party.json()["total"] == 0

    patched = ctx.client.patch(
        f"/api/voters/{body['id']}",
        headers=ctx.auth,
        json={"party": "Jana Sena", "age": None},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["party"] == "Jana Sena"
    assert patched.json()["age"] is None
    assert patched.json()["revision"] > body["revision"]
    assert search(ctx, party="jana sena").json()["total"] == 1

    removed = ctx.client.delete(f"/api/voters/{body['id']}", headers=ctx.auth)
    assert removed.status_code == 200
    assert removed.json()["deleted_at"] is not None
    assert ctx.client.get(f"/api/voters/{body['id']}", headers=ctx.auth).status_code == 404
    assert ctx.client.get("/api/voters", headers=ctx.auth).json()["total"] == 0

    reused = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload())
    assert reused.status_code == 201
    assert reused.json()["id"] != body["id"]


def test_telugu_partial_search_and_validation(ctx):
    created = ctx.client.post(
        "/api/voters",
        headers=ctx.auth,
        json=voter_payload(
            voter_id="zzz1000008",
            first_name="నమూనా",
            surname="ఐదు",
            relative_name="బంధువు",
            c_city="నాతవరం",
            mobile="09000000007",
            party=None,
        ),
    )
    assert created.status_code == 201, created.text
    saved = created.json()
    assert saved["voter_id"] == "ZZZ1000008"
    assert saved["first_name"] == "నమూనా"
    assert saved["mobile"] == "09000000007"
    assert saved["party"] is None
    found = search(ctx, q="నమూనా")
    assert found.json()["total"] == 1
    found_mobile = search(ctx, mobile="9000000007")
    assert found_mobile.json()["total"] == 1

    prefixed = ctx.client.post(
        "/api/voters",
        headers=ctx.auth,
        json=voter_payload(voter_id="ZZZ1000009", mobile="+91 90000 00022", first_name="Prefix"),
    )
    assert prefixed.status_code == 201, prefixed.text
    assert prefixed.json()["mobile"] == "+91 90000 00022"

    bad_mobile = ctx.client.post(
        "/api/voters",
        headers=ctx.auth,
        json=voter_payload(voter_id="ZZZ1000010", mobile="12345"),
    )
    assert bad_mobile.status_code == 422
    bad_age = ctx.client.post(
        "/api/voters",
        headers=ctx.auth,
        json=voter_payload(voter_id="ZZZ1000011", age=0),
    )
    assert bad_age.status_code == 422
    bad_party = ctx.client.post(
        "/api/voters",
        headers=ctx.auth,
        json=voter_payload(voter_id="ZZZ1000012", party="Not a party"),
    )
    assert bad_party.status_code == 422
    duplicate = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload(voter_id="ZZZ1000008"))
    assert duplicate.status_code == 409


def test_active_voter_id_index_is_partial(ctx):
    _ = ctx
    db = database.SessionLocal()
    try:
        sql = db.execute(
            text("SELECT sql FROM sqlite_master WHERE name = 'uq_voters_voter_id_active'")
        ).scalar()
    finally:
        db.close()
    assert sql is not None
    assert "deleted_at IS NULL" in sql


def test_patch_null_required_fields_is_422_not_409_and_row_is_unchanged(ctx):
    first = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload()).json()
    ctx.client.post(
        "/api/voters",
        headers=ctx.auth,
        json=voter_payload(voter_id="ZZZ1000002", first_name="Second", mobile="9000000022"),
    )
    for body in ({"first_name": None}, {"gender": None}, {"first_name": None, "gender": None}):
        response = ctx.client.patch(f"/api/voters/{first['id']}", headers=ctx.auth, json=body)
        assert response.status_code == 422, response.text
        assert "already exists" not in response.text
    null_voter_id = ctx.client.patch(f"/api/voters/{first['id']}", headers=ctx.auth, json={"voter_id": None})
    assert null_voter_id.status_code == 422

    current = ctx.client.get(f"/api/voters/{first['id']}", headers=ctx.auth).json()
    assert current == first
    # Changing VoterId is refused outright (422), even to a value another voter already uses.
    clash = ctx.client.patch(f"/api/voters/{first['id']}", headers=ctx.auth, json={"voter_id": "ZZZ1000002"})
    assert clash.status_code == 422


def test_sensitive_filters_are_refused_in_get_query_and_work_in_post(ctx):
    ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload())
    for key, value in (
        ("mobile", "9000000021"),
        ("voter_id", "ZZZ1000001"),
        ("caste", "SampleGroupA"),
        ("q", "testperson"),
    ):
        refused = ctx.client.get("/api/voters", headers=ctx.auth, params={key: value})
        assert refused.status_code == 422, (key, refused.text)
        assert value not in refused.text
    paged = ctx.client.get("/api/voters", headers=ctx.auth, params={"page": 1, "page_size": 10})
    assert paged.status_code == 200
    assert paged.json()["total"] == 1

    for filters in (
        {"mobile": "9000000021"},
        {"voter_id": "ZZZ1000001"},
        {"caste": "samplegroupa"},
        {"q": "testperson"},
    ):
        found = search(ctx, **filters)
        assert found.status_code == 200, found.text
        assert found.json()["total"] == 1
    assert search(ctx, mobile="9000000099").json()["total"] == 0
    assert search(ctx, page=0).status_code == 422


def test_web_list_ignores_url_filters_and_searches_by_post(ctx):
    ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload())
    ctx.client.post(
        "/api/voters",
        headers=ctx.auth,
        json=voter_payload(voter_id="ZZZ1000002", first_name="Otherperson", mobile="9000000022"),
    )
    page = ctx.client.get("/voters", params={"mobile": "9000000022"})
    assert page.status_code == 200
    assert "Testperson" in page.text and "Otherperson" in page.text
    token = csrf_from_page(page.text)
    found = ctx.client.post("/voters/search", data={"csrf_token": token, "mobile": "9000000022"})
    assert found.status_code == 200
    assert "Otherperson" in found.text
    assert "Testperson" not in found.text
    missing_token = ctx.client.post("/voters/search", data={"mobile": "9000000022"})
    assert missing_token.status_code == 403


def test_soft_delete_strips_personal_fields_from_row_and_sync_pull(ctx):
    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload()).json()
    removed = ctx.client.delete(f"/api/voters/{created['id']}", headers=ctx.auth)
    assert removed.status_code == 200
    body = removed.json()
    assert body["deleted_at"] is not None
    assert body["revision"] > created["revision"]

    db = database.SessionLocal()
    try:
        row = db.get(Voter, created["id"])
        assert row is not None
        assert row.deleted_at is not None
        for field in DATA_FIELDS:
            if field in {"voter_id", "first_name", "gender", "is_local"}:
                continue
            assert getattr(row, field) is None, field
        assert row.is_local is False
        assert row.first_name == ""
        assert row.gender == "Unknown"
        assert row.voter_id != "ZZZ1000001"
    finally:
        db.close()

    pulled = ctx.client.get("/api/sync/pull", headers=ctx.auth, params={"since": created["revision"]}).json()
    tomb = next(item for item in pulled["changes"] if item["id"] == created["id"])
    assert tomb["deleted_at"] is not None
    assert tomb["revision"] == body["revision"]
    for field in ("mobile", "caste", "party", "surname", "relative_name", "address", "village", "permanent_address", "profession", "age"):
        assert tomb[field] is None, field
    assert tomb["first_name"] == ""
    raw = str(pulled)
    for secret in ("Testperson", "Alpha", "SampleGroupA", "9000000021", "TDP", "ZZZ1000001", "Teacher"):
        assert secret not in raw
