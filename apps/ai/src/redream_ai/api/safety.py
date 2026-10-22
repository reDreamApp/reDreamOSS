"""POST /safety/classify — crisis pre-classifier (ADR 0005).

Backend must call this BEFORE every /chat turn. High-risk responses short-circuit
the IRT machine and trigger a helpline overlay client-side.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..config import settings
from ..llm.base import LlmClient
from ..models import SafetyRequest, SafetyResponse
from ..safety import classify
from .chat import get_llm_client, get_scaleway_llm_client

router = APIRouter()


@router.post("/safety/classify", response_model=SafetyResponse)
async def safety_classify(
    req: SafetyRequest,
    llm: LlmClient = Depends(get_scaleway_llm_client),
    fallback_llm: LlmClient = Depends(get_llm_client),
) -> SafetyResponse:
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
