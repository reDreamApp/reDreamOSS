"""safety_classify — crisis pre-classifier (ADR 0005).

Backend must call this BEFORE every chat turn. High-risk responses short-circuit
the IRT machine and trigger a helpline overlay client-side.
"""

from __future__ import annotations

from fastapi import HTTPException

from ..config import settings
from ..llm.base import LlmClient
from ..models import SafetyRequest, SafetyResponse
from ..safety import classify


def build_scaleway_llm_client() -> LlmClient | None:
    """The classifier's primary, Scaleway; None if no API key is set."""
    if not settings.scaleway_api_key:
        return None
    from ..llm.scaleway_client import ScalewayLlmClient

    return ScalewayLlmClient(
        api_key=settings.scaleway_api_key, project_id=settings.scaleway_project_id
    )


def build_mistral_llm_client() -> LlmClient | None:
    """The classifier's fallback, direct Mistral; None if no API key is set."""
    if not settings.mistral_api_key:
        return None
    from ..llm.mistral_client import MistralLlmClient

    return MistralLlmClient(api_key=settings.mistral_api_key)


async def safety_classify(
    req: SafetyRequest,
    *,
    llm: LlmClient,
    fallback_llm: LlmClient,
) -> SafetyResponse:
    """`llm` is the Scaleway primary, `fallback_llm` the direct-Mistral fallback.
    The backend builds both once (`build_*_llm_client`) and passes them in."""
    if not req.text.strip():
        raise HTTPException(400, "text must not be empty")

    return await classify(
        llm=llm,
        model=settings.scaleway_safety_model,
        fallback_llm=fallback_llm,
        fallback_model=settings.mistral_safety_model,
        text=req.text,
        language=req.language,
        stage=req.stage,
        context=req.context,
    )
