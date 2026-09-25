"""Chat request/response models, including the SSE event shapes.

The SSE models are declared here rather than only documented in prose so that
the generated OpenAPI schema and ``frontend/src/types.ts`` stay in step.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_MESSAGE_CHARS = 4000


class ChatRequest(BaseModel):
    """Body for ``POST /api/chat``."""

    session_id: str = Field(max_length=32, description="Target chat session.")
    message: str = Field(max_length=MAX_MESSAGE_CHARS)
    grade: Optional[int] = Field(
        default=None, ge=1, le=12, description="Overrides the session's grade for this turn."
    )
    subject: Optional[str] = Field(default=None, max_length=64)
    file_ids: list[str] = Field(
        default_factory=list,
        max_length=3,
        description="Ids of previously uploaded files to attach to this turn.",
    )

    @field_validator("message")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        """Reject a whitespace-only message early."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("Message cannot be empty.")
        return stripped

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "session_id": "9f2c1a4b8e7d4c1fb0a3d5e6f7081920",
                "message": "Explain photosynthesis with an example",
                "grade": 7,
                "subject": "biology",
                "file_ids": [],
            }
        }
    )


class MessageRead(BaseModel):
    """A stored message."""

    id: str
    session_id: str
    role: str
    content: str
    token_count: int = 0
    metadata: Optional[dict[str, Any]] = Field(default=None, alias="message_metadata")
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class MessageList(BaseModel):
    """Payload for ``GET /api/sessions/{id}/messages``."""

    items: list[MessageRead]
    total: int


# ----------------------------------------------------------------------
# SSE event payloads
# ----------------------------------------------------------------------
class TokenEvent(BaseModel):
    """``event: token`` -- one delta of the answer."""

    text: str


class ToolCallEvent(BaseModel):
    """``event: tool_call`` -- a tool started, finished or failed."""

    tool: str
    status: Literal["running", "done", "error"]
    detail: str = ""


class GuardrailEvent(BaseModel):
    """``event: guardrail`` -- a guard changed or stopped the turn."""

    guard: str
    verdict: Literal["allow", "soft_block", "hard_block"]
    category: Optional[str] = None
    message: str = ""
    replacement: Optional[str] = Field(
        default=None,
        description="When present, replaces everything streamed so far.",
    )


class ToolResultSummary(BaseModel):
    """One tool invocation, summarised in the ``done`` event."""

    tool: str
    query: str = ""
    ok: bool = True
    detail: str = ""
    urls: list[str] = Field(default_factory=list)


class GuardrailSummary(BaseModel):
    """One guardrail decision, summarised in the ``done`` event."""

    guard: str
    verdict: str
    category: Optional[str] = None
    detail: str = ""


class DoneEvent(BaseModel):
    """``event: done`` -- the final, guard-approved answer plus metadata."""

    answer: str
    message_id: Optional[str] = None
    intent: Optional[str] = None
    blocked: bool = False
    notices: list[str] = Field(default_factory=list)
    tool_results: list[ToolResultSummary] = Field(default_factory=list)
    guardrail_events: list[GuardrailSummary] = Field(default_factory=list)
    quiz: Optional[dict[str, Any]] = None
    citations: list[str] = Field(default_factory=list)


class ErrorEvent(BaseModel):
    """``event: error`` -- the turn could not be completed."""

    message: str
    detail: Optional[str] = None
