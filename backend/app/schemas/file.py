"""Upload request and response models."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class FileRead(BaseModel):
    """An uploaded file as returned by the API."""

    id: str
    session_id: str
    original_name: str
    mime: str
    size_bytes: int
    extracted_chars: int = 0
    status: str
    error: Optional[str] = None
    chunk_count: int = 0
    is_image: bool = False
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FileList(BaseModel):
    """A session's uploads."""

    items: list[FileRead]
    total: int


class FileUploadResponse(BaseModel):
    """Payload for ``POST /api/files``."""

    file: FileRead
    message: str = Field(description="Student-facing confirmation or warning.")
