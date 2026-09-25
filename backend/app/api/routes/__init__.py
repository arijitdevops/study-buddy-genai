"""API route modules, aggregated onto one router."""

from fastapi import APIRouter

from app.api.routes import chat, files, health, sessions

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(sessions.router)
api_router.include_router(files.router)
api_router.include_router(chat.router)

__all__ = ["api_router"]
