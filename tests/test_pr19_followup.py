"""PR #19 follow-up: 403 wording, owner-only admin accounts, pre-PR-19 tombstones.

Synthetic names, VoterIds and mobiles only.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

import pytest

from app.constants import CSV_HEADERS, DATA_FIELDS
from app.services.users import ADMIN_ACCOUNT_MESSAGE, ADMIN_ROLE_MESSAGE
from app.services.voters import UNRECOVERABLE_RESTORE_MESSAGE, UNRECOVERABLE_VOTER_ID_MESSAGE
from tests.conftest import csrf_from_page, make_user, voter_payload

PW = "followup-pass-01"
PW2 = "followup-pass-02"
USER = "qa_fu_user"
MANAGER = "qa_fu_manager"
ADMIN2 = "qa_fu_admin2"
ADMIN3 = "qa_fu_admin3"
ADMIN_ONLY_TEXT = "That page is for administrators."
MANAGER_TEXT = "That page is for managers and administrators."
DELETED_ROW_FIELDS = ("deleted_at", "hard_deleted_at", "revision", "updated_at")


# --- helpers ----------------------------------------------------------------------------------


def _accounts(ctx) -> dict:
    """Create one account per non-owner role and return their Bearer headers by key."""
    return {
        "user": make_user(ctx, USER, PW, role="user"),
        "manager": make_user(ctx, MANAGER, PW, role="manager"),
        "admin2": make_user(ctx, ADMIN2, PW, role="admin"),
        "admin3": make_user(ctx, ADMIN3, PW, role="admin"),
    }


def _bearer(ctx, username: str) -> dict:
    """Bearer headers for an account that already exists."""
    ctx.client.cookies.clear()
    login = ctx.client.post("/api/auth/login", json={"username": username, "password": PW})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _web_as(ctx, username: str, password: str = PW) -> None:
    ctx.client.cookies.clear()
    signed = ctx.client.post(
        "/login", data={"username": username, "password": password}, follow_redirects=False
    )
    assert signed.status_code == 303, signed.text


def _web_csrf(ctx, path: str) -> str:
    return csrf_from_page(ctx.client.get(path).text)


def _id_of(ctx, username: str) -> str:
    users = ctx.client.get("/api/users", headers=ctx.auth).json()
    return next(item["id"] for item in users if item["username"] == username)


def _roles_by_username(ctx) -> dict:
    return {item["username"]: item["role"] for item in ctx.client.get("/api/users", headers=ctx.auth).json()}


def _create(ctx, **overrides) -> dict:
    created = ctx.client.post("/api/voters", headers=ctx.auth, json=voter_payload(**overrides))
    assert created.status_code == 201, created.text
    return created.json()


def _soft_delete(ctx, voter_id: str) -> None:
    deleted = ctx.client.delete(f"/api/voters/{voter_id}", headers=ctx.auth)
    assert deleted.status_code == 200, deleted.text


def _make_pre_pr19_tombstone(ctx, voter_id: str) -> None:
    """Create, soft delete, then strip the fields the way strip_to_tombstone did before PR #19."""
    from app import database
    from app.models import Voter
    from app.services.voters import strip_to_tombstone

    _create(ctx, voter_id=voter_id)
    _soft_delete(ctx, voter_id)
    db = database.SessionLocal()
    try:
        strip_to_tombstone(db.get(Voter, voter_id))
        db.commit()
    finally:
        db.close()


def _row(voter_id: str) -> dict:
    """Stored values of one row, read straight from the database."""
    from app import database
    from app.models import Voter

    db = database.SessionLocal()
    try:
        voter = db.get(Voter, voter_id)
        return {field: getattr(voter, field) for field in (*DATA_FIELDS, *DELETED_ROW_FIELDS)}
    finally:
        db.close()


def _is_unrecoverable(voter_id: str) -> bool:
    from app import database
    from app.models import Voter
    from app.services.voters import is_unrecoverable_soft_delete

    db = database.SessionLocal()
    try:
        return is_unrecoverable_soft_delete(db.get(Voter, voter_id))
    finally:
        db.close()


def _later() -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()


