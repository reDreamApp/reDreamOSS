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

# setdefault: a real CI/env value still wins.
os.environ.setdefault("LLM_BASE_URL", "http://llm.invalid/v1")
os.environ.setdefault("LLM_API_KEY", "dummy")
os.environ.setdefault("LLM_MODEL", "m-large")
os.environ.setdefault("LLM_SMALL_MODEL", "m-small")
# STT/TTS still read the provider keys (until S09): throwaway values here too.
os.environ.setdefault("SCALEWAY_API_KEY", "test-dummy-key")
os.environ.setdefault("MISTRAL_API_KEY", "test-dummy-key")
# A local .env may switch these off; tests run with the production defaults.
os.environ["SAFETY_CRITIC_ENABLED"] = "true"
os.environ["LLM_JSON_MODE"] = "true"
os.environ["LLM_REASONING_MODELS"] = ""
os.environ["LLM_REASONING_EFFORT"] = "low"
os.environ["LLM_REASONING_EXTRA_TOKENS"] = "2048"

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


@pytest.fixture
def fake_llm() -> FakeLlmClient:
    """A fresh FakeLlmClient. Enqueue responses in the test body."""
    return FakeLlmClient()
