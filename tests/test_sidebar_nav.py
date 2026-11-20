"""Sidebar navigation: sections, role visibility, and server-side gating. Synthetic users only."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.web.nav import COMING_SOON_PAGES, active_path
from tests.conftest import csrf_from_page, make_user

SECTION_TITLES = ["Overview", "Voters", "Campaign", "Admin"]
SOON_PATHS = [
    "/dashboard",
    "/slips",
    "/campaign/sms",
    "/campaign/whatsapp",
    "/campaign/voice-calls",
    "/campaign/projection",
    "/campaign/activities",
    "/campaign/public-issues",
    "/survey-report",
]
ADMIN_ONLY_PATHS = ["/master-data", "/survey-report", "/import"]


def _web_login(ctx, username: str, password: str) -> None:
    ctx.client.cookies.clear()
    response = ctx.client.post(
        "/login", data={"username": username, "password": password}, follow_redirects=False
    )
    assert response.status_code == 303, response.text


def _sidebar(html: str) -> str:
    match = re.search(r"<aside class=\"sidebar\".*?</aside>", html, re.S)
    assert match, "sidebar missing"
    return match.group(0)


def _hrefs(sidebar: str) -> set[str]:
    return set(re.findall(r'href="([^"]+)"', sidebar))


def _titles(sidebar: str) -> list[str]:
    return re.findall(r'class="sidebar-title">([^<]+)<', sidebar)


def _as_clerk(ctx):
    make_user(ctx, "clerk.one", "clerk-pass-123", role="user")
    _web_login(ctx, "clerk.one", "clerk-pass-123")


def _as_non_owner_admin(ctx):
    make_user(ctx, "admin.two", "admin2-pass-123", role="admin")
    _web_login(ctx, "admin.two", "admin2-pass-123")


def test_stub_paths_match_the_nav_model():
    assert sorted(COMING_SOON_PAGES) == sorted(SOON_PATHS)


def test_top_nav_row_is_gone_and_header_keeps_brand_and_user(ctx):
    _web_login(ctx, "admin", "admin-pass-123")

    html = ctx.client.get("/voters").text

    header = re.search(r"<header.*?</header>", html, re.S).group(0)
    assert "<nav" not in header
    assert "Nathavaram" in header
    assert "admin · admin" in header


def test_owner_sees_all_sections_in_order_with_every_link(ctx):
    _web_login(ctx, "admin", "admin-pass-123")

    sidebar = _sidebar(ctx.client.get("/voters").text)

    assert _titles(sidebar) == SECTION_TITLES
    expected = {"/voters", "/reports", "/master-data", "/import", "/users", "/change-password"}
    assert expected | set(SOON_PATHS) <= _hrefs(sidebar)
    assert 'action="/logout"' in sidebar
    assert "Log out" in sidebar
    assert "Coming soon" in sidebar


def test_regular_user_does_not_see_admin_links(ctx):
    _as_clerk(ctx)

    sidebar = _sidebar(ctx.client.get("/voters").text)

    hrefs = _hrefs(sidebar)
    assert not hrefs & set(ADMIN_ONLY_PATHS + ["/users"])
    assert {"/voters", "/reports", "/change-password", "/dashboard", "/slips"} <= hrefs
    assert 'action="/logout"' in sidebar
    assert _titles(sidebar) == SECTION_TITLES


def test_non_owner_admin_sees_admin_links_but_not_users(ctx):
    _as_non_owner_admin(ctx)

    hrefs = _hrefs(_sidebar(ctx.client.get("/voters").text))

    assert set(ADMIN_ONLY_PATHS) <= hrefs
    assert "/users" not in hrefs


@pytest.mark.parametrize("path", ADMIN_ONLY_PATHS + ["/users"])
def test_admin_routes_return_403_for_regular_user(ctx, path):
    _as_clerk(ctx)

    response = ctx.client.get(path, follow_redirects=False)

    assert response.status_code == 403


def test_users_route_is_403_for_non_owner_admin(ctx):
    _as_non_owner_admin(ctx)

    assert ctx.client.get("/users", follow_redirects=False).status_code == 403
    assert ctx.client.get("/master-data").status_code == 200
    assert ctx.client.get("/import").status_code == 200


@pytest.mark.parametrize("path", SOON_PATHS)
def test_coming_soon_pages_render_for_owner(ctx, path):
    _web_login(ctx, "admin", "admin-pass-123")

    response = ctx.client.get(path)

    assert response.status_code == 200
    assert "Coming soon" in response.text
    assert f'href="{path}" aria-current="page"' in response.text


@pytest.mark.parametrize("path", [p for p in SOON_PATHS if p != "/survey-report"])
def test_open_coming_soon_pages_render_for_regular_user(ctx, path):
    _as_clerk(ctx)

    assert ctx.client.get(path).status_code == 200


@pytest.mark.parametrize("path", SOON_PATHS)
def test_coming_soon_pages_require_login(ctx, path):
    ctx.client.cookies.clear()

    response = ctx.client.get(path, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_sidebar_logout_signs_the_user_out(ctx):
    _web_login(ctx, "admin", "admin-pass-123")
    token = csrf_from_page(ctx.client.get("/voters").text)

    out = ctx.client.post("/logout", data={"csrf_token": token}, follow_redirects=False)

    assert out.status_code == 303
    assert ctx.client.get("/voters", follow_redirects=False).status_code == 303


def test_login_page_has_no_sidebar(ctx):
    ctx.client.cookies.clear()

    html = ctx.client.get("/login").text

    assert '<aside class="sidebar"' not in html


def test_empty_voter_list_shows_import_hint_to_admin(ctx):
    _web_login(ctx, "admin", "admin-pass-123")

    page = ctx.client.get("/voters")

    assert page.status_code == 200
    assert "Import page" in page.text


def test_empty_voter_list_hides_import_hint_from_regular_user(ctx):
    _as_clerk(ctx)

    page = ctx.client.get("/voters")

    assert page.status_code == 200
    assert "No voters match your search" in page.text
    assert "Import page" not in page.text


# --- mobile drawer, polish, active-path ------------------------------------------

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def _css() -> str:
    return (STATIC / "styles.css").read_text(encoding="utf-8")


def _current(html: str) -> list[str]:
    """aria-current hrefs in the left sidebar only (not page-level tab strips)."""
    return re.findall(r'href="([^"]+)" aria-current="page"', _sidebar(html))


def test_mobile_drawer_markup_is_collapsed_with_accessible_toggle(ctx):
    _web_login(ctx, "admin", "admin-pass-123")

    html = ctx.client.get("/voters").text

    assert 'class="shell has-sidebar nav-collapsed"' in html
    button = re.search(r"<button[^>]*data-menu-toggle[^>]*>", html).group(0)
    assert 'aria-expanded="false"' in button
    assert 'aria-controls="sidebar"' in button
    assert 'aria-label="Open menu"' in button
    assert "Menu" in html
    assert 'id="sidebar"' in _sidebar(html)
    assert "nav-open" not in html.split("<main", 1)[0]
    assert "<noscript><style>" in html


def test_drawer_css_collapses_on_mobile_and_is_static_on_desktop():
    css = _css()

    assert re.search(r"\.shell\.has-sidebar \.sidebar \{[^}]*translateX\(-100%\)", css)
    assert ".shell.has-sidebar.nav-open .sidebar" in css
    desktop = css.split("@media (min-width: 900px)", 1)[1]
    assert "position: static" in desktop
    assert ".menu-toggle" in desktop


def test_app_js_toggles_aria_expanded_and_closes_on_backdrop_and_link():
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    assert "aria-expanded" in js
    assert "data-menu-backdrop" in js
    assert 'closest("a")' in js


def test_logout_left_alignment_is_scoped_to_sidebar():
    css = _css()

    rule = re.search(r"\.sidebar button\.sidebar-link \{([^}]*)\}", css).group(1)
    assert "justify-content: flex-start" in rule
    assert "text-align: left" in rule
    assert re.search(r"^\.button, button \{[^}]*justify-content: center", css, re.M | re.S)


def test_nav_labels_and_badges_do_not_wrap():
    css = _css()

    assert re.search(r"\.sidebar-label \{[^}]*white-space: nowrap", css)
    assert re.search(r"\.soon-badge \{[^}]*white-space: nowrap", css)


def test_rendered_labels_are_wrapped_for_nowrap(ctx):
    _web_login(ctx, "admin", "admin-pass-123")

    sidebar = _sidebar(ctx.client.get("/voters").text)

    assert '<span class="sidebar-label">Change password</span>' in sidebar
    assert 'class="soon-badge">Coming soon</span>' in sidebar


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/voters", "/voters"),
        ("/voters/abc123/edit", "/voters"),
        ("/voters/new", "/voters"),
        ("/reports", "/reports"),
        ("/master-data", "/master-data"),
        ("/master-data/cities", "/master-data"),
        ("/users", "/users"),
        ("/import", "/import"),
        ("/dashboard", "/dashboard"),
        ("/campaign/sms", "/campaign/sms"),
        ("/votersX", None),
        ("/", None),
        ("/unknown", None),
    ],
)
def test_active_path_uses_longest_prefix_without_loose_matches(path, expected):
    assert active_path(path) == expected


def test_voters_page_highlights_voters_list_only(ctx):
    _web_login(ctx, "admin", "admin-pass-123")

    assert _current(ctx.client.get("/voters").text) == ["/voters"]


@pytest.mark.parametrize("path", ["/import", "/reports", "/master-data", "/users"])
def test_other_pages_highlight_only_their_own_item(ctx, path):
    _web_login(ctx, "admin", "admin-pass-123")

    assert _current(ctx.client.get(path).text) == [path]


# --- reports overflow at 412px, master-data active tab ---------------------------


def _rule(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match, f"CSS rule {selector!r} missing"
    return match.group(1)


def test_report_tables_sit_inside_a_horizontal_scroll_box(ctx):
    _web_login(ctx, "admin", "admin-pass-123")

    html = ctx.client.get("/reports").text

    blocks = re.findall(r'<section class="report-block">(.*?)</section>', html, re.S)
    assert len(blocks) == 9
    for block in blocks:
        assert re.search(r'<div class="table-wrap[^"]*">\s*<table>', block)
    assert re.search(r"\.table-wrap \{[^}]*overflow-x: auto", _css())


def test_report_layout_cannot_force_a_wider_than_viewport_page():
    css = _css()

    assert "minmax(0, 1fr)" in _rule(css, ".shell.has-sidebar")
    assert "min-width: 0" in _rule(css, ".shell.has-sidebar main")
    assert "minmax(0, 1fr)" in _rule(css, ".report-grid")
    assert "min-width: 0" in _rule(css, ".report-block")
    # the desktop two-column grid must also let columns shrink below table width
    assert "repeat(2, minmax(0, 1fr))" in css


def test_sidebar_master_data_highlighted_with_tab_query(ctx):
    _web_login(ctx, "admin", "admin-pass-123")

    html = ctx.client.get("/master-data?tab=constituency").text

    assert _current(html) == ["/master-data"]


def test_master_data_marks_only_the_selected_tab_current(ctx):
    _web_login(ctx, "admin", "admin-pass-123")

    html = ctx.client.get("/master-data").text

    nav = re.search(r'<nav class="tabs".*?</nav>', html, re.S).group(0)
    assert nav.count('aria-current="page"') == 1
    assert len(re.findall(r"<a href=", nav)) > 1


def test_active_tab_css_is_distinct_from_inactive_and_hover():
    css = _css()
    inactive = _rule(css, ".tabs a")
    hover = _rule(css, ".tabs a:hover")
    active = re.search(
        r'\.tabs a\[aria-current="page"\],\s*\.tabs a\[aria-current="page"\]:hover \{([^}]*)\}', css
    )
    assert active, "active tab rule missing"

    assert active.group(1).strip() not in (inactive.strip(), hover.strip())
    assert "background: var(--green)" in active.group(1)
    assert "color: #fff" in active.group(1)
    assert "border-bottom-color" in active.group(1)
    assert "font-weight: 700" in active.group(1)
    assert "background" not in inactive
