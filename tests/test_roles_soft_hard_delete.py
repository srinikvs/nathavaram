"""Roles (user / manager / admin), restorable soft delete, the Deleted voters page and hard delete.

Synthetic data only: fake names and 90000000xx mobiles.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.constants import DATA_FIELDS, USER_EDITABLE_FIELDS
from app.permissions import (
    can_edit_all_fields,
    can_hard_delete,
    can_import,
    can_manage_accounts,
    can_restore,
    can_review_deleted,
    can_soft_delete,
    can_use_master_data,
    permission_flags,
)
from tests.conftest import csrf_from_page, make_user, search, voter_payload

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "role-pass-1234"
MANAGER = "manager.one"
USER = "field.user"
VOTER_SUMMARY_SQL = (
    "SELECT voter_id, first_name, is_local, created_at, updated_at, revision, party, gender, age,"
    " deleted_at FROM voters ORDER BY voter_id"
)


class _Role:
    """Minimal stand-in with the attributes the permission helpers read."""

    def __init__(self, role: str, is_owner: bool = False) -> None:
        self.role = role
        self.is_owner = is_owner


# --- Permission helpers (unit) ------------------------------------------------------------


@pytest.mark.parametrize(
    ("helper", "allowed_roles"),
    [
        (can_soft_delete, {"user", "manager", "admin"}),
        (can_review_deleted, {"manager", "admin"}),
        (can_restore, {"manager", "admin"}),
        (can_hard_delete, {"manager", "admin"}),
        (can_edit_all_fields, {"admin"}),
        (can_import, {"admin"}),
        (can_use_master_data, {"admin"}),
        (can_manage_accounts, {"admin"}),
    ],
)
def test_each_helper_allows_exactly_the_roles_in_the_matrix(helper, allowed_roles):
    for role in ("user", "manager", "admin"):
        assert helper(_Role(role)) is (role in allowed_roles), (helper.__name__, role)


def test_permission_flags_expose_the_same_rules_for_templates():
    flags = permission_flags(_Role("manager"))

    assert flags["review_deleted"] and flags["restore"] and flags["hard_delete"]
    assert flags["soft_delete"] and not flags["edit_all"] and not flags["import"] and not flags["accounts"]


def test_user_editable_fields_match_the_product_list():
    assert USER_EDITABLE_FIELDS == {
        "booth", "ward", "booth_s_no", "ward_s_no", "relative_name", "house_no", "age",
        "street", "mobile", "village", "panchayath", "current_city", "is_local", "party",
    }
    assert "caste" not in USER_EDITABLE_FIELDS


# --- Helpers ----------------------------------------------------------------------------------


def _create(ctx, **overrides) -> dict:
    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload(**overrides))
    assert created.status_code == 201, created.text
    return created.json()


def _get(ctx, voter_id: str, headers=None):
    return ctx.client.get(f"/api/voters/{voter_id}", headers=headers or ctx.auth)


def _roles(ctx) -> dict:
    """Bearer headers for one account per role; the admin is the seeded owner."""
    return {
        "user": make_user(ctx, USER, PASSWORD, role="user"),
        "manager": make_user(ctx, MANAGER, PASSWORD, role="manager"),
        "admin": ctx.auth,
    }


def _web_login(ctx, username: str = "admin", password: str = "admin-pass-123") -> None:
    ctx.client.cookies.clear()
    signed = ctx.client.post(
        "/login", data={"username": username, "password": password, "next": "/voters"}, follow_redirects=False
    )
    assert signed.status_code == 303, signed.text


def _soft_delete(ctx, voter_id: str, headers):
    return ctx.client.delete(f"/api/voters/{voter_id}", headers=headers)


def _deleted_ids(ctx, headers) -> list[str]:
    response = ctx.client.get("/api/voters/deleted", headers=headers)
    assert response.status_code == 200, response.text
    return [item["voter_id"] for item in response.json()["items"]]


def _pulled(ctx) -> list[dict]:
    return ctx.client.get("/api/sync/pull?since=0", headers=ctx.auth).json()["changes"]


def _later() -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()


def _push(ctx, headers, changes) -> list[dict]:
    response = ctx.client.post(
        "/api/sync/push", headers=headers, json={"device_id": "lab-phone-2", "changes": changes}
    )
    assert response.status_code == 200, response.text
    return response.json()["results"]


# --- Soft delete keeps every field -------------------------------------------------------------


@pytest.mark.parametrize("role", ["user", "manager", "admin"])
def test_soft_delete_by_every_role_keeps_every_field_and_hides_the_row(ctx, role):
    headers = _roles(ctx)[role]
    voter = _create(ctx)

    removed = _soft_delete(ctx, voter["voter_id"], headers)

    assert removed.status_code == 200, removed.text
    body = removed.json()
    assert body["deleted_at"] is not None and body["hard_deleted_at"] is None
    for field in DATA_FIELDS:
        assert body[field] == voter[field], field
    assert _get(ctx, voter["voter_id"]).status_code == 404
    assert voter["voter_id"] not in [i["voter_id"] for i in search(ctx).json()["items"]]
    assert voter["voter_id"] not in [i["voter_id"] for i in search(ctx, q="Testperson").json()["items"]]


def test_soft_delete_stays_on_the_sync_pull_with_its_fields(ctx):
    voter = _create(ctx)
    _soft_delete(ctx, voter["voter_id"], ctx.auth)

    row = next(item for item in _pulled(ctx) if item["voter_id"] == voter["voter_id"])

    assert row["deleted_at"] is not None and row["first_name"] == "Testperson"


# --- Deleted voters page and API ----------------------------------------------------------------


def test_deleted_list_is_403_for_user_and_shows_kept_data_for_manager_and_admin(ctx):
    roles = _roles(ctx)
    voter = _create(ctx, village="Hidden Village")
    _soft_delete(ctx, voter["voter_id"], roles["user"])

    assert ctx.client.get("/api/voters/deleted", headers=roles["user"]).status_code == 403
    assert _deleted_ids(ctx, roles["manager"]) == [voter["voter_id"]]
    assert _deleted_ids(ctx, roles["admin"]) == [voter["voter_id"]]


def test_deleted_voters_page_is_manager_and_admin_only_and_shows_the_kept_fields(ctx):
    _roles(ctx)
    voter = _create(ctx, village="Hidden Village")
    _soft_delete(ctx, voter["voter_id"], ctx.auth)

    _web_login(ctx, USER, PASSWORD)
    forbidden = ctx.client.get("/voters/deleted", follow_redirects=False)
    _web_login(ctx, MANAGER, PASSWORD)
    page = ctx.client.get("/voters/deleted")

    assert forbidden.status_code == 403
    assert page.status_code == 200
    assert "<h1>Deleted voters</h1>" in page.text
    assert voter["voter_id"] in page.text and "Hidden Village" in page.text
    assert "Restore" in page.text and "Permanently delete" in page.text


def test_active_voters_page_has_no_hard_delete_and_details_404_for_a_deleted_voter(ctx):
    voter = _create(ctx)
    _web_login(ctx)
    assert "Permanently" not in ctx.client.get("/voters").text
    assert "hard-delete" not in ctx.client.get("/voters").text
    _soft_delete(ctx, voter["voter_id"], ctx.auth)

    details = ctx.client.get(f"/voters/{voter['voter_id']}")

    assert details.status_code == 404
    assert "Permanently" not in details.text


# --- Restore -------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["manager", "admin"])
def test_restore_brings_back_the_exact_row(ctx, role):
    roles = _roles(ctx)
    voter = _create(ctx, party="YSRCP", village="Restore Village")
    removed = _soft_delete(ctx, voter["voter_id"], roles["user"]).json()

    restored = ctx.client.post(f"/api/voters/{voter['voter_id']}/restore", headers=roles[role])

    assert restored.status_code == 200, restored.text
    body = restored.json()
    assert body["deleted_at"] is None
    for field in DATA_FIELDS:
        assert body[field] == voter[field], field
    assert body["revision"] > removed["revision"]
    assert _get(ctx, voter["voter_id"]).status_code == 200
    assert voter["voter_id"] in [i["voter_id"] for i in search(ctx).json()["items"]]


def test_user_cannot_restore_and_gets_403(ctx):
    roles = _roles(ctx)
    voter = _create(ctx)
    _soft_delete(ctx, voter["voter_id"], roles["user"])

    denied = ctx.client.post(f"/api/voters/{voter['voter_id']}/restore", headers=roles["user"])

    assert denied.status_code == 403
    assert _get(ctx, voter["voter_id"]).status_code == 404


def test_restore_of_an_active_voter_is_a_conflict(ctx):
    voter = _create(ctx)

    response = ctx.client.post(f"/api/voters/{voter['voter_id']}/restore", headers=ctx.auth)

    assert response.status_code == 409


def test_web_restore_returns_the_voter_to_the_active_list(ctx):
    voter = _create(ctx)
    _soft_delete(ctx, voter["voter_id"], ctx.auth)
    _web_login(ctx)
    token = csrf_from_page(ctx.client.get("/voters/deleted").text)

    response = ctx.client.post(
        f"/voters/{voter['voter_id']}/restore", data={"csrf_token": token}, follow_redirects=False
    )

    assert response.status_code == 303
    assert _get(ctx, voter["voter_id"]).status_code == 200


# --- Hard delete ---------------------------------------------------------------------------------


def test_hard_delete_strips_personal_data_and_keeps_a_sync_marker(ctx):
    roles = _roles(ctx)
    voter = _create(ctx)
    _soft_delete(ctx, voter["voter_id"], roles["user"])

    hard = ctx.client.post(f"/api/voters/{voter['voter_id']}/hard-delete", headers=roles["manager"])

    assert hard.status_code == 200, hard.text
    body = hard.json()
    assert body["hard_deleted_at"] is not None and body["deleted_at"] is not None
    assert body["first_name"] == "" and body["is_local"] is False
    for field in DATA_FIELDS:
        if field not in {"voter_id", "first_name", "is_local"}:
            assert body[field] is None, field
    row = next(item for item in _pulled(ctx) if item["voter_id"] == voter["voter_id"])
    assert row["hard_deleted_at"] is not None and row["mobile"] is None
    assert "Testperson" not in str(row)


def test_hard_deleted_voter_cannot_be_restored(ctx):
    voter = _create(ctx)
    _soft_delete(ctx, voter["voter_id"], ctx.auth)
    ctx.client.post(f"/api/voters/{voter['voter_id']}/hard-delete", headers=ctx.auth)

    restored = ctx.client.post(f"/api/voters/{voter['voter_id']}/restore", headers=ctx.auth)

    assert restored.status_code == 409
    assert "cannot be restored" in restored.json()["detail"]
    assert voter["voter_id"] not in _deleted_ids(ctx, ctx.auth)


def test_hard_delete_requires_a_soft_deleted_voter(ctx):
    voter = _create(ctx)

    response = ctx.client.post(f"/api/voters/{voter['voter_id']}/hard-delete", headers=ctx.auth)

    assert response.status_code == 409
    assert _get(ctx, voter["voter_id"]).status_code == 200


def test_user_gets_403_on_hard_delete(ctx):
    roles = _roles(ctx)
    voter = _create(ctx)
    _soft_delete(ctx, voter["voter_id"], roles["user"])

    response = ctx.client.post(f"/api/voters/{voter['voter_id']}/hard-delete", headers=roles["user"])

    assert response.status_code == 403


def test_web_hard_delete_is_confirm_gated_and_only_on_the_deleted_page(ctx):
    voter = _create(ctx)
    _soft_delete(ctx, voter["voter_id"], ctx.auth)
    _web_login(ctx)
    token = csrf_from_page(ctx.client.get("/voters/deleted").text)
    confirm_page = ctx.client.get(f"/voters/{voter['voter_id']}/hard-delete")

    unconfirmed = ctx.client.post(
        f"/voters/{voter['voter_id']}/hard-delete", data={"csrf_token": token}, follow_redirects=False
    )

    assert "Permanently delete" in confirm_page.text
    assert unconfirmed.status_code == 303 and unconfirmed.headers["location"].endswith("/hard-delete")
    row = next(item for item in _pulled(ctx) if item["voter_id"] == voter["voter_id"])
    assert row["hard_deleted_at"] is None and row["first_name"] == "Testperson"


def test_bulk_hard_delete_needs_confirm_and_refuses_a_batch_with_an_active_voter(ctx):
    kept = _create(ctx)
    gone = _create(ctx, voter_id="ZZZ1000002", mobile="9000000022")
    _soft_delete(ctx, gone["voter_id"], ctx.auth)
    ids = [gone["voter_id"], kept["voter_id"]]

    without_confirm = ctx.client.post("/api/voters/bulk-hard-delete", headers=ctx.auth, json={"voter_ids": ids})
    mixed = ctx.client.post(
        "/api/voters/bulk-hard-delete", headers=ctx.auth, json={"voter_ids": ids, "confirm": True}
    )

    assert without_confirm.status_code == 400
    assert mixed.status_code == 409  # a batch that includes an active voter is refused as a whole
    assert _get(ctx, kept["voter_id"]).status_code == 200  # nothing applied to the batch
    assert _get(ctx, gone["voter_id"]).status_code == 404


def test_bulk_hard_delete_applies_to_soft_deleted_voters_when_confirmed(ctx):
    first = _create(ctx)
    second = _create(ctx, voter_id="ZZZ1000002", mobile="9000000022")
    for voter in (first, second):
        _soft_delete(ctx, voter["voter_id"], ctx.auth)

    response = ctx.client.post(
        "/api/voters/bulk-hard-delete",
        headers=ctx.auth,
        json={"voter_ids": [first["voter_id"], second["voter_id"]], "confirm": True},
    )

    assert response.status_code == 200, response.text
    assert response.json()["count"] == 2
    assert _deleted_ids(ctx, ctx.auth) == []


def test_bulk_hard_delete_is_403_for_user(ctx):
    roles = _roles(ctx)
    voter = _create(ctx)
    _soft_delete(ctx, voter["voter_id"], roles["user"])

    response = ctx.client.post(
        "/api/voters/bulk-hard-delete",
        headers=roles["user"],
        json={"voter_ids": [voter["voter_id"]], "confirm": True},
    )

    assert response.status_code == 403


# --- Bulk delete on the active list is soft delete -------------------------------------------------


@pytest.mark.parametrize("role", ["user", "manager", "admin"])
def test_bulk_delete_is_soft_for_every_role_and_needs_confirm(ctx, role):
    headers = _roles(ctx)[role]
    voter = _create(ctx)

    no_confirm = ctx.client.post("/api/voters/bulk-delete", headers=headers, json={"voter_ids": [voter["voter_id"]]})
    done = ctx.client.post(
        "/api/voters/bulk-delete", headers=headers, json={"voter_ids": [voter["voter_id"]], "confirm": True}
    )

    assert no_confirm.status_code == 400
    assert done.status_code == 200, done.text
    assert voter["voter_id"] in _deleted_ids(ctx, ctx.auth)
    assert _get(ctx, voter["voter_id"]).status_code == 404


# --- Bulk edit by role -------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["user", "manager"])
def test_user_and_manager_bulk_edit_only_user_editable_fields(ctx, role):
    headers = _roles(ctx)[role]
    voter = _create(ctx)

    allowed = ctx.client.post(
        "/api/voters/bulk-edit", headers=headers, json={"voter_ids": [voter["voter_id"]], "values": {"party": "BJP"}}
    )
    blocked = ctx.client.post(
        "/api/voters/bulk-edit",
        headers=headers,
        json={"voter_ids": [voter["voter_id"]], "values": {"first_name": "Changedname", "party": "INC"}},
    )

    assert allowed.status_code == 200, allowed.text
    assert blocked.status_code == 403
    assert "cannot change" in blocked.json()["detail"]
    assert _get(ctx, voter["voter_id"]).json()["party"] == "BJP"


def test_admin_bulk_edit_can_change_admin_only_fields_but_never_voter_id(ctx):
    voter = _create(ctx)

    changed = ctx.client.post(
        "/api/voters/bulk-edit",
        headers=ctx.auth,
        json={"voter_ids": [voter["voter_id"]], "values": {"caste": "SampleGroupZ", "gender": "Female"}},
    )
    refused = ctx.client.post(
        "/api/voters/bulk-edit",
        headers=ctx.auth,
        json={"voter_ids": [voter["voter_id"]], "values": {"voter_id": "ZZZ9999999"}},
    )

    assert changed.status_code == 200, changed.text
    stored = _get(ctx, voter["voter_id"]).json()
    assert (stored["caste"], stored["gender"]) == ("SampleGroupZ", "Female")
    assert refused.status_code == 422


# --- Admin-only areas for user and manager -------------------------------------------------------------


@pytest.mark.parametrize("role", ["user", "manager"])
def test_import_export_master_data_and_users_are_403_for_user_and_manager(ctx, role):
    headers = _roles(ctx)[role]
    csv_text = "VoterId,FirstName\nZZZ1000009,Fakeimport\n"

    imported = ctx.client.post(
        "/api/voters/import.csv", headers=headers, files={"file": ("v.csv", csv_text.encode(), "text/csv")}
    )

    assert imported.status_code == 403
    assert ctx.client.post("/api/voters/export.csv", headers=headers, json={}).status_code == 403
    assert ctx.client.get("/api/master-data/tabs", headers=headers).status_code == 403
    assert ctx.client.get("/api/users", headers=headers).status_code == 403
    created = ctx.client.post(
        "/api/users", headers=headers, json={"username": "nope.user", "password": PASSWORD, "role": "user"}
    )
    assert created.status_code == 403


def test_manager_web_pages_for_admin_only_areas_are_403(ctx):
    _roles(ctx)
    _web_login(ctx, MANAGER, PASSWORD)

    for path in ("/import", "/master-data", "/users"):
        assert ctx.client.get(path, follow_redirects=False).status_code == 403, path


def test_non_owner_admin_manages_accounts_but_not_the_owner(ctx):
    headers = make_user(ctx, "second.admin", PASSWORD, role="admin")
    owner = next(u for u in ctx.client.get("/api/users", headers=ctx.auth).json() if u["username"] == "admin")

    demote = ctx.client.patch(f"/api/users/{owner['id']}", headers=headers, json={"role": "user"})
    deactivate = ctx.client.patch(f"/api/users/{owner['id']}", headers=headers, json={"is_active": False})
    reset = ctx.client.post(
        f"/api/users/{owner['id']}/reset-password", headers=headers, json={"new_password": PASSWORD + "x"}
    )
    delete = ctx.client.delete(f"/api/users/{owner['id']}", headers=headers)

    assert demote.status_code == 403 and deactivate.status_code == 403
    assert reset.status_code == 403
    assert delete.status_code == 400  # the owner account can never be deleted
    assert ctx.client.get("/api/users", headers=headers).status_code == 200


def test_owner_account_must_stay_admin(ctx):
    owner = next(u for u in ctx.client.get("/api/users", headers=ctx.auth).json() if u["username"] == "admin")

    response = ctx.client.patch(f"/api/users/{owner['id']}", headers=ctx.auth, json={"role": "manager"})

    assert response.status_code == 400
    assert "must stay admin" in response.json()["detail"]


def test_user_accounts_accept_the_manager_role(ctx):
    created = ctx.client.post(
        "/api/users", headers=ctx.auth, json={"username": "new.manager", "password": PASSWORD, "role": "manager"}
    )

    assert created.status_code == 201, created.text
    assert created.json()["role"] == "manager"


def test_users_form_offers_user_manager_and_admin(ctx):
    _web_login(ctx)

    page = ctx.client.get("/users").text

    for role in ("user", "manager", "admin"):
        assert f'<option value="{role}"' in page, role


# --- Sync ----------------------------------------------------------------------------------------------


def test_sync_user_soft_delete_applies_and_keeps_fields(ctx):
    roles = _roles(ctx)
    voter = _create(ctx)

    results = _push(ctx, roles["user"], [{"voter_id": voter["voter_id"], "updated_at": _later(), "deleted": True}])

    assert results[0]["status"] == "applied", results
    assert results[0]["server_record"]["first_name"] == "Testperson"


def test_sync_user_cannot_restore_a_soft_deleted_voter(ctx):
    roles = _roles(ctx)
    voter = _create(ctx)
    _soft_delete(ctx, voter["voter_id"], ctx.auth)
    snapshot = {field: voter[field] for field in DATA_FIELDS}

    results = _push(ctx, roles["user"], [{**snapshot, "updated_at": _later(), "deleted": False}])

    assert results[0]["status"] == "forbidden", results
    assert "restore" in results[0]["message"]
    assert _get(ctx, voter["voter_id"]).status_code == 404


def test_sync_manager_can_restore_a_soft_deleted_voter(ctx):
    roles = _roles(ctx)
    voter = _create(ctx)
    _soft_delete(ctx, voter["voter_id"], ctx.auth)
    snapshot = {field: voter[field] for field in DATA_FIELDS}

    results = _push(ctx, roles["manager"], [{**snapshot, "updated_at": _later(), "deleted": False}])

    assert results[0]["status"] == "applied", results
    assert _get(ctx, voter["voter_id"]).status_code == 200


def test_sync_change_after_hard_delete_creates_a_new_record_not_a_restore(ctx):
    voter = _create(ctx)
    _soft_delete(ctx, voter["voter_id"], ctx.auth)
    ctx.client.post(f"/api/voters/{voter['voter_id']}/hard-delete", headers=ctx.auth)
    snapshot = {field: voter[field] for field in DATA_FIELDS}

    results = _push(ctx, ctx.auth, [{**snapshot, "updated_at": _later(), "deleted": False}])

    assert results[0]["status"] == "applied", results
    assert results[0]["server_record"]["hard_deleted_at"] is None
    assert results[0]["server_record"]["first_name"] == "Testperson"


def test_sync_field_forbidden_message_lists_the_new_editable_set(ctx):
    roles = _roles(ctx)
    voter = _create(ctx)
    snapshot = {field: voter[field] for field in DATA_FIELDS}

    results = _push(ctx, roles["user"], [{**snapshot, "gender": "Female", "updated_at": _later()}])

    assert results[0]["status"] == "forbidden"
    assert "Gender" in results[0]["message"]
    allowed = results[0]["message"].split("may edit only ", 1)[1]
    assert "Party" in allowed and "Booth" in allowed and "Age" in allowed
    assert "Caste" not in allowed and "FirstName" not in allowed


# --- Migration 0010 ------------------------------------------------------------------------------


def _alembic(db_file: Path) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", "sqlite:///" + db_file.as_posix())
    return cfg


def test_migration_0010_keeps_existing_roles_and_voter_rows_and_allows_manager(tmp_path):
    db_file = tmp_path / "roles.db"
    cfg = _alembic(db_file)
    command.upgrade(cfg, "0009_add_gender")
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, role, is_active, must_change_password,"
            " is_owner, token_version, created_at, updated_at)"
            " VALUES ('u-admin', 'fake.admin', 'hash-a', 'admin', 1, 0, 1, 0,"
            " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'),"
            " ('u-user', 'fake.user', 'hash-u', 'user', 1, 1, 0, 0,"
            " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO voters (voter_id, first_name, is_local, created_at, updated_at, revision, party)"
            " VALUES ('ZZZ8200001', 'Fakeperson', 1, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 1, 'TDP')"
        )
        voters_before = conn.execute(VOTER_SUMMARY_SQL).fetchall()

    command.upgrade(cfg, "head")

    with sqlite3.connect(db_file) as conn:
        roles_by_name = dict(conn.execute("SELECT username, role FROM users").fetchall())
        conn.execute(
            "INSERT INTO users (id, username, password_hash, role, is_active, must_change_password,"
            " is_owner, token_version, created_at, updated_at)"
            " VALUES ('u-manager', 'fake.manager', 'hash-m', 'manager', 1, 1, 0, 0,"
            " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO users (id, username, password_hash, role, is_active, must_change_password,"
                " is_owner, token_version, created_at, updated_at)"
                " VALUES ('u-bad', 'fake.bad', 'hash-b', 'robot', 1, 1, 0, 0,"
                " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
            )
        voters_after = conn.execute(VOTER_SUMMARY_SQL).fetchall()
        columns = {row[1] for row in conn.execute("PRAGMA table_info(voters)")}
        manager_role = conn.execute("SELECT role FROM users WHERE username = 'fake.manager'").fetchone()

    assert roles_by_name == {"fake.admin": "admin", "fake.user": "user"}
    assert manager_role == ("manager",)
    assert voters_after == voters_before  # the migration does not touch voter rows
    assert "hard_deleted_at" in columns


def test_migration_0010_downgrade_refuses_while_a_manager_exists(tmp_path):
    db_file = tmp_path / "down.db"
    cfg = _alembic(db_file)
    command.upgrade(cfg, "head")
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, role, is_active, must_change_password,"
            " is_owner, token_version, created_at, updated_at)"
            " VALUES ('u-m', 'fake.m', 'h', 'manager', 1, 0, 0, 0,"
            " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
        )

    with pytest.raises(RuntimeError, match="Downgrade refused"):
        command.downgrade(cfg, "0009_add_gender")
