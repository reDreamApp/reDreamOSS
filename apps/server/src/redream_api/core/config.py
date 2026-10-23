"""Settings via pydantic-settings."""

from __future__ import annotations

from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, ValidationInfo, field_validator, model_validator
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
INVITE_CODE_MIN_LEN = 8
# The register request accepts invite codes up to this length.
INVITE_CODE_MAX_LEN = 256
_HELPLINE_TEXT_DEFAULTS = {
    "helpline_text_en": HELPLINE_TEXT_EN_DEFAULT,
    "helpline_text_de": HELPLINE_TEXT_DE_DEFAULT,
}
# The instance block (GET /api/v1/instance): at most this many characters each.
_INSTANCE_TEXT_MAX_LEN = {
    "instance_name": 80,
    "operator_name": 120,
    "operator_contact": 200,
    "privacy_url": 500,
}
# PRIVACY_URL may use http:// only on these hosts (local testing).
_PRIVACY_URL_HTTP_HOSTS = ("localhost", "127.0.0.1")


class Settings(BaseSettings):
    """Runtime config. Surface area mirrored in the repo root's .env.example."""

    # Database — must use an async driver.
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"

    # Accounts. open: anyone may register; invite: only with INVITE_CODE;
    # closed: only the CLI (python -m redream_api.cli) creates users.
    registration_mode: Literal["open", "invite", "closed"] = "invite"
    # Required with invite, INVITE_CODE_MIN_LEN..INVITE_CODE_MAX_LEN characters,
    # or startup fails.
    invite_code: str = ""

    # Chat turns + transcriptions + rehearsal renderings per user per UTC day
    # (core/limits, in memory); 0 = off.
    daily_ai_requests_per_user: int = Field(default=100, ge=0)

    # Lifetime of the signed rehearsal-audio link (services/audio_links).
    rehearsal_url_ttl_seconds: int = Field(default=300, ge=60, le=3600)

    # Instance, shown in the app when people connect (GET /api/v1/instance).
    # Optional; blank = unset (None). OPERATOR_CONTACT is free text (an email
    # or a URL); PRIVACY_URL an absolute https:// URL (http:// only on
    # localhost / 127.0.0.1). Too long or a bad PRIVACY_URL, and startup fails.
    instance_name: str | None = None
    operator_name: str | None = None
    operator_contact: str | None = None
    privacy_url: str | None = None

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
        # A failed setting is reported without its value: INVITE_CODE and the
        # keys must not end up in a startup log.
        hide_input_in_errors=True,
    )

    @field_validator("database_url")
    @classmethod
    def _must_be_async_driver(cls, v: str) -> str:
        if not v.startswith(_ASYNC_DRIVERS):
            raise ValueError(
                # Only the scheme: the rest of the URL may hold the password.
                f"DATABASE_URL must use an async driver (one of {_ASYNC_DRIVERS}), "
                f"got: {urlsplit(v).scheme or '(no scheme)'}://…"
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

    @field_validator(
        "instance_name", "operator_name", "operator_contact", "privacy_url", mode="before"
    )
    @classmethod
    def _instance_text(cls, v: object, info: ValidationInfo) -> object:
        if not isinstance(v, str):
            return v
        field = info.field_name or ""
        text = v.strip()
        if not text:
            # INSTANCE_NAME= left blank in .env means unset, not "".
            return None
        name = field.upper()
        max_len = _INSTANCE_TEXT_MAX_LEN[field]
        if len(text) > max_len:
            raise ValueError(f"{name} is {len(text)} characters; at most {max_len} are allowed")
        if field == "privacy_url" and not _is_privacy_url(text):
            raise ValueError(
                f"{name} must be an absolute https:// URL with a host "
                f"(http:// only on {' or '.join(_PRIVACY_URL_HTTP_HOSTS)})"
            )
        return text

    @field_validator("registration_mode", mode="before")
    @classmethod
    def _normalize_registration_mode(cls, v: object) -> object:
        # REGISTRATION_MODE= left blank in .env means the default.
        return (v.strip().lower() or "invite") if isinstance(v, str) else v

    @field_validator("invite_code")
    @classmethod
    def _strip_invite_code(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def _invite_code_with_invite_mode(self) -> Self:
        if self.registration_mode == "invite" and len(self.invite_code) < INVITE_CODE_MIN_LEN:
            raise ValueError(
                f"INVITE_CODE must be at least {INVITE_CODE_MIN_LEN} characters while "
                "REGISTRATION_MODE=invite (the default); set it, or set "
                "REGISTRATION_MODE=open or REGISTRATION_MODE=closed"
            )
        if self.registration_mode == "invite" and len(self.invite_code) > INVITE_CODE_MAX_LEN:
            raise ValueError(
                f"INVITE_CODE must be at most {INVITE_CODE_MAX_LEN} characters (no one "
                "could enter a longer one)"
            )
        return self

    def helpline_text_for(self, language: str) -> str:
        """The helpline text for a chat in `language`: German for "de", else English."""
        return self.helpline_text_de if language == "de" else self.helpline_text_en


def _is_privacy_url(url: str) -> bool:
    """True for an absolute https:// URL with a host, or http:// on localhost."""
    # isprintable() rejects control, format and non-breaking-space characters.
    # A backslash or userinfo makes parsers disagree on the host (browsers
    # read "\" as "/"), so the host checked here could differ from the one opened.
    if not url.isprintable() or " " in url or "\\" in url:
        return False
    try:
        parts = urlsplit(url)
        _ = parts.port  # a malformed port raises ValueError
    except ValueError:
        return False
    if "@" in parts.netloc:
        return False
    host = parts.hostname or ""  # lower-cased by urlsplit, as is the scheme
    if parts.scheme == "http":
        return host in _PRIVACY_URL_HTTP_HOSTS
    return parts.scheme == "https" and bool(host)


settings = Settings()
