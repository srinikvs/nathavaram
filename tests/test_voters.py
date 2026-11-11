"""CRUD, validation, and search, including Telugu text."""

from __future__ import annotations

from sqlalchemy import text

from app import database
from tests.conftest import voter_payload


def test_create_search_update_and_soft_delete(ctx):
    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload(party="tdp", gender="female"))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["party"] == "TDP"
    assert body["gender"] == "Female"
    assert body["voter_id"] == "ZZZ1000001"
    assert body["revision"] >= 1
    assert body["mobile"] == "9000000021"

    listed = ctx.client.get("/api/voters", headers=ctx.auth, params={"q": "testperson"})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    by_house = ctx.client.get("/api/voters", headers=ctx.auth, params={"house_no": "12"})
    assert by_house.json()["total"] == 1
    by_booth = ctx.client.get("/api/voters", headers=ctx.auth, params={"booth": "12"})
    assert by_booth.json()["total"] == 1
    by_party = ctx.client.get("/api/voters", headers=ctx.auth, params={"party": "jana sena"})
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
    assert ctx.client.get("/api/voters", headers=ctx.auth, params={"party": "jana sena"}).json()["total"] == 1

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
    found = ctx.client.get("/api/voters", headers=ctx.auth, params={"q": "నమూనా"})
    assert found.json()["total"] == 1
    found_mobile = ctx.client.get("/api/voters", headers=ctx.auth, params={"mobile": "9000000007"})
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
