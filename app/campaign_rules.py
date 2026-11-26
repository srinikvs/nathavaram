"""Campaign messaging rules that need no database: channels, targets, limits, statuses."""

from __future__ import annotations

CHANNELS: tuple[str, ...] = ("sms", "whatsapp")
CHANNEL_LABELS = {"sms": "SMS", "whatsapp": "WhatsApp"}

TARGET_TYPES: tuple[str, ...] = (
    "ward",
    "booth",
    "panchayath",
    "mandal",
    "constituency",
)
TARGET_LABELS = {
    "ward": "Ward",
    "booth": "Booth",
    "panchayath": "Panchayati",
    "mandal": "Mandalam",
    "constituency": "Constituency",
}
# Voter column that matches each target type.
TARGET_VOTER_FIELDS = {
    "ward": "ward",
    "booth": "booth",
    "panchayath": "panchayath",
    "mandal": "mandal",
    "constituency": "constituency",
}

SEND_MODES: tuple[str, ...] = ("now", "daily")
MAX_DAILY_TIMES = 3
MAX_PHOTOS_PER_CAMPAIGN = 5
TITLE_MAX_LENGTH = 200
MESSAGE_MAX_LENGTH = 4000

RECIPIENT_STATUSES: tuple[str, ...] = ("initiated", "processed", "sent", "rejected")
CAMPAIGN_STATUSES: tuple[str, ...] = (
    "queued",
    "sending",
    "scheduled",
    "paused",
    "completed",
    "cancelled",
)

# Display labels for KPI cards (match the sample dashboard wording).
KPI_LABELS = {
    "initiated": "Initiated",
    "processed": "Processed",
    "sent": "Sent",
    "rejected": "Rejected",
}

IST_TZ = "Asia/Kolkata"
TIME_HHMM_RE = r"^([01]\d|2[0-3]):([0-5]\d)$"
DATE_ISO_RE = r"^\d{4}-\d{2}-\d{2}$"

PHOTO_CAP_MESSAGE = (
    f"A campaign can have at most {MAX_PHOTOS_PER_CAMPAIGN} photos. Remove one before adding another."
)
TOO_MANY_PHOTOS_MESSAGE = f"You can attach up to {MAX_PHOTOS_PER_CAMPAIGN} photos."
TOO_MANY_TIMES_MESSAGE = f"Choose at most {MAX_DAILY_TIMES} daily send times."
