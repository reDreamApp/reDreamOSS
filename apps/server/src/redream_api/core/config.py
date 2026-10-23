"""Settings via pydantic-settings."""

from __future__ import annotations

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ASYNC_DRIVERS = ("postgresql+asyncpg://", "sqlite+aiosqlite://")

# Shown in the app, in place of the AI reply, when the crisis check flags a
# message (ADR 0005). Neutral: no country's numbers; an operator sets their
# own with HELPLINE_TEXT_EN / HELPLINE_TEXT_DE.
HELPLINE_TEXT_EN_DEFAULT = (
    "It sounds like you might be going through something serious. "
    "This app is a self-help tool and can't provide crisis support. "
    "Please reach out to someone who can help right now. "
    "If you are in immediate danger, call your local emergency number. "
    "You can find a free crisis line for your country at findahelpline.com."
)
HELPLINE_TEXT_DE_DEFAULT = (
    "Es klingt, als würdest du gerade etwas Ernstes durchmachen. "
    "Diese App ist ein Selbsthilfe-Tool und kann keine Krisenhilfe leisten. "
    "Bitte wende dich an Menschen, die dir jetzt helfen können. "
    "In akuter Gefahr ruf bitte die örtliche Notrufnummer an. "
    "Ein kostenloses Krisentelefon in deinem Land findest du unter findahelpline.com."
)
HELPLINE_TEXT_MAX_LEN = 1000
_HELPLINE_TEXT_DEFAULTS = {
    "helpline_text_en": HELPLINE_TEXT_EN_DEFAULT,
    "helpline_text_de": HELPLINE_TEXT_DE_DEFAULT,
}


class Settings(BaseSettings):
    """Runtime config. Surface area mirrored in .env.example."""

    # Database — must use an async driver.
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"

    # Supabase
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""

    # Lifetime of the signed rehearsal-audio link (services/audio_links).
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

    # AI (in-process, redream_ai)
    # DEV-ONLY escape hatch for the crisis pre-classifier (ADR 0005). Set
    # false ONLY in local dev while the classifier prompt misfires (known
    # false positive, 2026-07-13). MUST be true (default) in production —
    # docs/ROADMAP.md tracks the re-enable gate.
    safety_classifier_enabled: bool = True

    # Helpline text returned with a high-risk safety event, per chat language
    # (English for any other). Blank = the neutral default; at most
    # HELPLINE_TEXT_MAX_LEN characters, or startup fails.
    helpline_text_en: str = HELPLINE_TEXT_EN_DEFAULT
    helpline_text_de: str = HELPLINE_TEXT_DE_DEFAULT

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

    @field_validator("helpline_text_en", "helpline_text_de", mode="before")
    @classmethod
    def _helpline_text(cls, v: object, info: ValidationInfo) -> object:
        if not isinstance(v, str):
            return v
        field = info.field_name or ""
        text = v.strip()
        if not text:
            # HELPLINE_TEXT_DE= left blank in .env means the default, not "".
            return _HELPLINE_TEXT_DEFAULTS[field]
        if len(text) > HELPLINE_TEXT_MAX_LEN:
            name = field.upper()
            raise ValueError(
                f"{name} is {len(text)} characters; at most {HELPLINE_TEXT_MAX_LEN} are allowed"
            )
        return text

    def helpline_text_for(self, language: str) -> str:
        """The helpline text for a chat in `language`: German for "de", else English."""
        return self.helpline_text_de if language == "de" else self.helpline_text_en

    @property
    def jwt_algorithms_list(self) -> list[str]:
        return [a.strip() for a in self.supabase_jwt_algorithms.split(",") if a.strip()]


settings = Settings()
