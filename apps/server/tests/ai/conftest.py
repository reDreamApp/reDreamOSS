"""pytest fixtures for the AI service.

IMPORTANT: the ported IRT core (`redream_ai.core.irt_app`) builds its
Scaleway/Mistral agents eagerly at import, from the process environment (it does
not load `.env`). We pin throwaway keys here — BEFORE any `redream_ai` import —
so the suite never makes a network call. Every test that exercises the pipeline
mocks the LLM layer; real keys are only for the manual end-to-end smoke runs.
"""

from __future__ import annotations

import os

# setdefault: a real CI/env value still wins.
os.environ.setdefault("SCALEWAY_API_KEY", "test-dummy-key")
os.environ.setdefault("MISTRAL_API_KEY", "test-dummy-key")

import pytest

from redream_ai.llm.fake_client import FakeLlmClient


@pytest.fixture
def fake_llm() -> FakeLlmClient:
    """A fresh FakeLlmClient. Enqueue responses in the test body."""
    return FakeLlmClient()
