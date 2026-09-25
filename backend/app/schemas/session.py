"""Chat-session request and response models."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_TITLE_LENGTH = 160


class SessionCreate(BaseModel):
    """Body for ``POST /api/sessions``."""

    title: str = Field(default="New chat", max_length=MAX_TITLE_LENGTH)
    subject: Optional[str] = Field(default=None, max_length=64)
    grade: int = Field(default=8, ge=1, le=12, description="School class, 1-12.")
    student_id: Optional[str] = Field(
        default=None,
        max_length=32,
        description="Existing student id; a new student is created when omitted.",
    )
    display_name: str = Field(default="Student", max_length=64)

    @field_validator("title")
    @classmethod
    def _non_empty_title(cls, value: str) -> str:
        """Fall back to a default when the title is blank."""
        return value.strip() or "New chat"


class SessionUpdate(BaseModel):
    """Body for ``PATCH /api/sessions/{id}``. All fields are optional."""

    title: Optional[str] = Field(default=None, max_length=MAX_TITLE_LENGTH)
    subject: Optional[str] = Field(default=None, max_length=64)
    grade: Optional[int] = Field(default=None, ge=1, le=12)


class SessionRead(BaseModel):
    """A chat session as returned by the API."""

    id: str
    student_id: str
    title: str
    subject: Optional[str] = None
    grade: int = 8
    message_count: int = 0
    file_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SessionList(BaseModel):
    """Payload for ``GET /api/sessions``."""

    items: list[SessionRead]
    total: int
