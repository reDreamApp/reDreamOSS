"""pytest fixtures for the AI service.

IMPORTANT: the ported IRT core (`redream_ai.core.irt_app`) calls
`load_dotenv(find_dotenv())` at import and builds its Scaleway/Mistral agents
eagerly. We pin throwaway keys here — BEFORE any `redream_ai` import — so the
suite never loads the real `apps/ai/.env` and never makes a network call. Every
test that exercises the pipeline mocks the LLM layer; real keys are only for the
manual end-to-end smoke runs.
"""

from __future__ import annotations

import os

# setdefault: a real CI/env value still wins; load_dotenv(override=False) won't
# clobber these, so the real .env keys never leak into the test process.
os.environ.setdefault("SCALEWAY_API_KEY", "test-dummy-key")
os.environ.setdefault("MISTRAL_API_KEY", "test-dummy-key")

import pytest

from redream_ai.llm.fake_client import FakeLlmClient


@pytest.fixture
def fake_llm() -> FakeLlmClient:
    """A fresh FakeLlmClient. Enqueue responses in the test body."""
    return FakeLlmClient()
