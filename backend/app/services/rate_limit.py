"""In-process sliding-window rate limiting.

Keyed by client IP and, when present, by session id, so one noisy tab cannot
exhaust the quota of another. The store is per-process: behind more than one
worker, swap :class:`InMemoryRateLimiter` for a Redis-backed implementation
with the same two methods.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Callable, Deque, Optional

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60.0
#: Paths that never count against the quota.
EXEMPT_PATHS: frozenset[str] = frozenset({"/api/health", "/docs", "/redoc", "/openapi.json"})


@dataclass(slots=True)
class RateLimitDecision:
    """Whether a request may proceed."""

    allowed: bool
    remaining: int
    retry_after: int


class InMemoryRateLimiter:
    """A sliding-window counter with per-key request timestamps."""

    def __init__(self, limit_per_minute: Optional[int] = None) -> None:
        """Create the limiter.

        Args:
            limit_per_minute: Requests allowed per key per minute.
        """
        self._limit = limit_per_minute or settings.rate_limit_per_minute
        self._hits: dict[str, Deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    @property
    def limit(self) -> int:
        """The configured per-minute limit."""
        return self._limit

    async def check(self, key: str) -> RateLimitDecision:
        """Record a hit for ``key`` and decide whether it is allowed."""
        now = time.monotonic()
        async with self._lock:
            window = self._hits[key]
            cutoff = now - WINDOW_SECONDS
            while window and window[0] < cutoff:
                window.popleft()
            if len(window) >= self._limit:
                retry_after = max(1, int(WINDOW_SECONDS - (now - window[0])) + 1)
                return RateLimitDecision(allowed=False, remaining=0, retry_after=retry_after)
            window.append(now)
            return RateLimitDecision(
                allowed=True, remaining=self._limit - len(window), retry_after=0
            )

    async def reset(self, key: Optional[str] = None) -> None:
        """Clear counters for one key, or all of them."""
        async with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)


def default_key_builder(request: Request) -> str:
    """Build a rate-limit key from the client IP and optional session header."""
    client_host = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        client_host = forwarded.split(",")[0].strip()
    session_id = request.headers.get("x-session-id") or ""
    return f"{client_host}|{session_id}"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rejects requests above the configured per-minute rate with HTTP 429."""

    def __init__(
        self,
        app: Callable[..., object],
        limiter: Optional[InMemoryRateLimiter] = None,
        key_builder: Callable[[Request], str] = default_key_builder,
    ) -> None:
        """Install the middleware.

        Args:
            app: The ASGI app being wrapped.
            limiter: Limiter instance; a default one is created when omitted.
            key_builder: Produces the bucket key for a request.
        """
        super().__init__(app)  # type: ignore[arg-type]
        self._limiter = limiter or InMemoryRateLimiter()
        self._key_builder = key_builder

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Apply the limit, then delegate to the next handler."""
        if request.method == "OPTIONS" or request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        decision = await self._limiter.check(self._key_builder(request))
        if not decision.allowed:
            logger.info("Rate limit hit for %s %s", request.method, request.url.path)
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limited",
                    "message": (
                        "You're sending messages faster than I can keep up with. "
                        f"Try again in {decision.retry_after} second(s)."
                    ),
                },
                headers={
                    "Retry-After": str(decision.retry_after),
                    "X-RateLimit-Limit": str(self._limiter.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self._limiter.limit)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        return response