# --- 1. 403 wording ---------------------------------------------------------------------------


def test_user_on_deleted_voters_page_gets_managers_and_administrators_wording(ctx):
    _accounts(ctx)
    _web_as(ctx, USER)

    page = ctx.client.get("/voters/deleted")

    assert page.status_code == 403
    assert MANAGER_TEXT in page.text
    assert ADMIN_ONLY_TEXT not in page.text


@pytest.mark.parametrize("path", ["/users", "/import", "/master-data"])
@pytest.mark.parametrize("username", [USER, MANAGER])
def test_user_and_manager_on_admin_only_pages_still_see_administrators_wording(ctx, path, username):
    _accounts(ctx)
    _web_as(ctx, username)

    page = ctx.client.get(path)

    assert page.status_code == 403
    assert ADMIN_ONLY_TEXT in page.text
    assert MANAGER_TEXT not in page.text


def test_manager_can_open_deleted_voters_and_sees_no_forbidden_page(ctx):
    _accounts(ctx)
    _web_as(ctx, MANAGER)

    assert ctx.client.get("/voters/deleted").status_code == 200


def test_api_403_details_name_the_role_that_is_needed(ctx):
    headers = _accounts(ctx)

    user_on_deleted = ctx.client.get("/api/voters/deleted", headers=headers["user"])
    manager_on_users = ctx.client.get("/api/users", headers=headers["manager"])

    assert user_on_deleted.json()["detail"] == "Manager or admin role required"
    assert manager_on_users.json()["detail"] == "Admin role required"


# --- 2. owner-only admin accounts -------------------------------------------------------------


def test_non_owner_admin_cannot_reset_deactivate_or_delete_another_admin_api(ctx):
    headers = _accounts(ctx)
    target = _id_of(ctx, ADMIN3)

    reset = ctx.client.post(
        f"/api/users/{target}/reset-password", headers=headers["admin2"], json={"new_password": PW2}
    )
    deactivate = ctx.client.patch(f"/api/users/{target}", headers=headers["admin2"], json={"is_active": False})
    delete = ctx.client.delete(f"/api/users/{target}", headers=headers["admin2"])

    assert reset.status_code == 403 and reset.json()["detail"] == ADMIN_ACCOUNT_MESSAGE
    assert deactivate.status_code == 403 and deactivate.json()["detail"] == ADMIN_ACCOUNT_MESSAGE
    assert delete.status_code == 403 and delete.json()["detail"] == ADMIN_ACCOUNT_MESSAGE
    assert _roles_by_username(ctx)[ADMIN3] == "admin"
    still = next(u for u in ctx.client.get("/api/users", headers=ctx.auth).json() if u["id"] == target)
    assert still["is_active"] is True


def test_non_owner_admin_cannot_demote_or_change_password_of_another_admin_api(ctx):
    headers = _accounts(ctx)
    target = _id_of(ctx, ADMIN3)

    demote = ctx.client.patch(f"/api/users/{target}", headers=headers["admin2"], json={"role": "user"})
    password = ctx.client.patch(f"/api/users/{target}", headers=headers["admin2"], json={"password": PW2})

    assert demote.status_code == 403 and demote.json()["detail"] == ADMIN_ACCOUNT_MESSAGE
    assert password.status_code == 403 and password.json()["detail"] == ADMIN_ACCOUNT_MESSAGE
    assert _roles_by_username(ctx)[ADMIN3] == "admin"


def test_non_owner_admin_cannot_create_an_admin_or_promote_to_admin_api(ctx):
    headers = _accounts(ctx)

    create = ctx.client.post(
        "/api/users",
        headers=headers["admin2"],
        json={"username": "qa_fu_new_admin", "password": PW, "role": "admin"},
    )
    promote_user = ctx.client.patch(
        f"/api/users/{_id_of(ctx, USER)}", headers=headers["admin2"], json={"role": "admin"}
    )
    promote_manager = ctx.client.patch(
        f"/api/users/{_id_of(ctx, MANAGER)}", headers=headers["admin2"], json={"role": "admin"}
    )

    assert create.status_code == 403 and create.json()["detail"] == ADMIN_ROLE_MESSAGE
    assert promote_user.status_code == 403 and promote_user.json()["detail"] == ADMIN_ROLE_MESSAGE
    assert promote_manager.status_code == 403 and promote_manager.json()["detail"] == ADMIN_ROLE_MESSAGE
    roles = _roles_by_username(ctx)
    assert "qa_fu_new_admin" not in roles
    assert roles[USER] == "user" and roles[MANAGER] == "manager"


