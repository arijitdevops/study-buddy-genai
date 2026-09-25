"""Application configuration.

All settings are read from environment variables (or a local ``.env`` file).
Nothing in here raises on a missing optional key: absent third-party keys are
reported through ``/api/health`` and turn the matching feature off rather than
crashing the process at import time.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Repository-relative default for the .env file (backend/.env).
_BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Typed application settings.

    Attributes are populated from the environment. See ``backend/.env.example``
    for the full list with placeholder values.
    """

    model_config = SettingsConfigDict(
        env_file=str(_BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application -----------------------------------------------------
    app_name: str = "Study Buddy GenAI"
    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")
    session_secret: str = Field(default="change-me-in-production")

    # --- Gemini ----------------------------------------------------------
    gemini_api_key: str = Field(default="")
    gemini_model: str = Field(default="gemini-2.5-flash")
    gemini_vision_model: str = Field(default="gemini-2.5-flash")
    gemini_guard_model: str = Field(default="gemini-2.5-flash")
    gemini_timeout_seconds: float = Field(default=60.0)
    gemini_max_retries: int = Field(default=3)

    # --- Database --------------------------------------------------------
    database_url: str = Field(
        default="mysql+asyncmy://root:password@localhost:3306/study_buddy"
    )
    db_echo: bool = Field(default=False)
    #: Create any missing tables on startup (idempotent). Turn off when the
    #: schema is managed exclusively with Alembic.
    auto_create_tables: bool = Field(default=True)
    db_pool_size: int = Field(default=5)
    db_max_overflow: int = Field(default=10)

    # --- Search tools ----------------------------------------------------
    enable_web_search: bool = Field(default=True)
    #: ``auto`` = Tavily if TAVILY_API_KEY is set, then Serper if SERPER_API_KEY
    #: is set, then DuckDuckGo (no key). ``duckduckgo`` / ``tavily`` / ``serper``
    #: pin one provider (DuckDuckGo stays the fallback for the keyed ones).
    web_search_provider: str = Field(default="auto")
    tavily_api_key: str = Field(default="")
    serper_api_key: str = Field(default="")
    web_search_max_results: int = Field(default=5)
    #: DuckDuckGo region code, e.g. ``us-en``, ``uk-en``, ``in-en``.
    web_search_region: str = Field(default="us-en")

    # --- Uploads ---------------------------------------------------------
    upload_dir: str = Field(default="./uploads")
    max_upload_mb: int = Field(default=15)
    max_files_per_session: int = Field(default=10)

    # --- HTTP ------------------------------------------------------------
    cors_origins: str = Field(default="http://localhost:5173")
    rate_limit_per_minute: int = Field(default=30)

    # --- Pedagogy --------------------------------------------------------
    default_grade: int = Field(default=8)

    @field_validator("default_grade")
    @classmethod
    def _validate_grade(cls, value: int) -> int:
        """Clamp the configured default grade into the supported 1..12 range."""
        if not 1 <= value <= 12:
            logger.warning("DEFAULT_GRADE=%s out of range 1..12; clamping.", value)
            return min(12, max(1, value))
        return value

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        """Normalise the log level, falling back to INFO when unrecognised."""
        normalised = value.upper().strip()
        if normalised not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            return "INFO"
        return normalised

    # --- Derived helpers -------------------------------------------------
    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins as a list, parsed from the comma-separated env value."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def upload_path(self) -> Path:
        """Absolute path to the upload directory, resolved against the backend root."""
        path = Path(self.upload_dir)
        if not path.is_absolute():
            path = _BACKEND_ROOT / path
        return path

    @property
    def max_upload_bytes(self) -> int:
        """Upload size cap in bytes."""
        return self.max_upload_mb * 1024 * 1024

    @property
    def gemini_configured(self) -> bool:
        """True when a Gemini API key is present."""
        return bool(self.gemini_api_key.strip())

    @property
    def web_search_configured(self) -> bool:
        """True when web search is enabled.

        DuckDuckGo needs no key, so search works out of the box; Tavily and
        Serper are optional upgrades.
        """
        return self.enable_web_search

    @property
    def is_sqlite(self) -> bool:
        """True when DATABASE_URL points at SQLite (local dev and tests)."""
        return self.database_url.startswith("sqlite")

    def missing_optional_keys(self) -> list[str]:
        """Names of unset keys that degrade features but do not break startup."""
        missing: list[str] = []
        if not self.gemini_configured:
            missing.append("GEMINI_API_KEY")
        return missing


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    settings = Settings()
    missing = settings.missing_optional_keys()
    if missing:
        logger.warning(
            "Starting with missing configuration: %s. "
            "Affected features will return a clear error instead of working.",
            ", ".join(missing),
        )
    return settings


settings = get_settings()
