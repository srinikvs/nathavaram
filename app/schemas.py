"""Request and response models."""

from __future__ import annotations

from datetime import datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.constants import SYNC_BATCH_LIMIT
from app.validation import (
    DEFAULT_IS_LOCAL,
    canonical_age,
    canonical_gender,
    canonical_local,
    canonical_mobile,
    canonical_party,
    canonical_username,
    canonical_voter_id,
    clean_text,
    limit_text,
    local_label,
    require_text,
)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    password: str = Field(min_length=1, max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str
    username: str
    # Send as X-CSRF-Token on cookie-authenticated POST/PUT/PATCH/DELETE. Bearer calls do not need it.
    csrf_token: str | None = None
    # True: only POST /api/auth/change-password and logout work until the password is changed.
    must_change_password: bool = False


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=10, max_length=200)


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    password: str = Field(min_length=10, max_length=200)
    role: str = "user"

    @field_validator("username")
    @classmethod
    def _username(cls, value: object) -> str:
        return canonical_username(value)

    @field_validator("role")
    @classmethod
    def _role(cls, value: str) -> str:
        if value not in {"admin", "user"}:
            raise ValueError("Role must be admin or user")
        return value


class ResetPassword(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_password: str = Field(min_length=10, max_length=200)


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str | None = Field(default=None, max_length=200)
    role: str | None = None
    is_active: bool | None = None

    # Minimum password length is enforced in the service layer (400 with a field name).

    @field_validator("role")
    @classmethod
    def _role(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in {"admin", "user"}:
            raise ValueError("Role must be admin or user")
        return value


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    role: str
    is_active: bool
    must_change_password: bool
    is_owner: bool
    deleted_at: str | None = None
    created_at: str
    updated_at: str


class PasswordChangeResult(UserPublic):
    """Change-password response; carries a fresh token because old ones are revoked."""

    access_token: str
    expires_in: int
    csrf_token: str


class VoterWrite(BaseModel):
    """Full voter snapshot used by create and by sync push. voter_id is the primary key."""

    model_config = ConfigDict(extra="forbid")

    booth: str | None = Field(default=None, validation_alias=AliasChoices("booth", "booth_2024"))
    booth_s_no: str | None = None
    ward: str | None = Field(default=None, validation_alias=AliasChoices("ward", "ward_2026"))
    ward_s_no: str | None = None
    voter_id: str
    first_name: str
    last_name: str | None = None
    relative_name: str | None = None
    house_no: str | None = None
    gender: str | None = None
    age: int | None = None
    street: str | None = None
    mobile: str | None = None
    caste: str | None = None
    village: str | None = None
    panchayath: str | None = None
    mandal: str | None = None
    current_city: str | None = None
    is_local: bool = DEFAULT_IS_LOCAL
    party: str | None = None
    constituency: str | None = None

    @field_validator(
        "booth",
        "booth_s_no",
        "ward",
        "ward_s_no",
        "last_name",
        "relative_name",
        "house_no",
        "street",
        "village",
        "panchayath",
        "mandal",
        "current_city",
        "constituency",
        "caste",
        mode="before",
    )
    @classmethod
    def _texts(cls, value: object, info) -> str | None:  # noqa: ANN001
        return limit_text(clean_text(value), info.field_name)

    @field_validator("is_local", mode="before")
    @classmethod
    def _local(cls, value: object) -> bool:
        return canonical_local(value)

    @field_validator("voter_id", mode="before")
    @classmethod
    def _voter_id(cls, value: object) -> str:
        return canonical_voter_id(value)

    @field_validator("first_name", mode="before")
    @classmethod
    def _first_name(cls, value: object) -> str:
        text = limit_text(require_text(value, "FirstName"), "first_name")
        assert text is not None
        return text

    @field_validator("party", mode="before")
    @classmethod
    def _party(cls, value: object) -> str | None:
        return canonical_party(value)

    @field_validator("mobile", mode="before")
    @classmethod
    def _mobile(cls, value: object) -> str | None:
        return canonical_mobile(value)

    @field_validator("age", mode="before")
    @classmethod
    def _age(cls, value: object) -> int | None:
        return canonical_age(value)

    @field_validator("gender", mode="before")
    @classmethod
    def _gender(cls, value: object) -> str | None:
        return canonical_gender(value)


class VoterPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    booth: str | None = Field(default=None, validation_alias=AliasChoices("booth", "booth_2024"))
    booth_s_no: str | None = None
    ward: str | None = Field(default=None, validation_alias=AliasChoices("ward", "ward_2026"))
    ward_s_no: str | None = None
    voter_id: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    relative_name: str | None = None
    house_no: str | None = None
    gender: str | None = None
    age: int | None = None
    street: str | None = None
    mobile: str | None = None
    caste: str | None = None
    village: str | None = None
    panchayath: str | None = None
    mandal: str | None = None
    current_city: str | None = None
    is_local: bool | None = None
    party: str | None = None
    constituency: str | None = None

    @field_validator(
        "booth",
        "booth_s_no",
        "ward",
        "ward_s_no",
        "last_name",
        "relative_name",
        "house_no",
        "street",
        "village",
        "panchayath",
        "mandal",
        "current_city",
        "constituency",
        "caste",
        mode="before",
    )
    @classmethod
    def _texts(cls, value: object, info) -> str | None:  # noqa: ANN001
        if value is None:
            return None
        return limit_text(clean_text(value), info.field_name)

    @field_validator("is_local", mode="before")
    @classmethod
    def _local(cls, value: object) -> bool:
        if value is None:
            raise ValueError("Local/Non-Local cannot be null")
        return canonical_local(value)

    @field_validator("voter_id", mode="before")
    @classmethod
    def _voter_id(cls, value: object) -> str | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError("VoterId is required")
        return canonical_voter_id(value)

    @field_validator("first_name", mode="before")
    @classmethod
    def _first_name(cls, value: object) -> str | None:
        if value is None:
            raise ValueError("FirstName cannot be null")
        text = limit_text(require_text(value, "FirstName"), "first_name")
        assert text is not None
        return text

    @field_validator("party", mode="before")
    @classmethod
    def _party(cls, value: object) -> str | None:
        return canonical_party(value)

    @field_validator("mobile", mode="before")
    @classmethod
    def _mobile(cls, value: object) -> str | None:
        return canonical_mobile(value)

    @field_validator("age", mode="before")
    @classmethod
    def _age(cls, value: object) -> int | None:
        return canonical_age(value)

    @field_validator("gender", mode="before")
    @classmethod
    def _gender(cls, value: object) -> str | None:
        return canonical_gender(value)


class VoterPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    voter_id: str
    booth: str | None
    booth_s_no: str | None
    ward: str | None
    ward_s_no: str | None
    first_name: str
    last_name: str | None
    relative_name: str | None
    house_no: str | None
    gender: str | None
    age: int | None
    street: str | None
    mobile: str | None
    caste: str | None
    village: str | None
    panchayath: str | None
    mandal: str | None
    current_city: str | None
    is_local: bool
    party: str | None
    constituency: str | None
    created_at: str
    updated_at: str
    revision: int
    deleted_at: str | None
    source_id: str | None


class VoterPage(BaseModel):
    items: list[VoterPublic]
    total: int
    page: int
    page_size: int


class VoterSearch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    q: str | None = Field(default=None, max_length=100)
    voter_id: str | None = Field(default=None, max_length=32)
    village: str | None = Field(default=None, max_length=128)
    # Local / Non-Local narrows the list; blank (or "Any") applies no filter.
    is_local: str | None = Field(default=None, max_length=16)
    panchayath: str | None = Field(default=None, max_length=128)
    mandal: str | None = Field(default=None, max_length=128)
    booth: str | None = Field(default=None, max_length=64)
    ward: str | None = Field(default=None, max_length=64)
    caste: str | None = Field(default=None, max_length=128)
    party: str | None = Field(default=None, max_length=32)
    # Male / Female / Other; blank (or "Any") applies no filter.
    gender: str | None = Field(default=None, max_length=16)
    mobile: str | None = Field(default=None, max_length=20)
    page: int = 1
    page_size: int = 50

    @field_validator(
        "q",
        "voter_id",
        "village",
        "is_local",
        "panchayath",
        "mandal",
        "booth",
        "ward",
        "caste",
        "party",
        "gender",
        "mobile",
        mode="before",
    )
    @classmethod
    def _blank(cls, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return local_label(value)
        if not isinstance(value, str):
            raise ValueError("Expected text")
        text = value.strip()
        return text or None

    @field_validator("is_local")
    @classmethod
    def _local_filter(cls, value: str | None) -> str | None:
        """Normalise to 'Local' / 'Non-Local'; 'Any' means no filter."""
        if value is None or value.casefold() == "any":
            return None
        return local_label(canonical_local(value))

    @field_validator("page")
    @classmethod
    def _page(cls, value: int) -> int:
        if value < 1:
            raise ValueError("page must be at least 1")
        return value

    @field_validator("page_size")
    @classmethod
    def _page_size(cls, value: int) -> int:
        if value < 1 or value > 200:
            raise ValueError("page_size must be between 1 and 200")
        return value

    @field_validator("gender")
    @classmethod
    def _gender_filter(cls, value: str | None) -> str | None:
        """Normalise to Male / Female / Other; 'Any' means no filter."""
        if value is None or value.casefold() == "any":
            return None
        return canonical_gender(value)

    @field_validator("party")
    @classmethod
    def _party(cls, value: str | None) -> str | None:
        if value is None:
            return None
        found = canonical_party(value)
        if found is None:
            raise ValueError("Party filter must be one of the known parties")
        return found


class ImportErrorItem(BaseModel):
    row: int
    field: str | None = None
    message: str


class ImportReport(BaseModel):
    rows_read: int
    imported: int
    skipped: int
    errors: list[ImportErrorItem]


class CountRow(BaseModel):
    label: str
    count: int


class ReportSummary(BaseModel):
    total: int
    by_booth: list[CountRow]
    by_ward: list[CountRow]
    by_village: list[CountRow]
    by_panchayath: list[CountRow]
    by_mandal: list[CountRow]
    by_local: list[CountRow]
    by_age_band: list[CountRow]
    by_caste: list[CountRow]
    by_party: list[CountRow]


class SyncChange(BaseModel):
    """One offline record, keyed by voter_id. A non-delete is a full snapshot, not a patch."""

    model_config = ConfigDict(extra="forbid")

    voter_id: str
    updated_at: datetime
    deleted: bool = False
    base_revision: int | None = None
    created_at: datetime | None = None
    booth: str | None = Field(default=None, validation_alias=AliasChoices("booth", "booth_2024"))
    booth_s_no: str | None = None
    ward: str | None = Field(default=None, validation_alias=AliasChoices("ward", "ward_2026"))
    ward_s_no: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    relative_name: str | None = None
    house_no: str | None = None
    gender: str | None = None
    age: int | None = None
    street: str | None = None
    mobile: str | None = None
    caste: str | None = None
    village: str | None = None
    panchayath: str | None = None
    mandal: str | None = None
    current_city: str | None = None
    is_local: bool | str | None = None
    party: str | None = None
    constituency: str | None = None

    @field_validator("age", mode="before")
    @classmethod
    def _age(cls, value: object) -> int | None:
        if value is None or value == "":
            return None
        return canonical_age(value)

    @field_validator("gender", mode="before")
    @classmethod
    def _gender(cls, value: object) -> str | None:
        return canonical_gender(value)


class SyncPushRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1, max_length=64)
    changes: list[SyncChange] = Field(max_length=SYNC_BATCH_LIMIT)

    @field_validator("device_id")
    @classmethod
    def _device(cls, value: str) -> str:
        text = value.strip()
        if not text or len(text) > 64:
            raise ValueError("device_id must be 1–64 characters")
        if any(ord(ch) < 32 for ch in text):
            raise ValueError("device_id contains invalid characters")
        return text


class SyncResult(BaseModel):
    voter_id: str
    status: str
    message: str | None = None
    server_revision: int | None = None
    server_record: VoterPublic | None = None


class SyncPushResponse(BaseModel):
    server_time: str
    server_revision: int
    results: list[SyncResult]


class SyncPullResponse(BaseModel):
    server_time: str
    server_revision: int
    since: int
    has_more: bool
    changes: list[VoterPublic]


class MasterColumn(BaseModel):
    key: str
    label: str
    numeric: bool = False


class MasterTable(BaseModel):
    entity: str
    label: str
    available: bool
    note: str | None = None
    columns: list[MasterColumn]
    rows: list[dict[str, str | int]]