def test_non_owner_admin_still_manages_user_and_manager_accounts_api(ctx):
    headers = _accounts(ctx)

    created = ctx.client.post(
        "/api/users",
        headers=headers["admin2"],
        json={"username": "qa_fu_new_mgr", "password": PW, "role": "manager"},
    )
    demoted = ctx.client.patch(
        f"/api/users/{_id_of(ctx, MANAGER)}", headers=headers["admin2"], json={"role": "user"}
    )
    reset = ctx.client.post(
        f"/api/users/{_id_of(ctx, USER)}/reset-password", headers=headers["admin2"], json={"new_password": PW2}
    )
    deleted = ctx.client.delete(f"/api/users/{created.json()['id']}", headers=headers["admin2"])

    assert created.status_code == 201, created.text
    assert demoted.status_code == 200, demoted.text
    assert reset.status_code == 200, reset.text
    assert deleted.status_code == 200, deleted.text


def test_owner_still_manages_non_owner_admins_api(ctx):
    _accounts(ctx)
    target = _id_of(ctx, ADMIN3)

    reset = ctx.client.post(f"/api/users/{target}/reset-password", headers=ctx.auth, json={"new_password": PW2})
    demoted = ctx.client.patch(f"/api/users/{target}", headers=ctx.auth, json={"role": "manager"})
    repromoted = ctx.client.patch(f"/api/users/{target}", headers=ctx.auth, json={"role": "admin"})
    promoted_user = ctx.client.patch(f"/api/users/{_id_of(ctx, USER)}", headers=ctx.auth, json={"role": "admin"})
    created_admin = ctx.client.post(
        "/api/users", headers=ctx.auth, json={"username": "qa_fu_owner_admin", "password": PW, "role": "admin"}
    )
    deleted = ctx.client.delete(f"/api/users/{target}", headers=ctx.auth)

    assert reset.status_code == 200, reset.text
    assert demoted.status_code == 200 and repromoted.status_code == 200
    assert promoted_user.status_code == 200 and promoted_user.json()["role"] == "admin"
    assert created_admin.status_code == 201, created_admin.text
    assert deleted.status_code == 200, deleted.text


def test_web_non_owner_admin_is_refused_on_admin_accounts_with_csrf(ctx):
    _accounts(ctx)
    target = _id_of(ctx, ADMIN3)
    user_id = _id_of(ctx, USER)
    _web_as(ctx, ADMIN2)
    token = _web_csrf(ctx, "/users")

    reset = ctx.client.post(f"/users/{target}/reset-password", data={"new_password": PW2, "csrf_token": token})
    deleted = ctx.client.post(f"/users/{target}/delete", data={"confirm": "yes", "csrf_token": token})
    created = ctx.client.post(
        "/users",
        data={"username": "qa_fu_web_admin", "password": PW, "role": "admin", "csrf_token": token},
    )
    allowed = ctx.client.post(
        f"/users/{user_id}/reset-password",
        data={"new_password": PW2, "csrf_token": token},
        follow_redirects=False,
    )

    assert reset.status_code == 403 and ADMIN_ACCOUNT_MESSAGE in reset.text
    assert deleted.status_code == 403 and ADMIN_ACCOUNT_MESSAGE in deleted.text
    assert created.status_code == 403 and ADMIN_ROLE_MESSAGE in created.text
    assert allowed.status_code == 303


def test_web_users_page_hides_admin_controls_from_non_owner_admin(ctx):
    _accounts(ctx)
    target = _id_of(ctx, ADMIN3)
    user_id = _id_of(ctx, USER)
    _web_as(ctx, ADMIN2)

    page = ctx.client.get("/users").text

    assert f"/users/{target}/reset-password" not in page
    assert f"/users/{target}/delete" not in page
    assert f"/users/{user_id}/reset-password" in page
    assert f"/users/{user_id}/delete" in page
    assert '<option value="admin"' not in page


