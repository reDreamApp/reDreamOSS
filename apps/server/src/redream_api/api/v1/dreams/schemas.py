"""Dreams schemas. Rehearsal output fields are read-only on input bodies."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

REHEARSAL_OUTPUT_FIELDS = (
    "rehearsal_audio_url",
    "rehearsal_script",
    "rehearsal_duration",
    "rehearsal_language",
    "rehearsal_generated_at",
)
# The column behind rehearsal_audio_url is set by the rehearsal route alone.
_REJECTED_INPUT_FIELDS = (*REHEARSAL_OUTPUT_FIELDS, "rehearsal_audio_key")


def _reject_rehearsal_fields(values: object) -> object:
    if isinstance(values, dict):
        present = sorted(f for f in _REJECTED_INPUT_FIELDS if values.get(f) is not None)
        if present:
            raise ValueError(
                f"{present} are output-only. Use POST /dreams/{{id}}/rehearsal to generate them."
            )
    return values


class DreamCreate(BaseModel):
    title: str
    original_dream: str
    rewritten_dream: str | None = None
    intensity: int | None = None
    is_nightmare: bool = False
    is_current_dream: bool = False
    image_url: str | None = None
    session_id: str | None = None
    source: str = "manual"
    language: str | None = Field(default=None, pattern="^(en|de)$")

    @model_validator(mode="before")
    @classmethod
    def _no_rehearsal(cls, v: Any) -> Any:
        return _reject_rehearsal_fields(v)


class DreamUpdate(BaseModel):
    title: str | None = None
    original_dream: str | None = None
    rewritten_dream: str | None = None
    intensity: int | None = None
    is_nightmare: bool | None = None
    image_url: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _no_rehearsal(cls, v: Any) -> Any:
        return _reject_rehearsal_fields(v)


class DreamOut(BaseModel):
    id: int
    title: str
    original_dream: str
    rewritten_dream: str | None = None
    intensity: int | None = None
    is_nightmare: bool
    created_at: datetime
    last_edited: datetime
    practice_timestamps: list[Any] = Field(default_factory=list)
    is_current_dream: bool
    image_url: str | None = None
    session_id: str | None = None
    source: str
    language: str | None = None
    rehearsal_audio_url: str | None = None
    rehearsal_script: str | None = None
    rehearsal_duration: str | None = None
    rehearsal_language: str | None = None
    rehearsal_generated_at: datetime | None = None


class RehearsalGenerateRequest(BaseModel):
    language: str = Field(pattern="^(en|de)$")
