"""Liveness probe.

Answers 200 {"status": "ok"} whenever the process is up, configured or not;
llm_ready (the same check as /instance's features.chat) and
safety_classifier_enabled tell what is missing. No database access.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...core.config import settings
from ...services.ai_client import BaseAiClient, get_ai_client

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(ai: BaseAiClient = Depends(get_ai_client)) -> dict[str, str | bool]:
    return {
        "status": "ok",
        "llm_ready": ai.llm_configured,
        "safety_classifier_enabled": settings.safety_classifier_enabled,
    }
