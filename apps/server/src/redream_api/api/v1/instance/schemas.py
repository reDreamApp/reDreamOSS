"""Instance info schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class InstanceFeaturesOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat: bool
    """The chat LLM is configured (LLM_BASE_URL, LLM_API_KEY, LLM_MODEL,
    LLM_SMALL_MODEL)."""
    transcription: bool
    """Speech input is on: POST /audio/transcribe answers."""
    rehearsal_audio: bool
    """Speech output is on: POST /dreams/{id}/rehearsal answers."""


class InstanceOperatorOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None
    contact: str | None
    """Free text: an email or a URL."""
    privacy_url: str | None


class InstanceOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None
    api_version: int
    consent_version: str
    """The consent-text version a grant must name (core/consent)."""
    registration: Literal["open", "invite", "closed", "personal"]
    daily_ai_requests_per_user: int
    """0 = no cap."""
    features: InstanceFeaturesOut
    operator: InstanceOperatorOut