def test_web_users_page_shows_admin_controls_to_owner(ctx):
    _accounts(ctx)
    target = _id_of(ctx, ADMIN3)
    _web_as(ctx, "admin", "admin-pass-123")

    page = ctx.client.get("/users").text

    assert f"/users/{target}/reset-password" in page
    assert f"/users/{target}/delete" in page
    assert '<option value="admin"' in page


# --- 3. pre-PR-19 tombstones ------------------------------------------------------------------


def test_helper_matches_only_the_stripped_shape(ctx):
    _create(ctx, voter_id="ZZZ2000002")
    _soft_delete(ctx, "ZZZ2000002")
    _make_pre_pr19_tombstone(ctx, "ZZZ2000003")
    _create(ctx, voter_id="ZZZ2000004")

    assert _is_unrecoverable("ZZZ2000002") is False
    assert _is_unrecoverable("ZZZ2000003") is True
    assert _is_unrecoverable("ZZZ2000004") is False


def test_helper_ignores_a_hard_deleted_row(ctx):
    _make_pre_pr19_tombstone(ctx, "ZZZ2000005")

    hard = ctx.client.post("/api/voters/ZZZ2000005/hard-delete", headers=ctx.auth)

    assert hard.status_code == 200, hard.text
    assert _is_unrecoverable("ZZZ2000005") is False


def test_deleted_page_marks_the_tombstone_and_offers_only_hard_delete(ctx):
    make_user(ctx, MANAGER, PW, role="manager")
    _create(ctx, voter_id="ZZZ2000006")
    _soft_delete(ctx, "ZZZ2000006")
    _make_pre_pr19_tombstone(ctx, "ZZZ2000007")
    _web_as(ctx, MANAGER)

    page = ctx.client.get("/voters/deleted").text

    assert "Details not recoverable" in page
    assert 'action="/voters/ZZZ2000006/restore"' in page
    assert 'action="/voters/ZZZ2000007/restore"' not in page
    assert 'href="/voters/ZZZ2000007/hard-delete"' in page


def test_deleted_api_exposes_details_recoverable(ctx):
    _create(ctx, voter_id="ZZZ2000008")
    _soft_delete(ctx, "ZZZ2000008")
    _make_pre_pr19_tombstone(ctx, "ZZZ2000009")

    items = ctx.client.get("/api/voters/deleted", headers=ctx.auth).json()["items"]
    flags = {item["voter_id"]: item["details_recoverable"] for item in items}

    assert flags == {"ZZZ2000008": True, "ZZZ2000009": False}


def test_restore_of_a_tombstone_is_409_for_api_and_web_and_changes_nothing(ctx):
    make_user(ctx, MANAGER, PW, role="manager")
    _make_pre_pr19_tombstone(ctx, "ZZZ2000010")
    before = _row("ZZZ2000010")

    api = ctx.client.post("/api/voters/ZZZ2000010/restore", headers=ctx.auth)
    _web_as(ctx, MANAGER)
    token = _web_csrf(ctx, "/voters/deleted")
    web = ctx.client.post("/voters/ZZZ2000010/restore", data={"csrf_token": token}, follow_redirects=False)

    assert api.status_code == 409 and api.json()["detail"] == UNRECOVERABLE_RESTORE_MESSAGE
    assert web.status_code == 409 and "cannot be restored" in web.text
    assert _row("ZZZ2000010") == before


def test_post_pr19_soft_delete_still_restores_every_field_exactly(ctx):
    headers = _accounts(ctx)
    created = _create(ctx, voter_id="ZZZ2000011")
    _soft_delete(ctx, "ZZZ2000011")
    before = {field: created[field] for field in DATA_FIELDS}

    restored = ctx.client.post("/api/voters/ZZZ2000011/restore", headers=headers["manager"])

    assert restored.status_code == 200, restored.text
    assert {field: restored.json()[field] for field in DATA_FIELDS} == before
    assert restored.json()["deleted_at"] is None


