"""pytest fixtures for the AI service.

IMPORTANT: the ported IRT core (`redream_ai.core.irt_app`) builds its agents
eagerly at import, from the LLM_* settings. We pin a throwaway endpoint here —
BEFORE any `redream_ai` import; env vars beat `.env` — so the suite never makes
a network call. Every test that exercises the pipeline mocks the LLM layer; real
values are only for the manual end-to-end smoke runs and evals/.
"""

from __future__ import annotations

import os

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

import pytest

from redream_ai.llm.fake_client import FakeLlmClient


@pytest.fixture
def fake_llm() -> FakeLlmClient:
    """A fresh FakeLlmClient. Enqueue responses in the test body."""
    return FakeLlmClient()
