"""Top horizontal scrollbar hooks on wide tables. Synthetic fixtures only."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import voter_payload

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


@pytest.fixture
def seeded(ctx):
    response = ctx.client.post(
        "/api/voters",
        headers=ctx.auth,
        json=voter_payload(voter_id="ZZZ3000001", mobile="9000000001"),
    )
    assert response.status_code == 201, response.text
    return ctx


@pytest.mark.parametrize(
    ("path", "wrapper_class"),
    [
        ("/voters", 'class="table-wrap"'),
        ("/reports", 'class="table-wrap report-table-wrap"'),
        ("/master-data", 'class="table-scroll"'),
    ],
)
def test_wide_table_wrapper_carries_the_top_scroll_hook(seeded, path, wrapper_class):
    html = seeded.client.get(path).text

    assert f"<div {wrapper_class} data-top-scroll>" in html


def test_app_js_is_served_and_has_sync_logic(seeded):
    response = seeded.client.get("/static/app.js")

    assert response.status_code == 200
    script = response.text
    assert "[data-top-scroll]" in script
    assert "ResizeObserver" in script
    assert 'addEventListener("resize"' in script
    assert "scrollLeft" in script
    assert "track.hidden = !overflows" in script


def test_base_template_loads_app_js(seeded):
    assert '<script src="/static/app.js"></script>' in seeded.client.get("/voters").text


def test_css_constrains_top_track_and_hides_it_when_unused():
    css = (STATIC / "styles.css").read_text(encoding="utf-8")

    track_rule = css.split(".top-scroll {")[1].split("}")[0]
    assert "overflow-x: auto" in track_rule
    assert "max-width: 100%" in track_rule
    assert "min-width: 0" in track_rule
    assert ".top-scroll[hidden] { display: none; }" in css
