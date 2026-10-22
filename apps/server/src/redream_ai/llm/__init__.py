"""LLM client abstraction.

Routes accept an `LlmClient` and never import the Mistral SDK directly. Tests
inject `FakeLlmClient`. Production uses `ScalewayLlmClient` with
`MistralLlmClient` as fallback.
"""

from .base import LlmClient, StreamChunk
from .fake_client import FakeLlmClient

__all__ = ["FakeLlmClient", "LlmClient", "StreamChunk"]
