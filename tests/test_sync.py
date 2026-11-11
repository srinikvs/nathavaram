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
