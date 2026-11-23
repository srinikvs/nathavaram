"""Round trip of the saved list context (filters, page, scroll) used by Back. Synthetic only.

The logic lives in app/static/voter-list-context.js. Node runs it here, so no browser is needed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "app" / "static" / "voter-list-context.js"
NODE = shutil.which("node")

# Each step prints one JSON object so the assertions below read plain data.
ROUND_TRIP = """
const api = require(process.argv[1]);
const filters = api.filtersFromEntries([
  ["csrf_token", "not-a-filter"], ["mobile", "9000000021"], ["q", ""],
  ["voter_id", "ZZZ50000001"], ["is_local", "Local"],
]);
const saved = Object.assign(api.newContext(filters, 3, 420), { pending: true });
const text = api.encodeContext(saved);
const restored = api.decodeContext(text);
const plan = api.restorePlan(restored);
const oneShot = api.restorePlan(Object.assign({}, restored, { pending: false }));
const scrollOnly = api.restorePlan(Object.assign(api.newContext({}, 1, 77), { pending: true }));
console.log(JSON.stringify({
  filters: filters,
  restored: restored,
  plan: plan,
  oneShot: oneShot,
  scrollOnly: scrollOnly,
  notJson: api.decodeContext("{not json"),
  wrongShape: api.decodeContext('{"filters": "x"}'),
  nothing: api.decodeContext(null),
  clamped: api.decodeContext('{"filters": {}, "page": -4, "scrollY": "oops", "pending": true}'),
  storageKey: api.STORAGE_KEY,
}));
"""


def _run_round_trip() -> dict:
    result = subprocess.run(
        [NODE, "-e", ROUND_TRIP, str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@needs_node
def test_filters_and_page_and_scroll_survive_a_save_and_restore():
    data = _run_round_trip()

    assert data["restored"]["filters"] == {"mobile": "9000000021", "voter_id": "ZZZ50000001", "is_local": "Local"}
    assert data["restored"]["page"] == 3
    assert data["restored"]["scrollY"] == 420
    assert data["restored"]["pending"] is True


@needs_node
def test_restore_submits_saved_filters_once_and_then_stops():
    data = _run_round_trip()

    assert data["plan"]["type"] == "submit"
    assert data["plan"]["filters"] == data["restored"]["filters"]
    assert data["plan"]["page"] == 3 and data["plan"]["scrollY"] == 420
    assert data["oneShot"] == {"type": "none"}  # not pending any more, so no Back loop


@needs_node
def test_csrf_and_blank_fields_are_never_saved_as_filters():
    data = _run_round_trip()

    assert "csrf_token" not in data["filters"]
    assert "q" not in data["filters"]


@needs_node
def test_with_no_filters_back_only_restores_scroll():
    data = _run_round_trip()

    assert data["scrollOnly"] == {"type": "scroll", "y": 77}


@needs_node
def test_garbage_or_foreign_storage_is_ignored_and_values_are_clamped():
    data = _run_round_trip()

    assert data["notJson"] is None
    assert data["wrongShape"] is None
    assert data["nothing"] is None
    assert data["clamped"] == {
        "filters": {},
        "page": 1,
        "scrollY": 0,
        "pending": True,
        "restoreScrollY": None,
    }


@needs_node
def test_storage_key_is_namespaced():
    assert _run_round_trip()["storageKey"] == "nathavaram.voterList"


# ---- Browser Back against the real app.js and voter-list-context.js (fake DOM, synthetic data) ----

HARNESS = Path(__file__).resolve().parent / "js" / "fake_dom.js"
FILTER_JS = "{mobile: '9000000021', is_local: 'Local'}"


def _run_page_script(body: str) -> dict:
    """Runs a Node script that drives the fake DOM and prints one JSON object."""
    script = "const h = require(process.argv[1]);\nconst out = {};\n" + body + "\nconsole.log(JSON.stringify(out));\n"
    result = subprocess.run(
        [NODE, "-e", script, str(HARNESS)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@needs_node
def test_voter_id_click_records_pending_and_context_even_though_link_stops_propagation():
    data = _run_page_script(
        f"""
        const store = new Map();
        const tree = h.listBody({{ filters: {FILTER_JS}, page: 3 }});
        const page = h.openPage(tree, {{ path: '/voters/search', store }});
        page.win.scrollY = 420;
        const event = h.click(page, tree.link);
        out.stopped = event.stopped;
        out.saved = h.saved(store);
        """
    )

    assert data["stopped"] is True  # app.js still stops the VoterId click from reaching the row
    assert data["saved"]["pending"] is True
    assert data["saved"]["filters"] == {"mobile": "9000000021", "is_local": "Local"}
    assert data["saved"]["page"] == 3
    assert data["saved"]["scrollY"] == 420


@needs_node
def test_voter_id_click_does_not_toggle_row_checkbox_or_run_row_action():
    data = _run_page_script(
        """
        const store = new Map();
        const tree = h.listBody({ filters: { mobile: '9000000021' }, page: 1 });
        const page = h.openPage(tree, { path: '/voters/search', store });
        tree.checkbox.checked = true;
        h.click(page, tree.link);
        out.checked = tree.checkbox.checked;
        out.rowClicks = tree.rowClicks.length;
        out.barHidden = tree.bar.hidden;
        """
    )

    assert data["checked"] is True  # the link click leaves the row checkbox as it was
    assert data["rowClicks"] == 0
    assert data["barHidden"] is True


@needs_node
def test_filtered_search_result_is_rewritten_to_get_and_back_reapplies_filters_once():
    data = _run_page_script(
        f"""
        const store = new Map();
        const first = h.openPage(h.listBody({{ filters: {FILTER_JS}, page: 3 }}), {{ path: '/voters/search', store }});
        first.win.scrollY = 420;
        h.click(first, first.tree.link);
        out.rewrittenFirst = first.win.location.pathname;

        // Browser Back lands on a fresh GET /voters with no filters.
        const back = h.openPage(h.listBody({{}}), {{ path: '/voters', store }});
        out.postedOnBack = back.tree.form.submitted || [];
        out.pendingBeforeResubmit = h.saved(store).pending;

        // The re-posted search loads at /voters/search. It restores scroll and must not re-post again.
        const result = h.openPage(h.listBody({{ filters: {FILTER_JS}, page: 3 }}), {{ path: '/voters/search', store }});
        out.resultSubmits = (result.tree.form.submitted || []).length;
        out.resultScroll = result.win.scrollCalls;
        out.resultPath = result.win.location.pathname;
        out.resultSaved = h.saved(store);
        """
    )

    assert data["rewrittenFirst"] == "/voters"
    assert len(data["postedOnBack"]) == 1
    posted = dict(data["postedOnBack"][0])
    assert posted["mobile"] == "9000000021"
    assert posted["is_local"] == "Local"
    assert posted["page"] == "3"
    assert data["pendingBeforeResubmit"] is False
    assert data["resultSubmits"] == 0
    assert 420 in data["resultScroll"]
    assert data["resultPath"] == "/voters"
    assert data["resultSaved"]["pending"] is False


@needs_node
def test_back_to_list_button_on_details_still_marks_leaving_with_filters_kept():
    data = _run_page_script(
        f"""
        const store = new Map();
        h.openPage(h.listBody({{ filters: {FILTER_JS}, page: 2 }}), {{ path: '/voters/search', store }});
        const details = h.openPage(h.detailsBody(), {{ path: '/voters/ZZZ50000001', store }});
        h.click(details, details.tree.back);
        out.saved = h.saved(store);
        """
    )

    assert data["saved"]["pending"] is True
    assert data["saved"]["filters"] == {"mobile": "9000000021", "is_local": "Local"}
    assert data["saved"]["page"] == 2


@needs_node
def test_bfcache_pageshow_keeps_filtered_view_and_does_not_repost():
    data = _run_page_script(
        """
        const store = new Map();
        const tree = h.listBody({ filters: { mobile: '9000000021' }, page: 1 });
        const page = h.openPage(tree, { path: '/voters/search', store });
        page.win.scrollY = 300;
        h.click(page, tree.link);
        h.windowEvent(page, 'pagehide', {});
        page.win.scrollY = 0;
        h.windowEvent(page, 'pageshow', { persisted: true });
        out.submits = (tree.form.submitted || []).length;
        out.mobile = tree.form.elements.namedItem('mobile').value;
        out.scrollCalls = page.win.scrollCalls;
        out.saved = h.saved(store);
        """
    )

    assert data["submits"] == 0  # the restored page is not re-posted
    assert data["mobile"] == "9000000021"  # the filtered view stays in the form
    assert data["scrollCalls"] == [300]
    assert data["saved"]["pending"] is False  # a later fresh load does not re-post either


@needs_node
def test_pagehide_saves_scroll_without_setting_pending_or_taking_unsubmitted_typing():
    data = _run_page_script(
        """
        const store = new Map();
        const tree = h.listBody({ filters: { mobile: '9000000021' }, page: 1 });
        const page = h.openPage(tree, { path: '/voters/search', store });
        tree.form.elements.namedItem('mobile').value = '9000000022';
        page.win.scrollY = 200;
        h.windowEvent(page, 'pagehide', {});
        out.saved = h.saved(store);
        """
    )

    assert data["saved"]["filters"] == {"mobile": "9000000021"}  # the rendered search, not typing
    assert data["saved"]["scrollY"] == 200
    assert data["saved"]["pending"] is False


@needs_node
def test_pagehide_after_voter_click_keeps_pending_true():
    data = _run_page_script(
        """
        const store = new Map();
        const tree = h.listBody({ filters: { mobile: '9000000021' }, page: 1 });
        const page = h.openPage(tree, { path: '/voters/search', store });
        h.click(page, tree.link);
        h.windowEvent(page, 'pagehide', {});
        out.pending = h.saved(store).pending;
        """
    )

    assert data["pending"] is True


@needs_node
def test_submitting_a_new_search_saves_it_as_page_one_and_not_pending():
    data = _run_page_script(
        """
        const store = new Map();
        const tree = h.listBody({ filters: { mobile: '9000000021' }, page: 4 });
        const page = h.openPage(tree, { path: '/voters/search', store });
        tree.form.elements.namedItem('mobile').value = '9000000023';
        h.submit(page, tree.form);
        out.saved = h.saved(store);
        """
    )

    assert data["saved"]["filters"] == {"mobile": "9000000023"}
    assert data["saved"]["page"] == 1
    assert data["saved"]["pending"] is False


@needs_node
def test_clear_forgets_the_saved_context():
    data = _run_page_script(
        """
        const store = new Map();
        const tree = h.listBody({ filters: { mobile: '9000000021' }, page: 1 });
        const page = h.openPage(tree, { path: '/voters/search', store });
        h.click(page, tree.clear);
        out.saved = h.saved(store);
        """
    )

    assert data["saved"] is None


@needs_node
def test_ctrl_click_on_voter_id_opens_elsewhere_and_is_not_marked_leaving():
    data = _run_page_script(
        """
        const store = new Map();
        const tree = h.listBody({ filters: { mobile: '9000000021' }, page: 1 });
        const page = h.openPage(tree, { path: '/voters/search', store });
        h.click(page, tree.link, { ctrlKey: true });
        out.pending = h.saved(store).pending;
        """
    )

    assert data["pending"] is False
