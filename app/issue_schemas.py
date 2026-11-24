"""Request bodies for the Public Issues JSON API and web forms.

extra='forbid' on writes means a client cannot slip in a status at creation or any other
unknown field. Lengths here mirror app/issue_rules.py.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.issue_rules import ISSUE_MAX_LENGTH, NAME_MAX_LENGTH, NOTE_MAX_LENGTH, SUBJECT_MAX_LENGTH


class IssueCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voter_id: str | None = Field(default=None, max_length=32)
    name: str | None = Field(default=None, max_length=NAME_MAX_LENGTH)
    mobile: str | None = Field(default=None, max_length=20)
    subject: str | None = Field(default=None, max_length=SUBJECT_MAX_LENGTH)
    issue: str | None = Field(default=None, max_length=ISSUE_MAX_LENGTH)


class IssueStatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(max_length=16)


class IssueNoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=NOTE_MAX_LENGTH)


class VoterLookup(BaseModel):
    """POST body for the voter lookup. The VoterId travels in the body, never the URL."""

    model_config = ConfigDict(extra="forbid")

    voter_id: str | None = Field(default=None, max_length=32)


class IssueSearch(BaseModel):
    """Filters travel in a POST body (they can hold personal data), never in a URL."""

    model_config = ConfigDict(extra="ignore")

    q: str | None = Field(default=None, max_length=100)
    status: str | None = Field(default=None, max_length=16)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)
