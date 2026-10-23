"""pytest fixtures for the AI service.

IMPORTANT: the ported IRT core (`redream_ai.core.irt_app`) builds its agents
eagerly at import, from the LLM_* settings. We pin a throwaway endpoint here —
BEFORE any `redream_ai` import; env vars beat `.env` — so the suite never makes
a network call. Every test that exercises the pipeline mocks the LLM layer; real
values are only for the manual end-to-end smoke runs and evals/.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

# setdefault: a real CI/env value still wins.
os.environ.setdefault("LLM_BASE_URL", "http://llm.invalid/v1")
os.environ.setdefault("LLM_API_KEY", "dummy")
os.environ.setdefault("LLM_MODEL", "m-large")
os.environ.setdefault("LLM_SMALL_MODEL", "m-small")
# Speech is off unless a test switches it on; a local .env may configure it.
os.environ.update(
    dict.fromkeys(
        (
            "STT_BASE_URL",
            "STT_API_KEY",
            "STT_MODEL",
            "TTS_BASE_URL",
            "TTS_API_KEY",
            "TTS_MODEL",
            "TTS_VOICE",
            "TTS_VOICE_EN",
            "TTS_VOICE_DE",
        ),
        "",
    )
)
os.environ["TTS_API_STYLE"] = "openai"
# A local .env may switch these off; tests run with the production defaults.
os.environ["SAFETY_CRITIC_ENABLED"] = "true"
os.environ["LLM_JSON_MODE"] = "true"
os.environ["LLM_REASONING_MODELS"] = ""
os.environ["LLM_REASONING_EFFORT"] = "low"
os.environ["LLM_REASONING_EXTRA_TOKENS"] = "2048"
# A local .env may set an operator's helpline reference; tests use the defaults.
os.environ["HELPLINE_PROMPT_EN"] = ""
os.environ["HELPLINE_PROMPT_DE"] = ""
# redream_api's settings (loaded when a test imports it) refuse invite mode
# without a code; a local .env may choose another mode.
os.environ["REGISTRATION_MODE"] = "invite"
os.environ["INVITE_CODE"] = "test-invite-code"

import pytest

from redream_ai.llm import chat_request
from redream_ai.llm.fake_client import FakeLlmClient


@pytest.fixture(autouse=True)
def _no_remembered_adjustments() -> Iterator[None]:
    """Every test starts with no parameter adjustment remembered by another:
    chat_request keeps them (and the warnings given) for the process."""
    chat_request._adjusted.clear()
    chat_request._warned.clear()
    chat_request._warned_out_of_tokens.clear()
    yield
    chat_request._adjusted.clear()
    chat_request._warned.clear()
    chat_request._warned_out_of_tokens.clear()


@pytest.fixture(autouse=True)
def audio_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """AUDIO_DIR for this test: a fresh folder under tmp_path, never the repo."""
    from redream_ai.config import settings

    path = tmp_path.resolve() / "audio"
    monkeypatch.setattr(settings, "audio_dir", path)
    return path


@pytest.fixture
def fake_llm() -> FakeLlmClient:
    """A fresh FakeLlmClient. Enqueue responses in the test body."""
    return FakeLlmClient()
