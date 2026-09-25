"""Logging setup.

A single :func:`configure_logging` call installs a consistent format for the
application, uvicorn and SQLAlchemy loggers. A redaction filter is attached so
that an accidentally logged API key never reaches the log stream.
"""

from __future__ import annotations

import logging
import logging.config
import re
from typing import Any

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(AIza[0-9A-Za-z\-_]{10,})"),
    re.compile(r"(tvly-[0-9A-Za-z\-_]{8,})"),
    re.compile(r"((?:api[_-]?key|token|secret|password)\s*[=:]\s*)([^\s,;'\"]+)", re.I),
)


class SecretRedactingFilter(logging.Filter):
    """Redact anything that looks like a credential from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Rewrite ``record.msg``/``record.args`` in place; always returns True."""
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive, never drop a log line
            return True
        redacted = self.redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True

    @staticmethod
    def redact(text: str) -> str:
        """Return ``text`` with credential-shaped substrings replaced."""
        result = text
        for pattern in _SECRET_PATTERNS:
            if pattern.groups == 2:
                result = pattern.sub(r"\1***REDACTED***", result)
            else:
                result = pattern.sub("***REDACTED***", result)
        return result


def configure_logging(level: str = "INFO") -> None:
    """Install the application-wide logging configuration.

    Args:
        level: Root log level name, e.g. ``"INFO"`` or ``"DEBUG"``.
    """
    config: dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"redact": {"()": SecretRedactingFilter}},
        "formatters": {
            "standard": {
                "format": "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "standard",
                "filters": ["redact"],
                "stream": "ext://sys.stdout",
            }
        },
        "root": {"handlers": ["console"], "level": level},
        "loggers": {
            "uvicorn": {"handlers": ["console"], "level": level, "propagate": False},
            "uvicorn.error": {"handlers": ["console"], "level": level, "propagate": False},
            "uvicorn.access": {"handlers": ["console"], "level": "WARNING", "propagate": False},
            "sqlalchemy.engine": {"level": "WARNING", "propagate": True},
            "app": {"level": level, "propagate": True},
        },
    }
    logging.config.dictConfig(config)
