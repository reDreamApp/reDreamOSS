"""Settings via pydantic-settings."""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ASYNC_DRIVERS = ("postgresql+asyncpg://", "sqlite+aiosqlite://")


class Settings(BaseSettings):
    """Runtime config. Surface area mirrored in .env.example."""

    # Database — must use an async driver.
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"

    # Supabase
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    # Rehearsal-audio bucket targeted by the GDPR purge's storage cleanup
    # (storage_service). Mirrors apps/ai's setting of the same name.
    supabase_storage_bucket: str = "rehearsal-audio"
    supabase_public_url: str = ""
    rehearsal_url_ttl_seconds: int = Field(default=300, ge=60, le=3600)

    # Supabase JWT verification
    supabase_jwt_iss: str = ""
    supabase_jwks_url: str = ""
    supabase_jwt_aud: str = "authenticated"
    supabase_jwt_algorithms: str = "ES256"

    # Art. 9 consent — the consent-text version users must have granted.
    # Bump ONLY on material changes to the consent copy (new processor, new
    # purpose): a bump forces every user through re-consent on next launch.
    consent_version: str = "2026-10-07"

    # AI service
    ai_service_url: str = "http://localhost:8080"
    ai_request_timeout: float = 90.0
    # DEV-ONLY escape hatch for the crisis pre-classifier (ADR 0005). Set
    # false ONLY in local dev while the classifier prompt misfires (known
    # false positive, 2026-07-13). MUST be true (default) in production —
    # docs/ROADMAP.md tracks the re-enable gate.
    safety_classifier_enabled: bool = True

    # Runtime
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("database_url")
    @classmethod
    def _must_be_async_driver(cls, v: str) -> str:
        if not v.startswith(_ASYNC_DRIVERS):
            raise ValueError(
                f"DATABASE_URL must use an async driver (one of {_ASYNC_DRIVERS}), got: {v[:40]}…"
            )
        return v

    @property
    def jwt_algorithms_list(self) -> list[str]:
        return [a.strip() for a in self.supabase_jwt_algorithms.split(",") if a.strip()]


settings = Settings()
