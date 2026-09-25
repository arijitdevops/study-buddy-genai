"""Async SQLAlchemy engine and session management.

The engine is created lazily so that importing the application never requires a
reachable database. ``/api/health`` uses :func:`ping_database` to report
connectivity without taking the process down.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Optional

from typing import Any

from sqlalchemy import event, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

logger = logging.getLogger(__name__)

_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def init_engine() -> AsyncEngine:
    """Create (once) and return the async engine.

    Returns:
        The process-wide :class:`AsyncEngine`.
    """
    global _engine, _session_factory
    if _engine is None:
        logger.info("Creating async database engine.")
        options: dict[str, Any] = {"echo": settings.db_echo}
        if not settings.is_sqlite:
            # Pool tuning only applies to server databases (MySQL).
            options.update(
                pool_size=settings.db_pool_size,
                max_overflow=settings.db_max_overflow,
                pool_pre_ping=True,
                pool_recycle=1800,
            )
        _engine = create_async_engine(settings.database_url, **options)
        if settings.is_sqlite:
            _enable_sqlite_foreign_keys(_engine)
        _session_factory = async_sessionmaker(
            bind=_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _engine


def _enable_sqlite_foreign_keys(engine: AsyncEngine) -> None:
    """SQLite ignores ON DELETE CASCADE unless foreign keys are switched on."""

    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


async def create_tables() -> None:
    """Create any missing tables (``CREATE TABLE IF NOT EXISTS`` semantics).

    Used on startup when ``AUTO_CREATE_TABLES`` is true, and by the tests.
    Alembic remains the tool for evolving an existing schema.
    """
    from app.db import models  # noqa: F401 - registers the tables on Base.metadata
    from app.db.base import Base

    engine = init_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the session factory, initialising the engine if needed."""
    if _session_factory is None:
        init_engine()
    assert _session_factory is not None  # narrowed by init_engine
    return _session_factory


async def dispose_engine() -> None:
    """Dispose of the engine's connection pool (called on shutdown)."""
    global _engine, _session_factory
    if _engine is not None:
        logger.info("Disposing database engine.")
        await _engine.dispose()
        _engine = None
        _session_factory = None


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a transactional :class:`AsyncSession`.

    The session is committed when the request handler returns normally and
    rolled back if it raises.
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        logger.exception("Database error; transaction rolled back.")
        raise
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def ping_database() -> tuple[bool, str]:
    """Check database reachability.

    Returns:
        ``(ok, detail)`` where ``detail`` is ``"ok"`` or a short error string.
        Never raises.
    """
    try:
        engine = init_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True, "ok"
    except SQLAlchemyError as exc:
        logger.warning("Database ping failed: %s", exc.__class__.__name__)
        return False, f"{exc.__class__.__name__}: unable to connect"
    except Exception as exc:  # pragma: no cover - driver import errors etc.
        logger.warning("Database ping failed: %s", exc.__class__.__name__)
        return False, f"{exc.__class__.__name__}"
