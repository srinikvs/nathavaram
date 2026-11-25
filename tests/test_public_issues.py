"""Public Issues: roles, status rules, voter auto-fill, search, photos, notes, and CSRF.

All data is synthetic: fake names, 90000000xx mobiles, generated images. Photos are written to
a temporary directory, never to the repository.
"""

from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app.issue_rules import MAX_PHOTOS_PER_ISSUE, check_transition, status_options
from app.errors import ServiceError
from app.services.issue_photos import HEIC_SUPPORTED, photo_path
from tests.conftest import csrf_from_page, make_user, voter_payload

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
CLERK = "clerk.one"
CLERK_PW = "clerk-pass-123"
MANAGER = "mgr.one"
MANAGER_PW = "mgr-pass-123"
SECOND_CLERK = "clerk.two"
SECOND_CLERK_PW = "clerk2-pass-123"
ADMIN_PW = "admin-pass-123"

INITIATED = "Initiated"
IN_PROCESS = "In Process"
RESOLVED = "Resolved"
REJECTED = "Rejected"


@pytest.fixture
def issues(ctx, tmp_path):
    """Users for each role plus a private photo directory. Returns a namespace of headers."""
    photo_dir = tmp_path / "issue-photos"
    ctx.client.app.state.settings.issue_photo_dir = str(photo_dir)
    clerk = make_user(ctx, CLERK, CLERK_PW, role="user")
    clerk_two = make_user(ctx, SECOND_CLERK, SECOND_CLERK_PW, role="user")
    manager = make_user(ctx, MANAGER, MANAGER_PW, role="manager")
    return SimpleNamespace(
        ctx=ctx,
        client=ctx.client,
        photo_dir=photo_dir,
        admin=ctx.auth,
        clerk=clerk,
        clerk_two=clerk_two,
        manager=manager,
    )


def _body(**overrides) -> dict:
    data = {
        "voter_id": None,
        "name": "Sample Citizen",
        "mobile": "9000000041",
        "subject": "Road repair",
        "issue": "Pothole near the well on the main street",
    }
    data.update(overrides)
    return data


def _create(env, headers, **overrides):
    return env.client.post("/api/issues", headers=headers, json=_body(**overrides))


def _create_ok(env, headers, **overrides) -> dict:
    response = _create(env, headers, **overrides)
    assert response.status_code == 201, response.text
    return response.json()


def _status(env, headers, issue_id: int, target: str):
    return env.client.post(f"/api/issues/{issue_id}/status", headers=headers, json={"status": target})


def _current(env, issue_id: int) -> str:
    return env.client.get(f"/api/issues/{issue_id}", headers=env.admin).json()["issue"]["status"]


def _at_status(env, issue_id: int, target: str) -> None:
    """Put an issue in `target` as the manager (managers may set any status)."""
    if target == INITIATED:
        return
    response = _status(env, env.manager, issue_id, target)
    assert response.status_code == 200, response.text


def _web_login(ctx, username: str, password: str) -> None:
    ctx.client.cookies.clear()
    response = ctx.client.post(
        "/login", data={"username": username, "password": password}, follow_redirects=False
    )
    assert response.status_code == 303, response.text


def _voter(env, voter_id: str = "ZZZ3000001", **overrides) -> None:
    response = env.client.post(
        "/api/voters", headers=env.admin, json=voter_payload(voter_id=voter_id, **overrides)
    )
    assert response.status_code == 201, response.text


def _jpeg(size=(64, 48), color=(180, 40, 40), exif=None) -> bytes:
    buffer = BytesIO()
    image = Image.new("RGB", size, color)
    if exif is not None:
        image.save(buffer, format="JPEG", exif=exif)
    else:
        image.save(buffer, format="JPEG")
    return buffer.getvalue()


def _png(size=(64, 48)) -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", size, (0, 0, 255, 0)).save(buffer, format="PNG")
    return buffer.getvalue()


def _upload(env, headers, issue_id: int, data: bytes, content_type="image/jpeg", name="photo.jpg"):
    return env.client.post(
        f"/api/issues/{issue_id}/photos",
        headers=headers,
        files={"file": (name, data, content_type)},
    )


def _files(photo_dir: Path) -> list[Path]:
    if not photo_dir.exists():
        return []
    return sorted(path for path in photo_dir.rglob("*") if path.is_file())


# --- 1-2. creating ---------------------------------------------------------------------------


def test_user_creates_issue_that_starts_as_initiated(issues):
    created = _create_ok(issues, issues.clerk)

    assert created["status"] == INITIATED
    assert created["created_by"] == CLERK
    assert created["voter_id"] is None


def test_create_refuses_a_requested_status(issues):
    response = _create(issues, issues.manager, status=RESOLVED)

    assert response.status_code == 422
    assert _create(issues, issues.clerk, status=IN_PROCESS).status_code == 422


