"""Pull, push, last-write-wins, and VoterId conflicts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.constants import DATA_FIELDS
from tests.conftest import voter_payload


def _snapshot(voter: dict, **overrides) -> dict:
    body = {
        "id": voter["id"],
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


def test_pull_push_conflict_and_replay(ctx):
    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload())
    assert created.status_code == 201, created.text
    voter = created.json()

    stale = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    conflict = _push(ctx, [_snapshot(voter, updated_at=stale, first_name="Stale")])
    assert conflict["results"][0]["status"] == "conflict"
    current = ctx.client.get(f"/api/voters/{voter['id']}", headers=ctx.auth).json()
    assert current["first_name"] == "Testperson"
    assert current["revision"] == voter["revision"]

    same_time = _push(ctx, [_snapshot(current, first_name="Different")])
    assert same_time["results"][0]["status"] == "conflict"
    replay = _push(ctx, [_snapshot(current)])
    assert replay["results"][0]["status"] == "applied"
    assert replay["results"][0]["server_revision"] == current["revision"]

    newer = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()
    applied = _push(ctx, [_snapshot(current, updated_at=newer, first_name="Fresh", party="Jana Sena")])
    assert applied["results"][0]["status"] == "applied"
    updated = ctx.client.get(f"/api/voters/{voter['id']}", headers=ctx.auth).json()
    assert updated["first_name"] == "Fresh"
    assert updated["party"] == "Jana Sena"
    assert updated["revision"] > current["revision"]
    assert updated["source_id"] == "lab-phone-1"

    pulled = ctx.client.get("/api/sync/pull", headers=ctx.auth, params={"since": voter["revision"]})
    assert pulled.status_code == 200
    changes = pulled.json()["changes"]
    assert any(item["id"] == voter["id"] and item["first_name"] == "Fresh" for item in changes)
    caught_up = ctx.client.get(
        "/api/sync/pull",
        headers=ctx.auth,
        params={"since": pulled.json()["server_revision"]},
    )
    assert caught_up.json()["changes"] == []
    assert caught_up.json()["has_more"] is False


def _new_change(voter_id: str, **overrides) -> dict:
    change = {
        "id": str(uuid4()),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "deleted": False,
        "voter_id": voter_id,
        "first_name": "Syncperson",
        "gender": "Male",
    }
    change.update(overrides)
    return change


def test_batch_with_one_voter_id_clash_still_applies_the_rest(ctx):
    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload()).json()
    clash = _new_change(created["voter_id"])
    good_a = _new_change("ZZZ1000003")
    good_b = _new_change("ZZZ1000004")
    same_batch_twin = _new_change("ZZZ1000003")

    body = _push(ctx, [good_a, clash, good_b, same_batch_twin])

    statuses = [item["status"] for item in body["results"]]
    assert statuses == ["applied", "voter_id_conflict", "applied", "voter_id_conflict"]
    assert body["results"][1]["server_record"]["id"] == created["id"]
    assert body["results"][3]["server_record"]["id"] == good_a["id"]
    assert ctx.client.get(f"/api/voters/{good_a['id']}", headers=ctx.auth).status_code == 200
    assert ctx.client.get(f"/api/voters/{good_b['id']}", headers=ctx.auth).status_code == 200
    assert ctx.client.get(f"/api/voters/{clash['id']}", headers=ctx.auth).status_code == 404
    assert ctx.client.get(f"/api/voters/{same_batch_twin['id']}", headers=ctx.auth).status_code == 404


def test_database_unique_clash_rejects_only_that_change(ctx, monkeypatch):
    from app.services import sync as sync_service

    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload()).json()
    real_owner = sync_service._active_voter_id_owner
    calls = {"count": 0}

    def blind_on_first_call(db, voter_id, exclude_id):
        # Simulates a concurrent insert that the pre-check cannot see.
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return real_owner(db, voter_id, exclude_id)

    monkeypatch.setattr(sync_service, "_active_voter_id_owner", blind_on_first_call)
    clash = _new_change(created["voter_id"])
    good = _new_change("ZZZ1000005")

    response = ctx.client.post(
        "/api/sync/push",
        headers=ctx.auth,
        json={"device_id": "lab-phone-1", "changes": [clash, good]},
    )

    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert [item["status"] for item in results] == ["voter_id_conflict", "applied"]
    assert results[0]["server_record"]["id"] == created["id"]
    assert ctx.client.get(f"/api/voters/{good['id']}", headers=ctx.auth).status_code == 200
    assert ctx.client.get(f"/api/voters/{clash['id']}", headers=ctx.auth).status_code == 404
    assert ctx.client.get(f"/api/voters/{created['id']}", headers=ctx.auth).status_code == 200


def test_stale_base_revision_is_a_conflict_not_last_write_wins(ctx):
    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload()).json()
    edited = ctx.client.patch(
        f"/api/voters/{created['id']}", headers=ctx.auth, json={"first_name": "ServerEdit"}
    ).json()
    assert edited["revision"] > created["revision"]
    newer = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()

    stale = _push(
        ctx,
        [_snapshot(edited, updated_at=newer, first_name="DeviceEdit", base_revision=created["revision"])],
    )

    result = stale["results"][0]
    assert result["status"] == "conflict"
    assert result["server_record"]["first_name"] == "ServerEdit"
    current = ctx.client.get(f"/api/voters/{created['id']}", headers=ctx.auth).json()
    assert current["first_name"] == "ServerEdit"
    assert current["revision"] == edited["revision"]

    fresh = _push(
        ctx,
        [_snapshot(edited, updated_at=newer, first_name="DeviceEdit", base_revision=edited["revision"])],
    )
    assert fresh["results"][0]["status"] == "applied"
    assert ctx.client.get(f"/api/voters/{created['id']}", headers=ctx.auth).json()["first_name"] == "DeviceEdit"

    replay = _push(
        ctx,
        [_snapshot(edited, updated_at=newer, first_name="DeviceEdit", base_revision=created["revision"])],
    )
    assert replay["results"][0]["status"] == "applied"
    assert replay["results"][0]["message"] == "Already stored"


def test_stale_base_revision_blocks_a_delete(ctx):
    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload()).json()
    edited = ctx.client.patch(
        f"/api/voters/{created['id']}", headers=ctx.auth, json={"profession": "Farmer"}
    ).json()
    newer = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()

    blocked = _push(
        ctx,
        [{"id": created["id"], "updated_at": newer, "deleted": True, "base_revision": created["revision"]}],
    )

    assert blocked["results"][0]["status"] == "conflict"
    assert ctx.client.get(f"/api/voters/{created['id']}", headers=ctx.auth).status_code == 200
    allowed = _push(
        ctx,
        [{"id": created["id"], "updated_at": newer, "deleted": True, "base_revision": edited["revision"]}],
    )
    assert allowed["results"][0]["status"] == "applied"
    assert ctx.client.get(f"/api/voters/{created['id']}", headers=ctx.auth).status_code == 404


def test_sync_delete_leaves_a_stripped_tombstone(ctx):
    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload()).json()
    newer = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()

    applied = _push(ctx, [{"id": created["id"], "updated_at": newer, "deleted": True}])

    result = applied["results"][0]
    assert result["status"] == "applied"
    tomb = result["server_record"]
    assert tomb["deleted_at"] is not None
    pulled = ctx.client.get("/api/sync/pull", headers=ctx.auth, params={"since": created["revision"]}).json()
    match = next(item for item in pulled["changes"] if item["id"] == created["id"])
    for field in ("mobile", "caste", "party", "surname", "relative_name", "address", "village", "permanent_address", "profession"):
        assert match[field] is None, field
    assert match["first_name"] == ""
    assert "Testperson" not in str(pulled) and "9000000021" not in str(pulled)


def test_voter_id_conflict_delete_tombstone_and_future_clock(ctx):
    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload()).json()
    other_id = str(uuid4())
    newer = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()
    clash = _push(
        ctx,
        [
            {
                "id": other_id,
                "updated_at": newer,
                "deleted": False,
                "voter_id": created["voter_id"],
                "first_name": "Someone",
                "gender": "Male",
            }
        ],
    )
    assert clash["results"][0]["status"] == "voter_id_conflict"
    assert clash["results"][0]["server_record"]["id"] == created["id"]
    assert ctx.client.get(f"/api/voters/{other_id}", headers=ctx.auth).status_code == 404

    removed = ctx.client.delete(f"/api/voters/{created['id']}", headers=ctx.auth)
    assert removed.status_code == 200
    tomb = ctx.client.get(
        "/api/sync/pull",
        headers=ctx.auth,
        params={"since": created["revision"]},
    ).json()["changes"]
    match = next(item for item in tomb if item["id"] == created["id"])
    assert match["deleted_at"] is not None

    future = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    rejected = _push(ctx, [_snapshot(created, updated_at=future, deleted=True)])
    assert rejected["results"][0]["status"] == "validation_error"

    missing = str(uuid4())
    ignored = _push(
        ctx,
        [{"id": missing, "updated_at": datetime.now(timezone.utc).isoformat(), "deleted": True}],
    )
    assert ignored["results"][0]["status"] == "applied"
    assert "ignored" in (ignored["results"][0]["message"] or "")