def test_user_cannot_restore_either_kind_of_deleted_voter(ctx):
    headers = _accounts(ctx)
    _create(ctx, voter_id="ZZZ2000012")
    _soft_delete(ctx, "ZZZ2000012")
    _make_pre_pr19_tombstone(ctx, "ZZZ2000013")

    kept = ctx.client.post("/api/voters/ZZZ2000012/restore", headers=headers["user"])
    tomb = ctx.client.post("/api/voters/ZZZ2000013/restore", headers=headers["user"])

    assert kept.status_code == 403 and tomb.status_code == 403
    assert _row("ZZZ2000013")["deleted_at"] is not None


@pytest.mark.parametrize("username", ["user", "manager", "admin"])
def test_create_with_a_tombstone_voter_id_is_409_for_every_role(ctx, username):
    headers = _accounts(ctx)
    _make_pre_pr19_tombstone(ctx, "ZZZ2000014")
    before = _row("ZZZ2000014")
    caller = ctx.auth if username == "admin" else headers[username]

    response = ctx.client.post(
        "/api/voters", headers=caller, json=voter_payload(voter_id="ZZZ2000014", first_name="Fresh")
    )

    assert response.status_code == 409
    assert response.json()["detail"] == UNRECOVERABLE_VOTER_ID_MESSAGE
    assert _row("ZZZ2000014") == before


def test_web_create_with_a_tombstone_voter_id_is_refused_and_changes_nothing(ctx):
    _make_pre_pr19_tombstone(ctx, "ZZZ2000015")
    before = _row("ZZZ2000015")
    _web_as(ctx, "admin", "admin-pass-123")
    token = _web_csrf(ctx, "/voters/new")

    response = ctx.client.post(
        "/voters/new",
        data={"voter_id": "ZZZ2000015", "first_name": "Webfresh", "mobile": "9000000015", "csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert "details were removed" in response.text
    assert _row("ZZZ2000015") == before


def test_sync_restore_of_a_tombstone_is_refused_for_every_role(ctx):
    headers = _accounts(ctx)
    _make_pre_pr19_tombstone(ctx, "ZZZ2000016")
    before = _row("ZZZ2000016")
    change = {**voter_payload(voter_id="ZZZ2000016", first_name="Synced"), "updated_at": _later(), "deleted": False}

    for role in ("user", "manager", "admin2"):
        response = ctx.client.post(
            "/api/sync/push", headers=headers[role], json={"device_id": "lab-phone-fu", "changes": [change]}
        )
        result = response.json()["results"][0]
        assert result["status"] == "forbidden", (role, result)
        assert result["message"] == UNRECOVERABLE_RESTORE_MESSAGE
    assert _row("ZZZ2000016") == before


def test_import_of_a_tombstone_voter_id_is_a_conflict_and_changes_nothing(ctx):
    _make_pre_pr19_tombstone(ctx, "ZZZ2000017")
    before = _row("ZZZ2000017")
    row = voter_payload(voter_id="ZZZ2000017", first_name="Imported")
    row["is_local"] = "Non-Local"
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_HEADERS)
    writer.writerow(["" if row.get(field) is None else str(row[field]) for field in DATA_FIELDS])

    response = ctx.client.post(
        "/api/voters/import.csv",
        headers=ctx.auth,
        files={"file": ("voters.csv", buffer.getvalue().encode("utf-8"), "text/csv")},
    )

    report = response.json()
    assert report["imported"] == 0, report
    assert "permanently delete it first" in report["errors"][0]["message"]
    assert _row("ZZZ2000017") == before


def test_hard_delete_stays_available_for_a_tombstone_and_frees_the_voter_id(ctx):
    headers = _accounts(ctx)
    _make_pre_pr19_tombstone(ctx, "ZZZ2000018")

    hard = ctx.client.post("/api/voters/ZZZ2000018/hard-delete", headers=headers["manager"])
    reused = ctx.client.post(
        "/api/voters", headers=headers["manager"], json=voter_payload(voter_id="ZZZ2000018")
    )

    assert hard.status_code == 200, hard.text
    assert reused.status_code == 201, reused.text
