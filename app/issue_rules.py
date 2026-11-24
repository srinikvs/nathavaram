"""Public Issues rules that need no database: statuses, transitions, and limits.

The service layer calls check_transition before every status change. The web and JSON
routes never decide a transition on their own, and status_options only shapes the dropdown.
"""

from __future__ import annotations

from app.errors import ServiceError

ISSUE_STATUSES: tuple[str, ...] = ("Initiated", "In Process", "Resolved", "Rejected")
INITIAL_STATUS = "Initiated"

# The only status change a regular user may make. Every other move needs a manager or admin.
USER_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({("Initiated", "In Process")})

MAX_PHOTOS_PER_ISSUE = 5
NAME_MAX_LENGTH = 128
SUBJECT_MAX_LENGTH = 200
ISSUE_MAX_LENGTH = 4000
NOTE_MAX_LENGTH = 1000

NOTE_KIND = "note"
SYSTEM_KIND = "system"

USER_TRANSITION_REFUSED_MESSAGE = (
    "Your role can only move an issue from Initiated to In Process. "
    "A manager or admin sets Resolved, Rejected, or reopens an issue."
)


def check_transition(can_manage: bool, current: str, target: str) -> None:
    """Raise ServiceError unless `current` -> `target` is allowed for this caller."""
    if target not in ISSUE_STATUSES:
        raise ServiceError(f"Status must be one of: {', '.join(ISSUE_STATUSES)}", 422, "status")
    if target == current:
        raise ServiceError(f"The issue is already {current}", 409, "status")
    if can_manage or (current, target) in USER_TRANSITIONS:
        return
    raise ServiceError(USER_TRANSITION_REFUSED_MESSAGE, 403, "status")


def status_options(can_manage: bool, current: str) -> list[str]:
    """Statuses to offer in a dropdown: the current one first, then the legal moves."""
    if can_manage:
        return list(ISSUE_STATUSES)
    moves = [target for source, target in sorted(USER_TRANSITIONS) if source == current]
    return [current, *moves]


def status_change_line(previous: str, target: str, actor: str) -> str:
    """Text of the system line in the notes thread, e.g. 'Initiated to In Process by X'."""
    return f"{previous} to {target} by {actor}"
