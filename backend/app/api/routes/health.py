"""Health and readiness endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter

from app import __version__
from app.agents.tools.web_search import get_web_search_tool
from app.config import settings
from app.db.session import ping_database
from app.schemas.common import DependencyStatus, HealthResponse
from app.services.gemini import get_gemini_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Service health")
async def health() -> HealthResponse:
    """Report dependency status without leaking any secret values.

    Returns:
        ``status: "ok"`` when the database is reachable and Gemini is
        configured; ``"degraded"`` otherwise. Keys are reported by name only.
    """
    db_ok, db_detail = await ping_database()

    gemini = get_gemini_service()
    gemini_ok = gemini.available
    gemini_detail = (
        f"key present, model {settings.gemini_model}"
        if gemini_ok
        else "GEMINI_API_KEY not set (or google-genai not installed)"
    )

    search = get_web_search_tool()
    search_ok = search.available
    if not settings.enable_web_search:
        search_detail = "disabled via ENABLE_WEB_SEARCH"
    elif search_ok:
        search_detail = "providers: " + " -> ".join(search.provider_chain)
    else:
        search_detail = search.unavailable_reason

    upload_dir = settings.upload_path
    uploads_ok = upload_dir.is_dir()

    dependencies = [
        DependencyStatus(name="database", ok=db_ok, detail=db_detail),
        DependencyStatus(name="gemini", ok=gemini_ok, detail=gemini_detail),
        DependencyStatus(name="web_search", ok=search_ok, detail=search_detail),
        DependencyStatus(
            name="upload_dir",
            ok=uploads_ok,
            detail=str(upload_dir) if uploads_ok else f"{upload_dir} does not exist",
        ),
    ]

    status_value = "ok" if (db_ok and gemini_ok) else "degraded"
    return HealthResponse(
        status=status_value,
        version=__version__,
        environment=settings.environment,
        dependencies=dependencies,
        missing_configuration=settings.missing_optional_keys(),
    )
