"""FastAPI application entry point.

Run locally with::

    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api.routes import api_router
from app.config import settings
from app.db.session import create_tables, dispose_engine, init_engine
from app.logging_config import configure_logging
from app.services.gemini import GeminiNotConfiguredError
from app.services.rate_limit import RateLimitMiddleware

configure_logging(settings.log_level)
logger = logging.getLogger(__name__)

DESCRIPTION = """A guardrailed study assistant for school students.

Powered by Google Gemini and orchestrated with LangGraph. Chat responses are
streamed over Server-Sent Events; see `POST /api/chat`.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Prepare and tear down process-wide resources."""
    logger.info("Starting %s v%s (%s).", settings.app_name, __version__, settings.environment)
    try:
        settings.upload_path.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning("Could not create upload directory %s.", settings.upload_path)

    init_engine()
    if settings.auto_create_tables:
        try:
            await create_tables()
        except Exception as exc:  # noqa: BLE001 - the API still starts; /api/health reports it
            logger.warning(
                "Could not create database tables (%s). Is the database running? "
                "See GET /api/health.",
                exc.__class__.__name__,
            )
    missing = settings.missing_optional_keys()
    if missing:
        logger.warning(
            "Running with missing configuration: %s. Check GET /api/health.",
            ", ".join(missing),
        )
    try:
        yield
    finally:
        await dispose_engine()
        logger.info("Shutdown complete.")


app = FastAPI(
    title=settings.app_name,
    description=DESCRIPTION,
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-RateLimit-Limit", "X-RateLimit-Remaining", "Retry-After"],
)
app.add_middleware(RateLimitMiddleware)

app.include_router(api_router)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Return HTTP errors in the project's error envelope."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": f"http_{exc.status_code}",
            "message": str(exc.detail),
            "detail": None,
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Turn validation failures into a readable message."""
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(part) for part in first.get("loc", [])[1:]) or "request"
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "error": "validation_error",
            "message": f"{field}: {first.get('msg', 'is invalid')}",
            "detail": None,
        },
    )


@app.exception_handler(GeminiNotConfiguredError)
async def gemini_not_configured_handler(
    request: Request, exc: GeminiNotConfiguredError
) -> JSONResponse:
    """Explain a missing API key instead of returning a traceback."""
    logger.warning("Request hit an unconfigured Gemini: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"error": "gemini_not_configured", "message": str(exc), "detail": None},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log the traceback server-side; return a generic message to the client."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "internal_error",
            "message": "Something went wrong on my side. Please try again.",
            "detail": None,
        },
    )


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    """Point browsers at the docs."""
    return {
        "name": settings.app_name,
        "version": __version__,
        "docs": "/docs",
        "health": "/api/health",
    }
