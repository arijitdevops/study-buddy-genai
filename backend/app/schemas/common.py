"""Shared response models."""

from __future__ import annotations

from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ErrorResponse(BaseModel):
    """The body returned for every handled error."""

    error: str = Field(description="Machine-readable error code.")
    message: str = Field(description="Explanation safe to show a student.")
    detail: Optional[str] = Field(default=None, description="Extra context for developers.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": "gemini_not_configured",
                "message": "GEMINI_API_KEY is not set.",
                "detail": None,
            }
        }
    )


class Page(BaseModel, Generic[T]):
    """A simple page of results."""

    items: list[T]
    total: int = Field(description="Total rows matching the query.")
    limit: int
    offset: int


class DependencyStatus(BaseModel):
    """Health of one external dependency."""

    name: str
    ok: bool
    detail: str = ""


class HealthResponse(BaseModel):
    """Payload returned by ``GET /api/health``."""

    status: str = Field(description='"ok" or "degraded".')
    version: str
    environment: str
    dependencies: list[DependencyStatus]
    missing_configuration: list[str] = Field(
        default_factory=list,
        description="Names of unset environment variables. Values are never returned.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "degraded",
                "version": "0.1.0",
                "environment": "development",
                "dependencies": [
                    {"name": "database", "ok": True, "detail": "ok"},
                    {"name": "gemini", "ok": False, "detail": "GEMINI_API_KEY not set"},
                ],
                "missing_configuration": ["GEMINI_API_KEY"],
            }
        }
    )


class SimpleMessage(BaseModel):
    """A bare acknowledgement."""

    message: str
    data: Optional[dict[str, Any]] = None