# --- 3. user's one allowed move ----------------------------------------------------------------


def test_user_moves_initiated_to_in_process_via_api(issues):
    issue_id = _create_ok(issues, issues.clerk)["id"]

    response = _status(issues, issues.clerk, issue_id, IN_PROCESS)

    assert response.status_code == 200
    assert response.json()["status"] == IN_PROCESS


def test_user_moves_initiated_to_in_process_via_web_form(issues):
    issue_id = _create_ok(issues, issues.clerk)["id"]
    _web_login(issues.ctx, CLERK, CLERK_PW)
    page = issues.client.get("/issues/" + str(issue_id))
    token = csrf_from_page(page.text)

    response = issues.client.post(
        f"/issues/{issue_id}/status",
        data={"csrf_token": token, "status": IN_PROCESS},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert _current(issues, issue_id) == IN_PROCESS


# --- 4. user blocked from Resolved and Rejected -----------------------------------------------


@pytest.mark.parametrize("target", [RESOLVED, REJECTED])
def test_user_cannot_set_resolved_or_rejected_from_initiated(issues, target):
    issue_id = _create_ok(issues, issues.clerk)["id"]

    response = _status(issues, issues.clerk, issue_id, target)

    assert response.status_code == 403
    assert _current(issues, issue_id) == INITIATED


@pytest.mark.parametrize("target", [RESOLVED, REJECTED])
def test_user_cannot_set_resolved_or_rejected_from_in_process(issues, target):
    issue_id = _create_ok(issues, issues.clerk)["id"]
    assert _status(issues, issues.clerk, issue_id, IN_PROCESS).status_code == 200

    response = _status(issues, issues.clerk, issue_id, target)

    assert response.status_code == 403
    assert _current(issues, issue_id) == IN_PROCESS


# --- 5. user blocked from reopening ----------------------------------------------------------


def test_user_cannot_reopen_in_process_to_initiated(issues):
    issue_id = _create_ok(issues, issues.clerk)["id"]
    _at_status(issues, issue_id, IN_PROCESS)

    response = _status(issues, issues.clerk, issue_id, INITIATED)

    assert response.status_code == 403
    assert _current(issues, issue_id) == IN_PROCESS


@pytest.mark.parametrize("target", [IN_PROCESS, INITIATED])
def test_user_cannot_reopen_resolved(issues, target):
    issue_id = _create_ok(issues, issues.clerk)["id"]
    _at_status(issues, issue_id, RESOLVED)

    response = _status(issues, issues.clerk, issue_id, target)

    assert response.status_code == 403
    assert _current(issues, issue_id) == RESOLVED


@pytest.mark.parametrize("target", [INITIATED, IN_PROCESS, RESOLVED])
def test_user_cannot_reopen_rejected_to_an_earlier_status(issues, target):
    issue_id = _create_ok(issues, issues.clerk)["id"]
    _at_status(issues, issue_id, REJECTED)

    response = _status(issues, issues.clerk, issue_id, target)

    assert response.status_code == 403
    assert _current(issues, issue_id) == REJECTED


def test_status_must_be_a_known_label_and_cannot_repeat(issues):
    issue_id = _create_ok(issues, issues.manager)["id"]

    assert _status(issues, issues.manager, issue_id, "Closed").status_code == 422
    assert _status(issues, issues.manager, issue_id, INITIATED).status_code == 409


# --- 6. manager and admin may set any status ---------------------------------------------------


@pytest.mark.parametrize("target", [RESOLVED, REJECTED])
def test_manager_sets_resolved_and_rejected(issues, target):
    issue_id = _create_ok(issues, issues.clerk)["id"]

    response = _status(issues, issues.manager, issue_id, target)

    assert response.status_code == 200
    assert _current(issues, issue_id) == target


def test_manager_reopens_resolved_and_in_process(issues):
    issue_id = _create_ok(issues, issues.clerk)["id"]
    _at_status(issues, issue_id, RESOLVED)

    assert _status(issues, issues.manager, issue_id, IN_PROCESS).status_code == 200
    assert _status(issues, issues.manager, issue_id, INITIATED).status_code == 200
    assert _current(issues, issue_id) == INITIATED


def test_admin_sets_any_status_including_reopening(issues):
    issue_id = _create_ok(issues, issues.clerk)["id"]

    assert _status(issues, issues.admin, issue_id, REJECTED).status_code == 200
    assert _status(issues, issues.admin, issue_id, IN_PROCESS).status_code == 200


def test_history_and_notes_record_each_change_with_who_and_when(issues):
    issue_id = _create_ok(issues, issues.clerk)["id"]
    assert _status(issues, issues.clerk, issue_id, IN_PROCESS).status_code == 200
    assert _status(issues, issues.manager, issue_id, RESOLVED).status_code == 200
    assert _status(issues, issues.manager, issue_id, IN_PROCESS).status_code == 200

    detail = issues.client.get(f"/api/issues/{issue_id}", headers=issues.admin).json()

    steps = [(event["from_status"], event["to_status"], event["changed_by"]) for event in detail["history"]]
    assert steps == [
        (None, INITIATED, CLERK),
        (INITIATED, IN_PROCESS, CLERK),
        (IN_PROCESS, RESOLVED, MANAGER),
        (RESOLVED, IN_PROCESS, MANAGER),
    ]
    assert all(event["changed_at"] for event in detail["history"])
    system_lines = [note["body"] for note in detail["notes"] if note["kind"] == "system"]
    assert system_lines == [
        f"{INITIATED} to {IN_PROCESS} by {CLERK}",
        f"{IN_PROCESS} to {RESOLVED} by {MANAGER}",
        f"{RESOLVED} to {IN_PROCESS} by {MANAGER}",
    ]


def test_status_options_offer_only_legal_moves_to_users():
    assert status_options(False, INITIATED) == [INITIATED, IN_PROCESS]
    assert status_options(False, IN_PROCESS) == [IN_PROCESS]
    assert status_options(False, RESOLVED) == [RESOLVED]
    assert status_options(True, RESOLVED) == [INITIATED, IN_PROCESS, RESOLVED, REJECTED]


@pytest.mark.parametrize(
    ("can_manage", "current", "target"),
    [
        (False, INITIATED, RESOLVED),
        (False, IN_PROCESS, INITIATED),
        (False, RESOLVED, INITIATED),
        (True, RESOLVED, INITIATED),
    ],
)
def test_check_transition_table(can_manage, current, target):
    if can_manage:
        check_transition(True, current, target)
        return
    with pytest.raises(ServiceError) as refused:
        check_transition(False, current, target)
    assert refused.value.status_code == 403


# --- 7. delete -----------------------------------------------------------------------------


def test_user_cannot_delete_issue(issues):
    issue_id = _create_ok(issues, issues.clerk)["id"]

    response = issues.client.delete(f"/api/issues/{issue_id}", headers=issues.clerk)

    assert response.status_code == 403
    assert _current(issues, issue_id) == INITIATED


@pytest.mark.parametrize("role", ["manager", "admin"])
def test_manager_and_admin_delete_issue_and_its_photos(issues, role):
    headers = issues.manager if role == "manager" else issues.admin
    issue_id = _create_ok(issues, issues.clerk)["id"]
    assert _upload(issues, issues.clerk, issue_id, _jpeg()).status_code == 201
    assert _files(issues.photo_dir)

    response = issues.client.delete(f"/api/issues/{issue_id}", headers=headers)

    assert response.status_code == 200
    assert issues.client.get(f"/api/issues/{issue_id}", headers=headers).status_code == 404
    assert _files(issues.photo_dir) == []


# --- 8-9. voter auto-fill and the manual path --------------------------------------------------


def test_blank_voter_id_uses_hand_typed_name_and_mobile(issues):
    created = _create_ok(issues, issues.clerk, voter_id="", name="  Typed Name  ", mobile="+91 90000 00022")

    assert created["voter_id"] is None
    assert created["name"] == "Typed Name"
    assert created["mobile"] == "***********0022"


def test_unknown_voter_id_stores_null_voter_id_and_typed_contact(issues):
    created = _create_ok(issues, issues.clerk, voter_id="ZZZ9999999", name="Manual Person", mobile="9000000043")

    assert created["voter_id"] is None
    assert created["name"] == "Manual Person"
    assert created["mobile"] == "******0043"


@pytest.mark.parametrize(
    "overrides",
    [{"name": ""}, {"mobile": ""}, {"mobile": "12345"}, {"subject": "  "}, {"issue": ""}],
)
def test_manual_path_requires_name_subject_issue_and_valid_mobile(issues, overrides):
    response = _create(issues, issues.clerk, voter_id="", **overrides)

    assert response.status_code == 422


def test_matching_active_voter_fills_name_and_mobile(issues):
    _voter(issues, "ZZZ3000002")

    created = _create_ok(issues, issues.clerk, voter_id="zzz3000002", name="Ignored Typed Name")

    assert created["voter_id"] == "ZZZ3000002"
    assert created["name"] == "Testperson Alpha"
    assert created["mobile"] == "******0021"


def test_lookup_returns_active_voter_only_and_requires_login(issues):
    _voter(issues, "ZZZ3000003")

    found = issues.client.post(
        "/api/issues/voter-lookup", headers=issues.clerk, json={"voter_id": "zzz3000003"}
    ).json()
    anonymous = issues.client.post("/api/issues/voter-lookup", json={"voter_id": "ZZZ3000003"})

    assert found == {"found": True, "voter_id": "ZZZ3000003", "name": "Testperson Alpha", "mobile": "9000000021"}
    assert anonymous.status_code == 401


def test_snapshot_does_not_follow_later_voter_edits(issues):
    _voter(issues, "ZZZ3000004")
    issue_id = _create_ok(issues, issues.clerk, voter_id="ZZZ3000004")["id"]

    edit = issues.client.patch(
        "/api/voters/ZZZ3000004", headers=issues.admin, json={"first_name": "Changed", "mobile": "9000000099"}
    )

    assert edit.status_code == 200
    detail = issues.client.get(f"/api/issues/{issue_id}", headers=issues.clerk).json()["issue"]
    assert detail["name"] == "Testperson Alpha"
    assert detail["mobile"] == "******0021"


def test_soft_deleted_voter_is_not_used_for_autofill(issues):
    _voter(issues, "ZZZ3000005")
    assert issues.client.delete("/api/voters/ZZZ3000005", headers=issues.admin).status_code == 200

    lookup = issues.client.post(
        "/api/issues/voter-lookup", headers=issues.clerk, json={"voter_id": "ZZZ3000005"}
    ).json()
    created = _create_ok(issues, issues.clerk, voter_id="ZZZ3000005", name="Manual After Delete")

    assert lookup["found"] is False
    assert created["voter_id"] is None
    assert created["name"] == "Manual After Delete"


def test_hard_deleted_voter_is_not_used_for_autofill(issues):
    _voter(issues, "ZZZ3000006")
    assert issues.client.delete("/api/voters/ZZZ3000006", headers=issues.admin).status_code == 200
    assert issues.client.post("/api/voters/ZZZ3000006/hard-delete", headers=issues.admin).status_code == 200

    created = _create_ok(issues, issues.clerk, voter_id="ZZZ3000006", name="Manual After Hard Delete")

    assert created["voter_id"] is None
    assert created["name"] == "Manual After Hard Delete"


# --- 10. search, filters, counts, Telugu --------------------------------------------------------


def test_search_matches_voter_id_name_and_subject(issues):
    _voter(issues, "ZZZ3000007")
    _create_ok(issues, issues.clerk, voter_id="ZZZ3000007", subject="Water supply")
    _create_ok(issues, issues.clerk, name="Priya Sample", subject="Drainage")
    _create_ok(issues, issues.clerk, name="Other Person", subject="Street lights")

    def search(q: str) -> list[str]:
        response = issues.client.post(
            "/api/issues/search", headers=issues.clerk, json={"q": q}
        )
        return [item["subject"] for item in response.json()["items"]]

    assert search("zzz3000007") == ["Water supply"]
    assert search("priya") == ["Drainage"]
    assert search("street") == ["Street lights"]
    assert search("nothing-matches") == []


def test_status_filter_and_counts_reflect_live_data(issues):
    first = _create_ok(issues, issues.clerk, subject="First")["id"]
    _create_ok(issues, issues.clerk, subject="Second")
    _at_status(issues, first, RESOLVED)

    everything = issues.client.post("/api/issues/search", headers=issues.clerk, json={}).json()
    resolved = issues.client.post(
        "/api/issues/search", headers=issues.clerk, json={"status": RESOLVED}
    ).json()

    assert everything["counts"] == {INITIATED: 1, IN_PROCESS: 0, RESOLVED: 1, REJECTED: 0}
    assert everything["total"] == 2
    assert [item["id"] for item in resolved["items"]] == [first]


def test_telugu_issue_text_is_stored_and_shown_on_the_page(issues):
    telugu_subject = "తాగునీటి సమస్య"
    telugu_issue = "ఊరి బావి దగ్గర నీళ్లు రావడం లేదు"
    created = _create_ok(issues, issues.clerk, subject=telugu_subject, issue=telugu_issue)
    _web_login(issues.ctx, CLERK, CLERK_PW)

    page = issues.client.get(f"/issues/{created['id']}")

    assert created["issue"] == telugu_issue
    assert page.status_code == 200
    assert telugu_subject in page.text
    assert telugu_issue in page.text


# --- 11. photos -----------------------------------------------------------------------------


def test_photo_cap_is_five_then_conflict(issues):
    issue_id = _create_ok(issues, issues.clerk)["id"]

    for _ in range(5):
        assert _upload(issues, issues.clerk, issue_id, _jpeg()).status_code == 201
    response = _upload(issues, issues.clerk, issue_id, _jpeg())

    assert response.status_code == 409
    assert len(_files(issues.photo_dir)) == 5


def test_jpeg_is_shrunk_to_1600_long_edge_and_loses_gps_and_software_tags(issues):
    exif = Image.Exif()
    exif[0x0131] = "synthetic-camera"
    gps = exif.get_ifd(0x8825)
    gps[1] = "N"
    gps[2] = (17.0, 0.0, 0.0)
    source = _jpeg(size=(2400, 1200), exif=exif)
    assert 0x8825 in Image.open(BytesIO(source)).getexif()
    issue_id = _create_ok(issues, issues.clerk)["id"]

    uploaded = _upload(issues, issues.clerk, issue_id, source)
    photo_id = uploaded.json()["id"]
    served = issues.client.get(f"/api/issues/photos/{photo_id}/file", headers=issues.clerk)

    assert uploaded.status_code == 201
    assert uploaded.json()["width"] == 1600 and uploaded.json()["height"] == 800
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/jpeg"
    stored = Image.open(BytesIO(served.content))
    assert stored.size == (1600, 800)
    assert len(stored.getexif()) == 0


def test_png_with_transparency_is_accepted_and_small_images_are_not_enlarged(issues):
    issue_id = _create_ok(issues, issues.clerk)["id"]

    png = _upload(issues, issues.clerk, issue_id, _png(), "image/png", "photo.png")
    small = _upload(issues, issues.clerk, issue_id, _jpeg(size=(64, 48)))

    assert png.status_code == 201
    assert (png.json()["width"], png.json()["height"]) == (64, 48)
    assert (small.json()["width"], small.json()["height"]) == (64, 48)


@pytest.mark.skipif(not HEIC_SUPPORTED, reason="pillow-heif is not installed in this environment")
def test_heic_upload_is_accepted_and_converted_to_jpeg(issues):
    buffer = BytesIO()
    Image.new("RGB", (320, 200), (30, 120, 60)).save(buffer, format="HEIF")
    issue_id = _create_ok(issues, issues.clerk)["id"]

    response = _upload(issues, issues.clerk, issue_id, buffer.getvalue(), "image/heic", "photo.heic")

    assert response.status_code == 201
    assert (response.json()["width"], response.json()["height"]) == (320, 200)


def test_non_image_upload_is_refused(issues):
    issue_id = _create_ok(issues, issues.clerk)["id"]

    response = _upload(issues, issues.clerk, issue_id, b"this is not an image", "text/plain", "note.txt")

    assert response.status_code == 422
    assert _files(issues.photo_dir) == []


def test_photo_file_requires_sign_in(issues):
    issue_id = _create_ok(issues, issues.clerk)["id"]
    photo_id = _upload(issues, issues.clerk, issue_id, _jpeg()).json()["id"]

    anonymous = issues.client.get(f"/api/issues/photos/{photo_id}/file")

    assert anonymous.status_code == 401


def test_user_adds_photo_but_cannot_remove_it_manager_can(issues):
    issue_id = _create_ok(issues, issues.clerk)["id"]
    photo_id = _upload(issues, issues.clerk, issue_id, _jpeg()).json()["id"]
    path = f"/api/issues/{issue_id}/photos/{photo_id}"

    assert issues.client.delete(path, headers=issues.clerk).status_code == 403
    assert _files(issues.photo_dir)

    assert issues.client.delete(path, headers=issues.manager).status_code == 200
    assert _files(issues.photo_dir) == []


def test_photo_path_refuses_names_outside_the_photo_directory(tmp_path):
    with pytest.raises(ServiceError) as escaped:
        photo_path(tmp_path / "photos", "../secret.jpg")

    assert escaped.value.status_code == 404


def test_photo_upload_through_web_form_with_csrf(issues):
    issue_id = _create_ok(issues, issues.clerk)["id"]
    _web_login(issues.ctx, CLERK, CLERK_PW)
    token = csrf_from_page(issues.client.get(f"/issues/{issue_id}").text)

    response = issues.client.post(
        f"/issues/{issue_id}/photos",
        data={"csrf_token": token},
        files={"photo": ("photo.jpg", _jpeg(), "image/jpeg")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert len(_files(issues.photo_dir)) == 1


# --- 12. notes -------------------------------------------------------------------------------


def test_any_role_adds_a_note_and_telugu_note_is_kept(issues):
    issue_id = _create_ok(issues, issues.clerk)["id"]

    for headers in (issues.clerk, issues.manager, issues.admin):
        response = issues.client.post(
            f"/api/issues/{issue_id}/notes", headers=headers, json={"text": "ఫాలో అప్ చేశాం"}
        )
        assert response.status_code == 201
        assert response.json()["author"] in {CLERK, MANAGER, "admin"}

    notes = issues.client.get(f"/api/issues/{issue_id}", headers=issues.clerk).json()["notes"]
    assert [note["body"] for note in notes if note["kind"] == "note"] == ["ఫాలో అప్ చేశాం"] * 3


def test_notes_cannot_be_edited(issues):
    issue_id = _create_ok(issues, issues.clerk)["id"]
    note_id = issues.client.post(
        f"/api/issues/{issue_id}/notes", headers=issues.clerk, json={"text": "first"}
    ).json()["id"]

    response = issues.client.patch(
        f"/api/issues/{issue_id}/notes/{note_id}", headers=issues.admin, json={"text": "changed"}
    )

    assert response.status_code == 405


def test_only_admin_can_delete_a_note(issues):
    issue_id = _create_ok(issues, issues.clerk)["id"]

    def new_note() -> int:
        return issues.client.post(
            f"/api/issues/{issue_id}/notes", headers=issues.clerk, json={"text": "to remove"}
        ).json()["id"]

    manager_note = new_note()
    assert issues.client.delete(f"/api/issues/{issue_id}/notes/{manager_note}", headers=issues.manager).status_code == 403
    assert issues.client.delete(f"/api/issues/{issue_id}/notes/{manager_note}", headers=issues.clerk).status_code == 403

    admin_delete = issues.client.delete(f"/api/issues/{issue_id}/notes/{manager_note}", headers=issues.admin)

    assert admin_delete.status_code == 200


def test_status_lines_in_the_thread_cannot_be_deleted(issues):
    issue_id = _create_ok(issues, issues.clerk)["id"]
    assert _status(issues, issues.clerk, issue_id, IN_PROCESS).status_code == 200
    detail = issues.client.get(f"/api/issues/{issue_id}", headers=issues.admin).json()
    system_id = next(note["id"] for note in detail["notes"] if note["kind"] == "system")

    response = issues.client.delete(f"/api/issues/{issue_id}/notes/{system_id}", headers=issues.admin)

    assert response.status_code == 409


# --- 13. CSRF and the web pages ------------------------------------------------------------------


def test_cookie_unsafe_requests_need_csrf(issues):
    _web_login(issues.ctx, CLERK, CLERK_PW)

    refused = issues.client.post(
        "/issues/new",
        data={"voter_id": "", "name": "No Token", "mobile": "9000000051", "subject": "S", "issue": "I"},
        follow_redirects=False,
    )
    token = csrf_from_page(issues.client.get("/issues/new").text)
    accepted = issues.client.post(
        "/issues/new",
        data={
            "csrf_token": token,
            "voter_id": "",
            "name": "With Token",
            "mobile": "9000000052",
            "subject": "Subject",
            "issue": "Issue text",
        },
        follow_redirects=False,
    )

    assert refused.status_code == 403
    assert accepted.status_code == 303
    assert accepted.headers["location"].startswith("/issues/")


def test_cookie_api_post_without_csrf_header_is_refused(issues):
    _web_login(issues.ctx, CLERK, CLERK_PW)

    response = issues.client.post("/api/issues", json=_body())

    assert response.status_code == 403


def test_web_add_form_stores_hand_typed_contact(issues):
    _web_login(issues.ctx, CLERK, CLERK_PW)
    token = csrf_from_page(issues.client.get("/issues/new").text)

    response = issues.client.post(
        "/issues/new",
        data={
            "csrf_token": token,
            "voter_id": "",
            "name": "Web Person",
            "mobile": "9000000053",
            "subject": "Water",
            "issue": "Tap is dry",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    page = issues.client.get(response.headers["location"].split("?")[0])
    assert "Web Person" in page.text
    assert "******0053" in page.text
    assert "9000000053" not in page.text


def test_list_page_shows_heading_count_cards_and_columns(issues):
    _create_ok(issues, issues.clerk)
    _web_login(issues.ctx, CLERK, CLERK_PW)

    page = issues.client.get("/issues")

    assert page.status_code == 200
    assert "Public issues · 1" in page.text
    assert "Track and follow up on public issues." in page.text
    for label in ("INITIATED", "IN PROCESS", "RESOLVED", "REJECTED"):
        assert label in page.text
    assert 'data-label="VOTERID"' in page.text
    assert "Add issue" in page.text


def test_web_status_filter_card_and_search_form(issues):
    first = _create_ok(issues, issues.clerk, subject="Alpha subject")["id"]
    _create_ok(issues, issues.clerk, subject="Beta subject")
    _at_status(issues, first, RESOLVED)
    _web_login(issues.ctx, MANAGER, MANAGER_PW)

    filtered = issues.client.get("/issues?status=Resolved")
    token = csrf_from_page(filtered.text)
    searched = issues.client.post(
        "/issues/search", data={"csrf_token": token, "q": "beta"}
    )

    assert "Public issues · 1" in filtered.text
    assert "Alpha subject" in filtered.text
    assert "Beta subject" not in filtered.text
    assert "Public issues · 1" in searched.text
    assert "Beta subject" in searched.text


def test_manager_sees_delete_and_remove_controls_user_does_not(issues):
    issue_id = _create_ok(issues, issues.clerk)["id"]
    _upload(issues, issues.clerk, issue_id, _jpeg())
    _web_login(issues.ctx, CLERK, CLERK_PW)
    user_page = issues.client.get(f"/issues/{issue_id}").text
    _web_login(issues.ctx, MANAGER, MANAGER_PW)
    manager_page = issues.client.get(f"/issues/{issue_id}").text

    assert f'action="/issues/{issue_id}/delete"' not in user_page
    assert f'action="/issues/{issue_id}/delete"' in manager_page
    assert "/photos/" in manager_page and "/delete" in manager_page


def test_issue_pages_require_sign_in(issues):
    issue_id = _create_ok(issues, issues.clerk)["id"]
    issues.ctx.client.cookies.clear()

    for path in ("/issues", "/issues/new", f"/issues/{issue_id}"):
        response = issues.client.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"].startswith("/login"), path


def test_sidebar_links_public_issues_to_the_live_page(issues):
    _web_login(issues.ctx, CLERK, CLERK_PW)

    html = issues.client.get("/voters").text

    assert 'href="/issues"' in html
    assert "/campaign/public-issues" not in html


# --- 13. photos on the add form ------------------------------------------------------------


def _add_form_post(env, files=None, **overrides):
    """POST /issues/new as a signed-in clerk with CSRF. Returns the response (not followed)."""
    _web_login(env.ctx, CLERK, CLERK_PW)
    token = csrf_from_page(env.client.get("/issues/new").text)
    fields = {
        "csrf_token": token,
        "voter_id": "",
        "name": "Photo Person",
        "mobile": "9000000061",
        "subject": "Drain",
        "issue": "Open drain overflows near the school",
    }
    fields.update(overrides)
    return env.client.post("/issues/new", data=fields, files=files, follow_redirects=False)


def _issue_total(env) -> int:
    response = env.client.post("/api/issues/search", headers=env.admin, json={})
    assert response.status_code == 200, response.text
    return response.json()["total"]


def test_add_form_has_multiple_photo_input_below_full_width_issue_field(issues):
    _web_login(issues.ctx, CLERK, CLERK_PW)

    page = issues.client.get("/issues/new").text

    assert 'enctype="multipart/form-data"' in page
    photos_input = re.search(r'<input[^>]*name="photos"[^>]*>', page)
    assert photos_input and "multiple" in photos_input.group(0)
    assert 'accept="image/jpeg,image/png,image/heic,image/heif"' in photos_input.group(0)
    assert re.search(r'<div class="wide">\s*<label for="issue">Issue</label>', page)
    assert page.index('name="issue"') < photos_input.start() < page.index("Save issue")


def test_add_form_issue_textarea_is_half_height_and_full_width(issues):
    _web_login(issues.ctx, CLERK, CLERK_PW)

    page = issues.client.get("/issues/new").text
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    issue_textarea = re.search(r'<textarea id="issue"[^>]*>', page)

    assert issue_textarea and 'rows="5"' in issue_textarea.group(0)
    assert 'rows="10"' not in issue_textarea.group(0)
    assert re.search(r'<div class="wide">\s*<label for="issue">Issue</label>', page)
    assert re.search(r"input,\s*select,\s*textarea\s*\{[^}]*width:\s*100%", css)
    issue_rule = re.search(r"\.issue-form textarea\s*\{([^}]*)\}", css)
    assert issue_rule and "min-height: 6rem" in issue_rule.group(1)
    assert "min-height: 12rem" not in css
    assert "resize: vertical" in issue_rule.group(1)


def test_web_create_without_photos_still_creates_issue(issues):
    response = _add_form_post(issues, files={"photos": ("", b"", "application/octet-stream")})

    assert response.status_code == 303
    assert response.headers["location"].startswith("/issues/")
    assert _issue_total(issues) == 1
    assert _files(issues.photo_dir) == []


def test_web_create_with_photos_stores_them_under_issue_folder_and_shows_them(issues):
    response = _add_form_post(
        issues,
        files=[
            ("photos", ("first.jpg", _jpeg(), "image/jpeg")),
            ("photos", ("second.png", _png(), "image/png")),
        ],
    )

    assert response.status_code == 303
    issue_id = int(response.headers["location"].split("?")[0].rsplit("/", 1)[1])
    detail = issues.client.get(f"/api/issues/{issue_id}", headers=issues.admin).json()
    assert len(detail["photos"]) == 2
    stored = _files(issues.photo_dir)
    assert len(stored) == 2
    for path in stored:
        assert path.parent == issues.photo_dir / str(issue_id)
        assert path.suffix == ".jpg"
    served = issues.client.get(detail["photos"][0]["url"], headers=issues.clerk)
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/jpeg"
    page = issues.client.get(f"/issues/{issue_id}", headers=issues.clerk).text
    assert detail["photos"][0]["url"] in page


def test_web_create_with_more_than_five_photos_is_refused_and_creates_nothing(issues):
    files = [("photos", (f"p{i}.jpg", _jpeg(), "image/jpeg")) for i in range(MAX_PHOTOS_PER_ISSUE + 1)]

    response = _add_form_post(issues, files=files)

    assert response.status_code == 422
    assert "5 photos" in response.text
    assert _issue_total(issues) == 0
    assert _files(issues.photo_dir) == []


def test_web_create_with_text_file_is_refused_and_creates_nothing(issues):
    response = _add_form_post(
        issues, files=[("photos", ("note.txt", b"this is not an image", "text/plain"))]
    )

    assert response.status_code == 422
    assert "Upload a JPG, PNG or HEIC photo" in response.text
    assert _issue_total(issues) == 0
    assert _files(issues.photo_dir) == []


def test_web_create_with_one_bad_photo_among_good_ones_saves_nothing(issues):
    response = _add_form_post(
        issues,
        files=[
            ("photos", ("good.jpg", _jpeg(), "image/jpeg")),
            ("photos", ("bad.txt", b"not an image", "text/plain")),
        ],
    )

    assert response.status_code == 422
    assert _issue_total(issues) == 0
    assert _files(issues.photo_dir) == []


# --- 14. issue mobile is masked on every read path -----------------------------------------------

GAME_QA_MOBILE = "9000000077"
GAME_QA_MASKED = "******0077"


def _stored_issue_mobile(issue_id: int) -> str:
    from app import database
    from app.models import PublicIssue

    db = database.SessionLocal()
    try:
        return db.get(PublicIssue, issue_id).mobile
    finally:
        db.close()


def test_mask_issue_mobile_keeps_only_last_four_characters():
    from app.services.issues import mask_issue_mobile

    assert mask_issue_mobile("9000000012") == "******0012"
    assert mask_issue_mobile("+91 90000 00022") == "***********0022"


def test_mask_issue_mobile_fully_masks_values_shorter_than_four_characters():
    from app.services.issues import mask_issue_mobile

    assert mask_issue_mobile("123") == "***"
    assert mask_issue_mobile("1") == "*"


def test_mask_issue_mobile_leaves_empty_values_empty():
    from app.services.issues import mask_issue_mobile

    assert mask_issue_mobile(None) is None
    assert mask_issue_mobile("") == ""


def test_api_create_returns_masked_mobile_and_database_keeps_full_number(issues):
    created = _create_ok(issues, issues.clerk, voter_id="", name="Game QA", mobile=GAME_QA_MOBILE)

    assert created["mobile"] == GAME_QA_MASKED
    assert GAME_QA_MOBILE not in str(created)
    assert _stored_issue_mobile(created["id"]) == GAME_QA_MOBILE


def test_api_get_issue_returns_masked_mobile(issues):
    issue_id = _create_ok(issues, issues.clerk, voter_id="", mobile=GAME_QA_MOBILE)["id"]

    response = issues.client.get(f"/api/issues/{issue_id}", headers=issues.clerk)

    assert response.status_code == 200
    assert response.json()["issue"]["mobile"] == GAME_QA_MASKED
    assert GAME_QA_MOBILE not in response.text


def test_api_search_returns_masked_mobile(issues):
    _create_ok(issues, issues.clerk, voter_id="", mobile=GAME_QA_MOBILE)

    response = issues.client.post("/api/issues/search", headers=issues.clerk, json={})

    assert response.status_code == 200
    assert response.json()["items"][0]["mobile"] == GAME_QA_MASKED
    assert GAME_QA_MOBILE not in response.text


def test_api_status_change_returns_masked_mobile(issues):
    issue_id = _create_ok(issues, issues.clerk, voter_id="", mobile=GAME_QA_MOBILE)["id"]

    response = _status(issues, issues.manager, issue_id, IN_PROCESS)

    assert response.status_code == 200
    assert GAME_QA_MOBILE not in response.text
    assert GAME_QA_MASKED in response.text


def test_web_list_shows_masked_mobile_only(issues):
    _create_ok(issues, issues.clerk, voter_id="", mobile=GAME_QA_MOBILE)
    _web_login(issues.ctx, CLERK, CLERK_PW)

    page = issues.client.get("/issues").text

    assert GAME_QA_MASKED in page
    assert GAME_QA_MOBILE not in page


def test_web_detail_shows_masked_mobile_only(issues):
    issue_id = _create_ok(issues, issues.clerk, voter_id="", mobile=GAME_QA_MOBILE)["id"]
    _web_login(issues.ctx, CLERK, CLERK_PW)

    page = issues.client.get(f"/issues/{issue_id}").text

    assert GAME_QA_MASKED in page
    assert GAME_QA_MOBILE not in page
