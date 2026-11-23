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
