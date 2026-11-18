"""Pull, push, last-write-wins, and sync keyed on voter_id."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError

from app.constants import DATA_FIELDS
from tests.conftest import voter_payload


def _now(seconds: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _snapshot(voter: dict, **overrides) -> dict:
    body = {
        "updated_at": voter["updated_at"],
        "deleted": voter["deleted_at"] is not None,
    }
    for field in DATA_FIELDS:
        body[field] = voter[field]
    body.update(overrides)
    return body


def _push(ctx, changes):
    response = ctx.client.post(
        "/api/sync/push",
        headers=ctx.auth,
        json={"device_id": "lab-phone-1", "changes": changes},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _new_change(voter_id: str, **overrides) -> dict:
    change = {
        "voter_id": voter_id,
        "updated_at": _now(),
        "deleted": False,
        "first_name": "Syncperson",
    }
    change.update(overrides)
    return change


def test_pull_push_conflict_and_replay(ctx):
    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload())
    assert created.status_code == 201, created.text
    voter = created.json()

    stale = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    conflict = _push(ctx, [_snapshot(voter, updated_at=stale, first_name="Stale")])
    assert conflict["results"][0]["status"] == "conflict"
    assert conflict["results"][0]["voter_id"] == voter["voter_id"]
    current = ctx.client.get(f"/api/voters/{voter['voter_id']}", headers=ctx.auth).json()
    assert current["first_name"] == "Testperson"
    assert current["revision"] == voter["revision"]

    same_time = _push(ctx, [_snapshot(current, first_name="Different")])
    assert same_time["results"][0]["status"] == "conflict"
    replay = _push(ctx, [_snapshot(current)])
    assert replay["results"][0]["status"] == "applied"
    assert replay["results"][0]["server_revision"] == current["revision"]

    applied = _push(ctx, [_snapshot(current, updated_at=_now(2), first_name="Fresh", party="Jana Sena")])
    assert applied["results"][0]["status"] == "applied"
    updated = ctx.client.get(f"/api/voters/{voter['voter_id']}", headers=ctx.auth).json()
    assert updated["first_name"] == "Fresh"
    assert updated["party"] == "Jana Sena"
    assert updated["revision"] > current["revision"]
    assert updated["source_id"] == "lab-phone-1"

    pulled = ctx.client.get("/api/sync/pull", headers=ctx.auth, params={"since": voter["revision"]})
    assert pulled.status_code == 200
    changes = pulled.json()["changes"]
    assert any(item["voter_id"] == voter["voter_id"] and item["first_name"] == "Fresh" for item in changes)
    assert all("id" not in item for item in changes)
    caught_up = ctx.client.get(
        "/api/sync/pull",
        headers=ctx.auth,
        params={"since": pulled.json()["server_revision"]},
    )
    assert caught_up.json()["changes"] == []
    assert caught_up.json()["has_more"] is False


def test_push_creates_a_new_voter_keyed_on_its_voter_id(ctx):
    body = _push(ctx, [_new_change("zzz1000003", last_name="Device", house_no="4", is_local="Local")])

    result = body["results"][0]
    assert result["status"] == "applied"
    assert result["voter_id"] == "ZZZ1000003"
    assert result["server_record"]["last_name"] == "Device"
    assert result["server_record"]["is_local"] is True
    fetched = ctx.client.get("/api/voters/ZZZ1000003", headers=ctx.auth)
    assert fetched.status_code == 200
    assert fetched.json()["house_no"] == "4"


def test_push_for_an_existing_voter_id_merges_instead_of_clashing(ctx):
    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload()).json()

    body = _push(ctx, [_new_change(created["voter_id"], updated_at=_now(2), first_name="FromDevice")])

    assert body["results"][0]["status"] == "applied"
    merged = ctx.client.get(f"/api/voters/{created['voter_id']}", headers=ctx.auth).json()
    assert merged["first_name"] == "FromDevice"
    assert ctx.client.get("/api/voters", headers=ctx.auth).json()["total"] == 1


def test_a_bad_voter_id_is_reported_and_the_rest_of_the_batch_applies(ctx):
    good_a = _new_change("ZZZ1000003")
    bad = _new_change("not a valid id!")
    good_b = _new_change("ZZZ1000004")

    body = _push(ctx, [good_a, bad, good_b])

    assert [item["status"] for item in body["results"]] == ["applied", "validation_error", "applied"]
    assert "voter_id" in body["results"][1]["message"]
    assert ctx.client.get("/api/voters/ZZZ1000003", headers=ctx.auth).status_code == 200
    assert ctx.client.get("/api/voters/ZZZ1000004", headers=ctx.auth).status_code == 200


def test_database_key_race_rejects_only_that_change(ctx, monkeypatch):
    from app.services import sync as sync_service

    real_apply_one = sync_service._apply_one

    def lose_the_race_for_one_key(db, change, device_id, privileged):
        if change.voter_id == "ZZZ1000005":
            raise IntegrityError("INSERT INTO voters", {}, Exception("UNIQUE constraint failed"))
        return real_apply_one(db, change, device_id, privileged)

    monkeypatch.setattr(sync_service, "_apply_one", lose_the_race_for_one_key)

    body = _push(ctx, [_new_change("ZZZ1000005"), _new_change("ZZZ1000006")])

    assert [item["status"] for item in body["results"]] == ["conflict", "applied"]
    assert body["results"][0]["voter_id"] == "ZZZ1000005"
    assert ctx.client.get("/api/voters/ZZZ1000006", headers=ctx.auth).status_code == 200
    assert ctx.client.get("/api/voters/ZZZ1000005", headers=ctx.auth).status_code == 404


def test_stale_base_revision_is_a_conflict_not_last_write_wins(ctx):
    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload()).json()
    key = created["voter_id"]
    edited = ctx.client.patch(f"/api/voters/{key}", headers=ctx.auth, json={"first_name": "ServerEdit"}).json()
    assert edited["revision"] > created["revision"]
    newer = _now(2)

    stale = _push(
        ctx,
        [_snapshot(edited, updated_at=newer, first_name="DeviceEdit", base_revision=created["revision"])],
    )

    result = stale["results"][0]
    assert result["status"] == "conflict"
    assert result["server_record"]["first_name"] == "ServerEdit"
    current = ctx.client.get(f"/api/voters/{key}", headers=ctx.auth).json()
    assert current["first_name"] == "ServerEdit"
    assert current["revision"] == edited["revision"]

    fresh = _push(
        ctx,
        [_snapshot(edited, updated_at=newer, first_name="DeviceEdit", base_revision=edited["revision"])],
    )
    assert fresh["results"][0]["status"] == "applied"
    assert ctx.client.get(f"/api/voters/{key}", headers=ctx.auth).json()["first_name"] == "DeviceEdit"

    replay = _push(
        ctx,
        [_snapshot(edited, updated_at=newer, first_name="DeviceEdit", base_revision=created["revision"])],
    )
    assert replay["results"][0]["status"] == "applied"
    assert replay["results"][0]["message"] == "Already stored"


def test_stale_base_revision_blocks_a_delete(ctx):
    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload()).json()
    key = created["voter_id"]
    edited = ctx.client.patch(f"/api/voters/{key}", headers=ctx.auth, json={"caste": "Other"}).json()
    newer = _now(2)

    blocked = _push(
        ctx,
        [{"voter_id": key, "updated_at": newer, "deleted": True, "base_revision": created["revision"]}],
    )

    assert blocked["results"][0]["status"] == "conflict"
    assert ctx.client.get(f"/api/voters/{key}", headers=ctx.auth).status_code == 200
    allowed = _push(
        ctx,
        [{"voter_id": key, "updated_at": newer, "deleted": True, "base_revision": edited["revision"]}],
    )
    assert allowed["results"][0]["status"] == "applied"
    assert ctx.client.get(f"/api/voters/{key}", headers=ctx.auth).status_code == 404


def test_sync_delete_leaves_a_stripped_tombstone_that_keeps_its_key(ctx):
    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload()).json()

    applied = _push(ctx, [{"voter_id": created["voter_id"], "updated_at": _now(2), "deleted": True}])

    result = applied["results"][0]
    assert result["status"] == "applied"
    assert result["server_record"]["deleted_at"] is not None
    pulled = ctx.client.get("/api/sync/pull", headers=ctx.auth, params={"since": created["revision"]}).json()
    match = next(item for item in pulled["changes"] if item["voter_id"] == created["voter_id"])
    for field in (
        "mobile", "caste", "party", "last_name", "relative_name", "house_no", "street",
        "village", "panchayath", "mandal", "current_city",
    ):
        assert match[field] is None, field
    assert match["first_name"] == ""
    assert "Testperson" not in str(pulled) and "9000000021" not in str(pulled)


def test_a_newer_push_revives_a_tombstone_and_an_older_one_does_not(ctx):
    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload()).json()
    key = created["voter_id"]
    assert ctx.client.delete(f"/api/voters/{key}", headers=ctx.auth).status_code == 200

    older = _push(ctx, [_new_change(key, updated_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())])
    assert older["results"][0]["status"] == "conflict"
    assert ctx.client.get(f"/api/voters/{key}", headers=ctx.auth).status_code == 404

    newer = _push(ctx, [_new_change(key, updated_at=_now(2), first_name="Revived")])
    assert newer["results"][0]["status"] == "applied"
    revived = ctx.client.get(f"/api/voters/{key}", headers=ctx.auth)
    assert revived.status_code == 200
    assert revived.json()["first_name"] == "Revived"
    assert revived.json()["deleted_at"] is None


def test_future_clock_and_delete_of_unknown_voter_id(ctx):
    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload()).json()
    future = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()

    rejected = _push(ctx, [_snapshot(created, updated_at=future, deleted=True)])
    assert rejected["results"][0]["status"] == "validation_error"

    ignored = _push(ctx, [{"voter_id": "ZZZ9999999", "updated_at": _now(), "deleted": True}])
    assert ignored["results"][0]["status"] == "applied"
    assert "ignored" in (ignored["results"][0]["message"] or "")
