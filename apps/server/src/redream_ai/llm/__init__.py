"""LLM client abstraction.

Routes accept an `LlmClient` and never import a provider SDK directly. Tests
inject `FakeLlmClient`. Production uses `OpenAICompatibleLlmClient` on the
LLM_* endpoint.
"""

from .base import LlmClient, StreamChunk
from .fake_client import FakeLlmClient

__all__ = ["FakeLlmClient", "LlmClient", "StreamChunk"]
