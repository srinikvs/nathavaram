"""Sidebar navigation model. One place decides what each role sees.

Real routes keep their own server-side role checks; hiding a link here is only
a convenience, never the access control.
"""

from __future__ import annotations

from dataclasses import dataclass

ALL = "all"
ADMIN = "admin"
OWNER = "owner"  # an admin who is also the owner account


@dataclass(frozen=True)
class NavItem:
    label: str
    path: str
    audience: str = ALL
    coming_soon: bool = False
    is_logout: bool = False


@dataclass(frozen=True)
class NavSection:
    title: str
    items: tuple[NavItem, ...]


def _soon(label: str, path: str, audience: str = ALL) -> NavItem:
    return NavItem(label, path, audience, coming_soon=True)


NAV_SECTIONS: tuple[NavSection, ...] = (
    NavSection("Overview", (_soon("Dashboard", "/dashboard"),)),
    NavSection(
        "Voters",
        (
            NavItem("Voters List", "/voters"),
            _soon("Generate Slips", "/slips"),
            NavItem("Reports", "/reports"),
        ),
    ),
    NavSection(
        "Campaign",
        (
            _soon("SMS", "/campaign/sms"),
            _soon("WhatsApp", "/campaign/whatsapp"),
            _soon("Voice Calls", "/campaign/voice-calls"),
            _soon("Projection", "/campaign/projection"),
            _soon("Activities", "/campaign/activities"),
            _soon("Public Issues", "/campaign/public-issues"),
        ),
    ),
    NavSection(
        "Admin",
        (
            NavItem("Master Data", "/master-data", ADMIN),
            _soon("Survey Report", "/survey-report", ADMIN),
            NavItem("Import", "/import", ADMIN),
            NavItem("Users", "/users", OWNER),
            NavItem("Change password", "/change-password"),
            NavItem("Log out", "/logout", is_logout=True),
        ),
    ),
)

COMING_SOON_PAGES: dict[str, NavItem] = {
    item.path: item for section in NAV_SECTIONS for item in section.items if item.coming_soon
}


def can_see(user, item: NavItem) -> bool:
    if item.audience == ALL:
        return True
    if item.audience == ADMIN:
        return user.role == "admin"
    return user.role == "admin" and bool(user.is_owner)


def active_path(current_path: str) -> str | None:
    """Href of the nav item that owns ``current_path``: exact match or a subpath
    (``/voters/12/edit`` -> ``/voters``). The longest matching href wins, so a more
    specific item beats a shorter prefix, and unrelated routes match nothing."""
    best: str | None = None
    for section in NAV_SECTIONS:
        for item in section.items:
            if item.is_logout or item.path == "/":
                continue
            owns = current_path == item.path or current_path.startswith(item.path + "/")
            if owns and (best is None or len(item.path) > len(best)):
                best = item.path
    return best


def sections_for(user) -> list[NavSection]:
    """Sections visible to this user, with empty sections dropped."""
    if user is None:
        return []
    visible = []
    for section in NAV_SECTIONS:
        items = tuple(item for item in section.items if can_see(user, item))
        if items:
            visible.append(NavSection(section.title, items))
    return visible
