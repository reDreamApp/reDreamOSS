"""Public wire types for the AI service.

Field names are part of the AI <-> backend JSON contract. Changes here ripple
to `docs/contracts.md` and the backend's `ai_client` module — keep them
deliberate.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Stage(StrEnum):
    """Wire-contract IRT stages.

    The ported core routes only `recording → rewriting → summary → final`
    (`redream_ai.core.models.Stage`). `INTENSITY` is retained here for backward
    wire compatibility (older backend/app history may carry it); the ported flow
    never emits it — the 0-10 rating lives on `SummaryStructured.intensity`.
    """

    RECORDING = "recording"
    INTENSITY = "intensity"
    REWRITING = "rewriting"
    SUMMARY = "summary"
    FINAL = "final"


class Language(StrEnum):
    EN = "en"
    DE = "de"


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class Message(BaseModel):
    """A single conversation turn.

    `stage` is the IRT stage the message belongs to. Persisted by the backend;
    the AI service receives messages on input and emits one on output, but
    never stores anything between requests (ADR 0002).
    """

    role: Role
    content: str
    stage: Stage | None = None
    language: Language | None = None
    timestamp: datetime | None = None


# ---------------------------------------------------------------------------
# /chat — IRT turn
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """Full conversation history per request (ADR 0002 — stateless AI)."""

    messages: list[Message]
    language: Language
    session_id: str | None = Field(
        default=None,
        description=(
            "Optional opaque chat ID; the ported core keys its conversation by it. "
            "Not used for state."
        ),
    )
    nightmare_summary: str | None = Field(
        default=None,
        description=(
            "The nightmare summary the AI generated once at the recording→rewriting "
            "transition. The backend persists it and echoes it back on every "
            "subsequent turn so the AI doesn't regenerate it each rewriting turn "
            "(keeps ADR 0002 statelessness while honouring 'generated exactly once'). "
            "None on turns before the summary exists."
        ),
    )


class SummaryStructured(BaseModel):
    """Structured fields emitted when stage == SUMMARY (ADR 0003).

    Backend persists these directly into the `dreams` table; no string-matching.
    """

    title: str
    original_summary: str
    rewritten_summary: str
    dream_summary: str
    intensity: int | None = Field(
        default=None,
        ge=0,
        le=10,
        description="The 0-10 distress rating the user gave, if they gave one.",
    )


class UsageStats(BaseModel):
    input_tokens: int
    output_tokens: int


class ChatResponse(BaseModel):
    """Non-streaming chat response.

    For streaming (ADR 0009), POST /chat/stream emits content chunks, then one
    ``data: {"final": <this model as JSON>}`` frame, then the ``data: [DONE]``
    sentinel — full frame contract in ``redream_ai.api.chat``'s module docstring.
    """

    stage: Stage
    response: str
    structured: SummaryStructured | None = None
    usage: UsageStats
    prompt_version: str
    safety: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Clinical rewrite-safety verdict from the neuro-symbolic SafetyCritic. "
            "Populated only during the `rewriting` stage; None otherwise or when the "
            "critic is unavailable (None means 'not evaluated', not 'safe')."
        ),
    )
    nightmare_summary: str | None = Field(
        default=None,
        description=(
            "The once-generated nightmare summary, echoed so the backend can persist "
            "it and send it back next turn (see ChatRequest.nightmare_summary)."
        ),
    )

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# /safety/classify — crisis pre-classifier (ADR 0005)
# ---------------------------------------------------------------------------


class SafetyRisk(StrEnum):
    NONE = "none"
    LOW = "low"
    HIGH = "high"


class SafetyCategory(StrEnum):
    SUICIDAL_IDEATION = "suicidal_ideation"
    SELF_HARM = "self_harm"
    ABUSE_DISCLOSURE = "abuse_disclosure"
    CHILD_SAFETY = "child_safety"
    IMMINENT_DANGER = "imminent_danger"
    VIOLENCE_TO_OTHERS = "violence_to_others"


class SafetyRequest(BaseModel):
    text: str
    language: Language
    stage: Stage | None = Field(
        default=None,
        description="IRT stage the message was sent in, for classifier context.",
    )
    context: str | None = Field(
        default=None,
        description=(
            "The assistant message the user is replying to. Without it, terse "
            "answers ('It is an 8 out of 10.') are uninterpretable."
        ),
    )


class SafetyResponse(BaseModel):
    risk: SafetyRisk
    categories: list[SafetyCategory] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    prompt_version: str


# ---------------------------------------------------------------------------
# /audio/transcribe — Voxtral STT (ADR 0010)
# ---------------------------------------------------------------------------


class TranscribeResponse(BaseModel):
    text: str
    language_detected: Language | None = None
    input_tokens: int = 0
    output_tokens: int = 0


# ---------------------------------------------------------------------------
# /rehearsal/generate — Voxtral TTS + Supabase Storage upload
# ---------------------------------------------------------------------------


class RehearsalRequest(BaseModel):
    dream_id: str
    user_id: str = Field(description="Owner; prefixes the Storage path for the GDPR purge.")
    rewritten_dream: str
    language: Language
    max_synthesis_attempts: int = Field(default=2, ge=1, le=2)


class RehearsalResponse(BaseModel):
    audio_url: str = Field(description="Signed Supabase Storage URL for the generated MP3.")
    script: str = Field(description="Text used as TTS input (may be post-processed).")
    duration_estimate: str = Field(description="mm:ss estimate of playback duration.")
    voice_id: str
    cache_hit: bool = False
    synthesis_attempts: int = Field(default=0, ge=0, le=2)
