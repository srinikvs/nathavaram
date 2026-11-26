"""Role rules in one place. Routes, services, sync and templates all ask these helpers.

Roles (users.role): admin, manager, user. The owner is an admin with is_owner set.
Hiding a button or link is never the access control: every API and web route calls one of
these helpers on the server.

| Capability                              | user | manager | admin |
|-----------------------------------------|------|---------|-------|
| Create voter (all fields)               |  yes |   yes   |  yes  |
| Edit USER_EDITABLE_FIELDS               |  yes |   yes   |  yes  |
| Edit every other field (not VoterId)    |  no  |   no    |  yes  |
| Soft delete (single and bulk)           |  yes |   yes   |  yes  |
| See Deleted voters, restore             |  no  |   yes   |  yes  |
| Hard delete (permanent, PII removed)    |  no  |   yes   |  yes  |
| Import, export, Master Data            |  no  |   no    |  yes  |
| Manage user accounts                    |  no  |   no    |  yes  |
"""

from __future__ import annotations

from app.constants import ROLE_ADMIN, ROLE_MANAGER, ROLE_USER

SOFT_DELETE_ROLES: frozenset[str] = frozenset({ROLE_USER, ROLE_MANAGER, ROLE_ADMIN})
REVIEW_ROLES: frozenset[str] = frozenset({ROLE_MANAGER, ROLE_ADMIN})


def _role(user) -> str:
    return getattr(user, "role", "")


def is_admin(user) -> bool:
    return _role(user) == ROLE_ADMIN


def is_owner(user) -> bool:
    return is_admin(user) and bool(getattr(user, "is_owner", False))


def can_edit_all_fields(user) -> bool:
    """Admin (and so the owner) may edit every voter field except VoterId."""
    return is_admin(user)


def can_soft_delete(user) -> bool:
    return _role(user) in SOFT_DELETE_ROLES


def can_review_deleted(user) -> bool:
    return _role(user) in REVIEW_ROLES


def can_restore(user) -> bool:
    return _role(user) in REVIEW_ROLES


def can_hard_delete(user) -> bool:
    return _role(user) in REVIEW_ROLES


def can_import(user) -> bool:
    return is_admin(user)


def can_use_master_data(user) -> bool:
    return is_admin(user)


def can_manage_accounts(user) -> bool:
    return is_admin(user)


def can_manage_issues(user) -> bool:
    """Manager or admin: any Public Issue status, delete an issue, remove a photo."""
    return can_review_deleted(user)


def can_manage_campaigns(user) -> bool:
    """Manager or admin: create, schedule, pause/resume, cancel and delete campaigns."""
    return can_review_deleted(user)


def can_delete_issue_note(user) -> bool:
    """Only an admin may delete a note on a Public Issue."""
    return is_admin(user)


def permission_flags(user) -> dict[str, bool]:
    """Booleans for templates. Each one is a convenience; the routes enforce the same rule."""
    if user is None:
        return {}
    return {
        "admin": is_admin(user),
        "owner": is_owner(user),
        "edit_all": can_edit_all_fields(user),
        "soft_delete": can_soft_delete(user),
        "review_deleted": can_review_deleted(user),
        "restore": can_restore(user),
        "hard_delete": can_hard_delete(user),
        "import": can_import(user),
        "master_data": can_use_master_data(user),
        "accounts": can_manage_accounts(user),
        "campaigns_manage": can_manage_campaigns(user),
    }
