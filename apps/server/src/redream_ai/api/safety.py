"""safety_classify — crisis pre-classifier (ADR 0005).

Backend must call this BEFORE every chat turn. High-risk responses short-circuit
the IRT machine and trigger a helpline overlay client-side.
"""

from __future__ import annotations

from fastapi import HTTPException

from ..config import settings
from ..llm.base import LlmClient
from ..llm.openai_compatible import OpenAICompatibleLlmClient
from ..models import SafetyRequest, SafetyResponse
from ..safety import classify


def build_safety_llm_client() -> LlmClient | None:
    """The classifier's client on the LLM_* endpoint, with the shorter
    LLM_SAFETY_TIMEOUT_SECONDS; None until the endpoint is configured."""
    if not settings.llm_configured:
        return None
    return OpenAICompatibleLlmClient(
        settings.llm_base_url,
        settings.llm_api_key,
        timeout=settings.llm_safety_timeout_seconds,
    )


async def safety_classify(req: SafetyRequest, *, llm: LlmClient) -> SafetyResponse:
    """`llm` is the client the backend builds once (`build_safety_llm_client`).
    No fallback: a failed check fails the chat turn (fail-closed)."""
    if not req.text.strip():
        raise HTTPException(400, "text must not be empty")

    return await classify(
        llm=llm,
        model=settings.safety_model,
        json_mode=settings.llm_json_mode,
        text=req.text,
        language=req.language,
        stage=req.stage,
        context=req.context,
    )
