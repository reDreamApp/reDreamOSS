"""Service configuration via pydantic-settings."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Named wherever a missing chat endpoint is reported (503, readiness log, errors).
LLM_REQUIRED = "LLM_BASE_URL, LLM_API_KEY, LLM_MODEL and LLM_SMALL_MODEL"

# Named wherever switched-off speech is reported (readiness log, errors).
STT_REQUIRED = "STT_MODEL (and STT_BASE_URL/STT_API_KEY or LLM_BASE_URL/LLM_API_KEY)"
TTS_REQUIRED = (
    "TTS_MODEL, TTS_VOICE or TTS_VOICE_EN and TTS_VOICE_DE "
    "(and TTS_BASE_URL/TTS_API_KEY or LLM_BASE_URL/LLM_API_KEY)"
)

# The languages rehearsal audio is rendered in (models.Language); TTS is on
# only while each has a voice.
TTS_LANGUAGES = ("en", "de")


def _host(url: str) -> str:
    """Host and port of `url`, lower-cased ("" for an empty or bare value)."""
    return urlsplit(url.strip()).netloc.lower()


def _borrows_llm_key(own_key: str, base: str, llm_base: str) -> bool:
    """True when an empty speech key falls back to LLM_API_KEY although the
    speech endpoint is on another host than LLM_BASE_URL."""
    return bool(not own_key.strip() and base and _host(base) != _host(llm_base))


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

    # Speech input (voice journaling): multipart POST {base}/audio/transcriptions.
    # Empty base URL / key = LLM_BASE_URL / LLM_API_KEY; empty model = off.
    stt_base_url: str = ""
    stt_api_key: str = ""
    stt_model: str = ""

    # Speech output (rehearsal audio): POST {base}/audio/speech, in the request
    # and response shape of TTS_API_STYLE. Empty base URL / key = LLM_*; empty
    # model = off. TTS_VOICE serves every language unless TTS_VOICE_<LANG>
    # overrides it; off as well while a language has no voice. Another
    # TTS_API_STYLE than openai/mistral stops startup, as a bad
    # LLM_REASONING_EFFORT does: a typo fails loudly, not as silent "off".
    tts_base_url: str = ""
    tts_api_key: str = ""
    tts_model: str = ""
    tts_api_style: Literal["openai", "mistral"] = "openai"
    tts_voice: str = ""
    tts_voice_en: str = ""
    tts_voice_de: str = ""

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

    @property
    def stt_base(self) -> str:
        """The STT endpoint's base URL: STT_BASE_URL, else LLM_BASE_URL."""
        return (self.stt_base_url.strip() or self.llm_base_url.strip()).rstrip("/")

    @property
    def stt_key(self) -> str:
        """STT_API_KEY, else LLM_API_KEY — on LLM_BASE_URL's host only: the
        LLM key is never sent to another provider."""
        if self.stt_api_key.strip():
            return self.stt_api_key.strip()
        return "" if self.stt_borrows_llm_key else self.llm_api_key.strip()

    @property
    def stt_borrows_llm_key(self) -> bool:
        """True when STT_API_KEY is empty and STT_BASE_URL is on another host
        than LLM_BASE_URL: LLM_API_KEY is then not used, so transcription stays
        off (warned about in log_readiness)."""
        return _borrows_llm_key(self.stt_api_key, self.stt_base, self.llm_base_url)

    @property
    def stt_enabled(self) -> bool:
        """True once STT_MODEL is set and a base URL and a key resolve."""
        return bool(self.stt_model.strip() and self.stt_base and self.stt_key)

    @property
    def tts_base(self) -> str:
        """The TTS endpoint's base URL: TTS_BASE_URL, else LLM_BASE_URL."""
        return (self.tts_base_url.strip() or self.llm_base_url.strip()).rstrip("/")

    @property
    def tts_key(self) -> str:
        """TTS_API_KEY, else LLM_API_KEY — on LLM_BASE_URL's host only (see
        stt_key)."""
        if self.tts_api_key.strip():
            return self.tts_api_key.strip()
        return "" if self.tts_borrows_llm_key else self.llm_api_key.strip()

    @property
    def tts_borrows_llm_key(self) -> bool:
        """True when TTS_API_KEY is empty and TTS_BASE_URL is on another host
        than LLM_BASE_URL: speech output then stays off (see stt_borrows_llm_key)."""
        return _borrows_llm_key(self.tts_api_key, self.tts_base, self.llm_base_url)

    def tts_voice_for(self, language: str) -> str:
        """The voice for `language` ("en", "de"): TTS_VOICE_<LANG>, else TTS_VOICE."""
        override = {"en": self.tts_voice_en, "de": self.tts_voice_de}.get(language, "")
        return override.strip() or self.tts_voice.strip()

    @property
    def tts_enabled(self) -> bool:
        """True once TTS_MODEL is set, a base URL and a key resolve, and every
        supported language has a voice."""
        return bool(
            self.tts_model.strip()
            and self.tts_base
            and self.tts_key
            and all(self.tts_voice_for(lang) for lang in TTS_LANGUAGES)
        )

    @field_validator("llm_reasoning_effort", mode="before")
    @classmethod
    def _normalize_reasoning_effort(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("tts_api_style", mode="before")
    @classmethod
    def _normalize_tts_api_style(cls, value: object) -> object:
        # TTS_API_STYLE= (empty) means the default, like the other speech keys.
        return (value.strip().lower() or "openai") if isinstance(value, str) else value

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
