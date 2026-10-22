"""Service configuration via pydantic-settings."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Named wherever a missing chat endpoint is reported (503, readiness log, errors).
LLM_REQUIRED = "LLM_BASE_URL, LLM_API_KEY, LLM_MODEL and LLM_SMALL_MODEL"


class Settings(BaseSettings):
    """Runtime config. Values come from environment variables or .env.

    Surface area is mirrored in .env.example.
    """

    # Chat LLM: any OpenAI-compatible chat-completions endpoint, one provider
    # for every text role.
    llm_base_url: str = ""
    llm_api_key: str = ""
    # Replies, nightmare summary, summary extraction.
    llm_model: str = ""
    # Routing, talk gate, replay check, safety segmenter, clinical classifier.
    llm_small_model: str = ""
    # Crisis check (ADR 0005); empty = llm_small_model.
    llm_safety_model: str = ""
    # Per request. The SDK default (600 s) would outlast the backend's budget.
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    # The crisis check blocks the chat turn, so it gets less.
    llm_safety_timeout_seconds: float = Field(default=20.0, gt=0)
    # Send response_format json_object; off for endpoints that reject it (the
    # answers are then parsed out of prose or code fences).
    llm_json_mode: bool = True
    # Reasoning models, comma-separated, on top of the ones known by name
    # (o1, o3, o4, gpt-5 ...). They get no temperature, max_completion_tokens
    # instead of max_tokens, and LLM_REASONING_EFFORT.
    llm_reasoning_models: str = ""
    # reasoning_effort sent to reasoning models ("minimal": gpt-5 family only);
    # empty = not sent (the provider's default).
    llm_reasoning_effort: Literal["", "minimal", "low", "medium", "high"] = "low"
    # Added to a reasoning model's token limit: its reasoning counts against
    # max_completion_tokens, so the core's limits alone could leave no room for
    # the answer. A cap, not a charge; 0 = none.
    llm_reasoning_extra_tokens: int = Field(default=2048, ge=0)
    # The rewriting stage's SafetyCritic. false runs chat without it and logs a
    # warning when the core is built.
    safety_critic_enabled: bool = True

    # STT/TTS provider keys (S09 replaces them).
    mistral_api_key: str = Field(default="", description="Mistral API key (fallback path)")
    scaleway_api_key: str = Field(default="", description="Scaleway API key (primary path)")
    # Needed when the key is restricted to a project other than the
    # organization's default one; empty = default project.
    scaleway_project_id: str = ""

    # Pinned model IDs (ADR 0010 — never floating aliases for prod models).
    # Speech v3: the German preset needs it; speech v1 (voxtral-mini-tts-2603)
    # rejects it (2026-10-21).
    voxtral_tts_model: str = "voxtral-mini-tts-3-0"
    # STT (ADR 0010, 2026-07-09 amendment): Whisper on Scaleway primary,
    # Voxtral Transcribe V2 on direct Mistral fallback. The Scaleway ID format
    # (org/model:quant) is verbatim from their audio how-to docs.
    whisper_stt_model: str = "openai/whisper-large-v3:fp16"
    voxtral_stt_model: str = "voxtral-mini-2602"

    # TTS needs a voice: a preset slug (GET /v1/audio/voices) or a saved
    # voice id. German: the preset "Heidi" (added 2026-10-19), verified to
    # transcribe back word for word (2026-10-21).
    voxtral_voice_id_en: str = "gb_jane_neutral"
    voxtral_voice_id_de: str = "voice_481"

    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_storage_bucket: str = "rehearsal-audio"
    # Host the app can reach, when SUPABASE_URL is an internal compose address.
    supabase_public_url: str = ""
    # Lifetime of the signed rehearsal-audio URL stored on the dream.
    rehearsal_url_ttl_seconds: int = Field(default=300, ge=60, le=3600)

    environment: str = "development"
    log_level: str = "INFO"

    @property
    def llm_configured(self) -> bool:
        """True once the endpoint and both chat models are set."""
        return bool(
            self.llm_base_url and self.llm_api_key and self.llm_model and self.llm_small_model
        )

    @property
    def safety_model(self) -> str:
        """The crisis classifier's model: LLM_SAFETY_MODEL, else LLM_SMALL_MODEL."""
        return self.llm_safety_model or self.llm_small_model

    @property
    def reasoning_models(self) -> frozenset[str]:
        """The model names listed in LLM_REASONING_MODELS."""
        return frozenset(m.strip() for m in self.llm_reasoning_models.split(",") if m.strip())

    @field_validator("llm_reasoning_effort", mode="before")
    @classmethod
    def _normalize_reasoning_effort(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("rehearsal_url_ttl_seconds", mode="before")
    @classmethod
    def _migrate_audio_ttl(cls, value: object) -> object:
        return 300 if isinstance(value, (str, int)) and int(value) > 3600 else value

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
