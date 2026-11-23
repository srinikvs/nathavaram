"""Bulk select, edit and delete on Voters and Master Data. Synthetic fixtures only."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.conftest import csrf_from_page, make_user, voter_payload

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


def _add(ctx, n: int, **overrides) -> str:
    voter_id = f"ZZZ40000{n:02d}"
    response = ctx.client.post(
        "/api/voters",
        headers=ctx.auth,
        json=voter_payload(voter_id=voter_id, mobile=f"90000000{n:02d}", **overrides),
    )
    assert response.status_code == 201, response.text
    return voter_id


def _get(ctx, voter_id: str) -> dict:
    response = ctx.client.get(f"/api/voters/{voter_id}", headers=ctx.auth)
    assert response.status_code == 200, response.text
    return response.json()


def _bulk_edit(ctx, ids, values, headers=None):
    return ctx.client.post(
        "/api/voters/bulk-edit",
        headers=headers or ctx.auth,
        json={"voter_ids": ids, "values": values},
    )


def _bulk_delete(ctx, ids, **extra):
    return ctx.client.post(
        "/api/voters/bulk-delete", headers=ctx.auth, json={"voter_ids": ids, **extra}
    )


def _web_login(ctx, username="admin", password="admin-pass-123") -> str:
    ctx.client.cookies.clear()
    signed = ctx.client.post(
        "/login", data={"username": username, "password": password}, follow_redirects=False
    )
    assert signed.status_code == 303, signed.text
    return csrf_from_page(ctx.client.get("/voters").text)


@pytest.fixture
def three(ctx):
    return [_add(ctx, 1), _add(ctx, 2), _add(ctx, 3)]


# --- bulk edit: voters -------------------------------------------------------------------


def test_bulk_edit_applies_chosen_fields_to_selected_voters_only(ctx, three):
    first, second, third = three
    before_third = _get(ctx, third)

    response = _bulk_edit(ctx, [first, second], {"party": "BJP", "caste": "NewGroup", "age": 41})

    assert response.status_code == 200, response.text
    assert response.json()["count"] == 2
    for voter_id in (first, second):
        row = _get(ctx, voter_id)
        assert (row["party"], row["caste"], row["age"]) == ("BJP", "NewGroup", 41)
        assert row["street"] == "Sample Street"  # unchosen field untouched
    assert _get(ctx, third) == before_third


def test_bulk_edit_bumps_revision_and_source_like_single_edit(ctx, three):
    first, second, _ = three
    before = {v: _get(ctx, v)["revision"] for v in (first, second)}

    assert _bulk_edit(ctx, [first, second], {"village": "Newville"}).status_code == 200

    for voter_id in (first, second):
        assert _get(ctx, voter_id)["revision"] > before[voter_id]
    assert _get(ctx, first)["source_id"] == "web:admin"


@pytest.mark.parametrize(
    "values",
    [
        {"party": "Not A Party"},
        {"gender": "Robot"},
        {"age": 0},
        {"age": 131},
        {"age": "abc"},
        {"mobile": "123"},
    ],
)
def test_bulk_edit_rejects_values_single_edit_would_reject_and_applies_none(ctx, three, values):
    first, second, _ = three
    before = [_get(ctx, first), _get(ctx, second)]

    response = _bulk_edit(ctx, [first, second], values)

    assert response.status_code == 422, response.text
    assert [_get(ctx, first), _get(ctx, second)] == before


def test_bulk_edit_accepts_values_single_edit_canonicalises(ctx, three):
    first = three[0]

    assert _bulk_edit(ctx, [first], {"party": "jana sena", "gender": "female"}).status_code == 200

    row = _get(ctx, first)
    assert (row["party"], row["gender"]) == ("Jana Sena", "Female")


@pytest.mark.parametrize("blank", [None, "", "  "])
def test_bulk_edit_blank_is_local_defaults_to_local(ctx, blank):
    voter_id = _add(ctx, 5, is_local=False)

    assert _bulk_edit(ctx, [voter_id], {"is_local": blank}).status_code == 200

    assert _get(ctx, voter_id)["is_local"] is True


@pytest.mark.parametrize("explicit", [False, "Non-Local", "false"])
def test_bulk_edit_explicit_non_local_stays_false(ctx, explicit):
    voter_id = _add(ctx, 6, is_local=True)

    assert _bulk_edit(ctx, [voter_id], {"is_local": explicit}).status_code == 200

    assert _get(ctx, voter_id)["is_local"] is False


def test_bulk_edit_without_is_local_leaves_existing_values_unchanged(ctx):
    non_local = _add(ctx, 7, is_local=False)
    local = _add(ctx, 8, is_local=True)

    assert _bulk_edit(ctx, [non_local, local], {"party": "INC"}).status_code == 200

    assert _get(ctx, non_local)["is_local"] is False
    assert _get(ctx, local)["is_local"] is True


@pytest.mark.parametrize(
    "field", ["voter_id", "created_at", "updated_at", "revision", "deleted_at", "source_id"]
)
def test_bulk_edit_rejects_voter_id_and_internal_fields(ctx, three, field):
    first = three[0]
    before = _get(ctx, first)

    response = _bulk_edit(ctx, [first], {field: "ZZZ9999999", "party": "BJP"})

    assert response.status_code == 422
    assert _get(ctx, first) == before


def test_bulk_edit_requires_at_least_one_field_and_one_voter(ctx, three):
    assert _bulk_edit(ctx, three, {}).status_code == 422
    assert _bulk_edit(ctx, [], {"party": "BJP"}).status_code == 422


def test_bulk_edit_with_an_unknown_voter_fails_and_changes_nothing(ctx, three):
    first = three[0]
    before = _get(ctx, first)

    response = _bulk_edit(ctx, [first, "ZZZ4099999"], {"party": "BJP"})

    assert response.status_code == 404
    assert "No changes were made" in response.json()["detail"]
    assert _get(ctx, first) == before


def test_bulk_edit_does_not_touch_unselected_filtered_rows(ctx, three):
    # The client names ids; rows matching the same filter but not named are never expanded to.
    first, second, third = three
    assert _bulk_edit(ctx, [first], {"caste": "OnlyOne"}).status_code == 200

    assert _get(ctx, first)["caste"] == "OnlyOne"
    assert _get(ctx, second)["caste"] == "SampleGroupA"
    assert _get(ctx, third)["caste"] == "SampleGroupA"


# --- bulk delete: voters -----------------------------------------------------------------


def test_bulk_delete_without_confirm_deletes_nothing(ctx, three):
    for extra in ({}, {"confirm": False}):
        response = _bulk_delete(ctx, three, **extra)
        assert response.status_code == 400
        assert "confirm" in response.json()["detail"].lower()
    for voter_id in three:
        assert _get(ctx, voter_id)["first_name"] == "Testperson"


def test_bulk_delete_soft_deletes_only_selected_rows(ctx, three):
    first, second, third = three

    response = _bulk_delete(ctx, [first, second, first.lower()], confirm=True)

    assert response.status_code == 200, response.text
    assert response.json()["count"] == 2
    for voter_id in (first, second):
        assert ctx.client.get(f"/api/voters/{voter_id}", headers=ctx.auth).status_code == 404
    assert _get(ctx, third)["first_name"] == "Testperson"
    pulled = ctx.client.get("/api/sync/pull?since=0", headers=ctx.auth).json()["changes"]
    tombstones = {c["voter_id"]: c for c in pulled if c["deleted_at"]}
    assert set(tombstones) == {first, second}  # soft delete: tombstone kept, not hard-deleted
    assert tombstones[first]["first_name"] == "" and tombstones[first]["mobile"] is None


def test_bulk_delete_with_a_missing_voter_deletes_nothing(ctx, three):
    response = _bulk_delete(ctx, [three[0], "ZZZ4099999"], confirm=True)

    assert response.status_code == 404
    assert _get(ctx, three[0])["first_name"] == "Testperson"


# --- admin enforcement -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/voters/bulk-edit", {"voter_ids": ["ZZZ4000001"], "values": {"party": "BJP"}}),
        ("/api/voters/bulk-delete", {"voter_ids": ["ZZZ4000001"], "confirm": True}),
        (
            "/api/master-data/caste/rename",
            {"items": [{"caste": "SampleGroupA"}], "new_value": "Other"},
        ),
        ("/api/master-data/caste/delete", {"items": [{"caste": "SampleGroupA"}]}),
    ],
)
def test_regular_user_gets_403_and_anonymous_gets_401_on_bulk_endpoints(ctx, three, path, body):
    regular = make_user(ctx)

    forbidden = ctx.client.post(path, headers=regular, json=body)
    anonymous = ctx.client.post(path, json=body)

    assert forbidden.status_code == 403
    assert forbidden.json()["detail"] == "Admin role required"
    assert anonymous.status_code == 401
    assert _get(ctx, three[0])["party"] == "TDP"
    assert _get(ctx, three[0])["first_name"] == "Testperson"


def test_regular_user_web_bulk_posts_are_forbidden(ctx, three):
    make_user(ctx, "plain.user", "plain-pass-123")
    token = _web_login(ctx, "plain.user", "plain-pass-123")

    for path, data in (
        ("/voters/bulk-edit", {"voter_ids": three, "apply": "1", "use_party": "1", "value_party": "BJP"}),
        ("/voters/bulk-delete", {"voter_ids": three, "confirm": "yes"}),
        ("/master-data/bulk-delete", {"tab": "caste", "rows": ['{"caste": "SampleGroupA"}']}),
    ):
        response = ctx.client.post(path, data={"csrf_token": token, **data})
        assert response.status_code == 403, path
    assert _get(ctx, three[0])["party"] == "TDP"


def test_bulk_web_posts_need_the_csrf_token(ctx, three):
    _web_login(ctx)

    response = ctx.client.post("/voters/bulk-delete", data={"voter_ids": three, "confirm": "yes"})

    assert response.status_code == 403
    assert _get(ctx, three[0])["first_name"] == "Testperson"


# --- checkbox and action-bar markup ------------------------------------------------------


def test_admin_voters_page_has_checkbox_column_and_action_bar(ctx, three):
    _web_login(ctx)

    html = ctx.client.get("/voters").text

    assert html.count("data-row-select") == 3
    assert "data-select-all" in html
    assert "data-bulk-bar hidden" in html  # hidden until something is checked
    for text in ("selected", "Bulk edit", "Bulk delete", "Clear"):
        assert text in html
    # select-all column is first in the header and each row, ahead of Booth
    assert re.search(r'<tr>\s*<th class="select-cell">.*?</th>\s*<th>Booth</th>', html, re.S)
    assert '<div class="table-wrap" data-top-scroll>' in html


def test_regular_user_voters_page_has_no_checkboxes_or_action_bar(ctx, three):
    make_user(ctx, "plain.user", "plain-pass-123")
    _web_login(ctx, "plain.user", "plain-pass-123")

    html = ctx.client.get("/voters").text

    assert "Testperson" in html
    for marker in ("data-row-select", "data-select-all", "data-bulk", "Bulk edit", "Bulk delete"):
        assert marker not in html


def test_admin_master_data_tables_have_checkboxes_but_profession_does_not(ctx, three):
    _web_login(ctx)

    caste = ctx.client.get("/master-data?tab=caste").text
    profession = ctx.client.get("/master-data?tab=profession").text

    assert "data-row-select" in caste and "Bulk edit" in caste and "Bulk delete" in caste
    assert '<div class="table-scroll" data-top-scroll>' in caste
    for marker in ("data-row-select", "data-select-all", "data-bulk-bar", "Bulk edit"):
        assert marker not in profession


def test_regular_user_master_data_has_no_bulk_markup(ctx, three):
    make_user(ctx, "plain.user", "plain-pass-123")
    _web_login(ctx, "plain.user", "plain-pass-123")

    response = ctx.client.get("/master-data?tab=caste")

    assert response.status_code == 403
    for marker in ("data-row-select", "data-select-all", "data-bulk", "Bulk edit"):
        assert marker not in response.text


def test_select_all_script_only_touches_rendered_rows_and_clears_on_load():
    script = (STATIC / "app.js").read_text(encoding="utf-8")

    assert 'querySelectorAll("[data-row-select]")' in script  # only rendered rows
    assert "setAll(false)" in script  # selection starts empty after a filter/page reload
    assert "data-bulk-clear" in script
    assert "bar.hidden = picked === 0" in script
    assert "ResizeObserver" in script and "observer.observe(table)" in script  # top scroll kept


# --- web two-step flows ------------------------------------------------------------------


def test_web_bulk_delete_shows_count_first_and_cancel_deletes_nothing(ctx, three):
    token = _web_login(ctx)

    confirm_page = ctx.client.post(
        "/voters/bulk-delete", data={"csrf_token": token, "voter_ids": three[:2]}
    )

    assert confirm_page.status_code == 200
    assert "Remove 2 voters?" in confirm_page.text
    assert 'name="confirm" value="yes"' in confirm_page.text
    assert 'href="/voters">Cancel' in confirm_page.text  # cancel is a plain link: no write
    for voter_id in three:
        assert _get(ctx, voter_id)["first_name"] == "Testperson"


def test_web_bulk_delete_runs_only_after_confirm(ctx, three):
    token = _web_login(ctx)

    done = ctx.client.post(
        "/voters/bulk-delete",
        data={"csrf_token": token, "voter_ids": three[:2], "confirm": "yes"},
        follow_redirects=False,
    )

    assert done.status_code == 303
    assert done.headers["location"] == "/voters?bulk=deleted&n=2"
    assert ctx.client.get(f"/api/voters/{three[0]}", headers=ctx.auth).status_code == 404
    assert _get(ctx, three[2])["first_name"] == "Testperson"


def test_web_bulk_edit_two_step_applies_chosen_fields(ctx, three):
    token = _web_login(ctx)

    chooser = ctx.client.post(
        "/voters/bulk-edit", data={"csrf_token": token, "voter_ids": three[:2]}
    )
    assert chooser.status_code == 200
    assert "Bulk edit 2 voters" in chooser.text
    assert 'name="use_voter_id"' not in chooser.text
    for hidden in ("created_at", "updated_at", "revision", "deleted_at", "source_id"):
        assert f'name="use_{hidden}"' not in chooser.text
    assert _get(ctx, three[0])["party"] == "TDP"

    applied = ctx.client.post(
        "/voters/bulk-edit",
        data={
            "csrf_token": token,
            "voter_ids": three[:2],
            "apply": "1",
            "use_party": "1",
            "value_party": "INC",
            "use_is_local": "1",
            "value_is_local": "",
        },
        follow_redirects=False,
    )

    assert applied.status_code == 303
    assert _get(ctx, three[0])["party"] == "INC"
    assert _get(ctx, three[0])["is_local"] is True
    assert _get(ctx, three[2])["party"] == "TDP"


def test_web_bulk_edit_validation_error_applies_none(ctx, three):
    token = _web_login(ctx)

    response = ctx.client.post(
        "/voters/bulk-edit",
        data={
            "csrf_token": token,
            "voter_ids": three,
            "apply": "1",
            "use_party": "1",
            "value_party": "Nope",
        },
    )

    assert response.status_code == 422
    assert "Party" in response.text
    assert _get(ctx, three[0])["party"] == "TDP"


# --- master data -------------------------------------------------------------------------


def _master_rename(ctx, entity, items, new_value):
    return ctx.client.post(
        f"/api/master-data/{entity}/rename",
        headers=ctx.auth,
        json={"items": items, "new_value": new_value},
    )


def _master_delete(ctx, entity, items):
    return ctx.client.post(
        f"/api/master-data/{entity}/delete", headers=ctx.auth, json={"items": items}
    )


def test_master_rename_updates_matching_voters_only(ctx):
    x1 = _add(ctx, 10, caste="X")
    x2 = _add(ctx, 11, caste="X")
    other = _add(ctx, 12, caste="Z")
    before_other = _get(ctx, other)
    before_x1 = _get(ctx, x1)

    response = _master_rename(ctx, "caste", [{"caste": "X"}], "Y")

    assert response.status_code == 200, response.text
    assert response.json()["updated"] == 2
    assert _get(ctx, x1)["caste"] == _get(ctx, x2)["caste"] == "Y"
    assert _get(ctx, x1)["mobile"] == before_x1["mobile"]  # no other field rewritten
    assert _get(ctx, x1)["revision"] > before_x1["revision"]
    assert _get(ctx, other) == before_other


def test_master_rename_merges_several_selected_values_into_one(ctx):
    a = _add(ctx, 13, caste="A")
    b = _add(ctx, 14, caste="B")
    c = _add(ctx, 15, caste="C")

    assert _master_rename(ctx, "caste", [{"caste": "A"}, {"caste": "B"}], "AB").status_code == 200

    assert (_get(ctx, a)["caste"], _get(ctx, b)["caste"], _get(ctx, c)["caste"]) == ("AB", "AB", "C")


def test_master_rename_blank_row_only_changes_blank_values(ctx):
    blank = _add(ctx, 16, caste="")
    real = _add(ctx, 17, caste="Real")

    response = _master_rename(ctx, "caste", [{"caste": "(blank)"}], "Filled")

    assert response.status_code == 200
    assert _get(ctx, blank)["caste"] == "Filled"
    assert _get(ctx, real)["caste"] == "Real"


def test_master_rename_composite_row_matches_companion_key_but_edits_primary_only(ctx):
    ward3 = _add(ctx, 18, street="Main", ward="3")
    ward4 = _add(ctx, 19, street="Main", ward="4")

    response = _master_rename(ctx, "street", [{"street": "Main", "ward": "3"}], "High")

    assert response.status_code == 200
    assert (_get(ctx, ward3)["street"], _get(ctx, ward3)["ward"]) == ("High", "3")
    assert (_get(ctx, ward4)["street"], _get(ctx, ward4)["ward"]) == ("Main", "4")


def test_master_rename_validates_like_voter_fields(ctx):
    voter_id = _add(ctx, 20, caste="X")

    too_long = _master_rename(ctx, "caste", [{"caste": "X"}], "L" * 129)
    blank = _master_rename(ctx, "caste", [{"caste": "X"}], "  ")

    assert too_long.status_code == 422 and blank.status_code == 422
    assert _get(ctx, voter_id)["caste"] == "X"


def test_master_rename_rejects_bad_rows_unknown_entity_and_profession(ctx):
    _add(ctx, 21, caste="X")

    assert _master_rename(ctx, "caste", [{"nope": "X"}], "Y").status_code == 422
    assert _master_rename(ctx, "caste", [{"caste": "Missing"}], "Y").status_code == 404
    assert _master_rename(ctx, "caste", [], "Y").status_code == 422
    assert _master_rename(ctx, "bogus", [{"caste": "X"}], "Y").status_code == 404
    assert _master_rename(ctx, "profession", [{"profession": "X"}], "Y").status_code == 409


def test_master_delete_is_blocked_while_voters_use_the_value(ctx):
    a = _add(ctx, 22, caste="Used")
    b = _add(ctx, 23, caste="Used")
    before = [_get(ctx, a), _get(ctx, b)]

    response = _master_delete(ctx, "caste", [{"caste": "Used"}])

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "2 active voters" in detail and "Used" in detail
    assert [_get(ctx, a), _get(ctx, b)] == before  # nothing cleared or changed


def test_master_delete_of_an_unused_value_is_a_noop(ctx):
    voter_id = _add(ctx, 24, caste="Kept")

    response = _master_delete(ctx, "caste", [{"caste": "NobodyHasThis"}])

    assert response.status_code == 200
    assert response.json() == {"in_use": 0, "unused": 1}
    assert _get(ctx, voter_id)["caste"] == "Kept"


def test_master_delete_blocks_whole_request_if_any_value_is_in_use(ctx):
    voter_id = _add(ctx, 25, caste="Used")

    response = _master_delete(ctx, "caste", [{"caste": "NobodyHasThis"}, {"caste": "Used"}])

    assert response.status_code == 409
    assert _get(ctx, voter_id)["caste"] == "Used"


def test_master_delete_ignores_soft_deleted_voters(ctx):
    voter_id = _add(ctx, 26, caste="Gone")
    assert _bulk_delete(ctx, [voter_id], confirm=True).status_code == 200

    assert _master_delete(ctx, "caste", [{"caste": "Gone"}]).status_code == 200


def test_web_master_delete_shows_in_use_warning_with_count(ctx):
    voter_id = _add(ctx, 27, caste="Used")
    token = _web_login(ctx)

    response = ctx.client.post(
        "/master-data/bulk-delete",
        data={"csrf_token": token, "tab": "caste", "rows": ['{"caste": "Used"}']},
    )

    assert response.status_code == 409
    assert "Cannot delete: 1 active voter still use" in response.text
    assert _get(ctx, voter_id)["caste"] == "Used"


def test_web_master_rename_two_step(ctx):
    voter_id = _add(ctx, 28, caste="X")
    token = _web_login(ctx)
    rows = ['{"caste": "X"}']

    page = ctx.client.post(
        "/master-data/bulk-edit", data={"csrf_token": token, "tab": "caste", "rows": rows}
    )
    assert page.status_code == 200 and "1 voter" in page.text
    assert _get(ctx, voter_id)["caste"] == "X"

    done = ctx.client.post(
        "/master-data/bulk-edit",
        data={"csrf_token": token, "tab": "caste", "rows": rows, "apply": "1", "new_value": "Y"},
        follow_redirects=False,
    )

    assert done.status_code == 303
    assert _get(ctx, voter_id)["caste"] == "Y"
