"""
Application configuration for SmartFlow AI.

Settings are loaded exclusively from environment variables or a .env file.
No secrets are hardcoded. Designed for Google Cloud Run and local development.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-level settings resolved from environment variables.

    All values can be overridden via environment variables or a .env file.
    Sensitive values (API keys, credentials) must never be committed to
    source control and must always be injected at runtime.

    Cloud Run deployment: set environment variables via the Cloud Run
    service configuration or Secret Manager references.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Application identity
    # ------------------------------------------------------------------

    app_name: str = Field(
        default="SmartFlow AI",
        description="Human-readable application name surfaced in metadata endpoints.",
    )
    app_version: str = Field(
        default="0.1.0",
        description="Semantic version of the application (MAJOR.MINOR.PATCH).",
    )

    # ------------------------------------------------------------------
    # Runtime behaviour
    # ------------------------------------------------------------------

    debug: bool = Field(
        default=False,
        description=(
            "Enable debug mode. Must be False in production. "
            "Set via the DEBUG environment variable."
        ),
    )
    api_prefix: str = Field(
        default="/api/v1",
        description="URL prefix applied to all API routes (e.g. /api/v1).",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description=(
            "Minimum log level for structured output. "
            "Use INFO in production; DEBUG only in local development."
        ),
    )

    # ------------------------------------------------------------------
    # CORS
    # ------------------------------------------------------------------

    allowed_origins: list[str] = Field(
        default_factory=list,
        description=(
            "Allowlist of origins permitted by CORS middleware. "
            "Defaults to an empty list (no cross-origin access). "
            "Override via the ALLOWED_ORIGINS environment variable "
            "as a JSON array: '[\"https://example.com\"]'."
        ),
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("api_prefix")
    @classmethod
    def api_prefix_must_start_with_slash(cls, value: str) -> str:
        """Ensure the API prefix always begins with a forward slash."""
        if not value.startswith("/"):
            raise ValueError("api_prefix must start with '/' (e.g. '/api/v1')")
        return value

    @field_validator("app_version")
    @classmethod
    def version_must_be_semver_like(cls, value: str) -> str:
        """Validate that app_version follows a MAJOR.MINOR.PATCH pattern.

        Each component must be a non-negative integer.
        """
        parts = value.split(".")
        if len(parts) != 3 or not all(part.isdigit() for part in parts):
            raise ValueError(
                f"app_version '{value}' must follow MAJOR.MINOR.PATCH format "
                "(e.g. '0.1.0'). Each component must be a non-negative integer."
            )
        return value

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_allowed_origins(cls, value: object) -> list[str]:
        """Accept ALLOWED_ORIGINS as a comma-separated string or a list.

        Cloud Run environment variables are plain strings; this validator
        allows operators to set: ALLOWED_ORIGINS=https://a.com,https://b.com
        """
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value  # type: ignore[return-value]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings instance.

    lru_cache ensures the Settings object is constructed once per process,
    which is efficient and consistent with FastAPI's dependency injection
    pattern (``Depends(get_settings)``).

    To force a reload during testing, call ``get_settings.cache_clear()``
    before constructing a new Settings instance with test overrides.
    """
    return Settings()
