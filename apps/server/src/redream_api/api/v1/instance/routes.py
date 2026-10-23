"""What this server is and offers — read by the app when people connect.

No login, no database, no consent gate: the app asks before anyone signs in.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from ....core import consent
from ....core.config import settings
from ....services.ai_client import BaseAiClient, get_ai_client
from .schemas import InstanceFeaturesOut, InstanceOperatorOut, InstanceOut

router = APIRouter(tags=["instance"])

# The HTTP API's version, as the app checks it: bump on a breaking change.
API_VERSION = 1


@router.get("/instance", response_model=InstanceOut)
async def get_instance(
    response: Response,
    ai: BaseAiClient = Depends(get_ai_client),
) -> InstanceOut:
    # Settings and voice can change with a restart: always revalidate.
    response.headers["Cache-Control"] = "no-cache"
    return InstanceOut(
        name=settings.instance_name,
        api_version=API_VERSION,
        consent_version=consent.CONSENT_VERSION,
        registration=settings.registration_mode,
        daily_ai_requests_per_user=settings.daily_ai_requests_per_user,
        features=InstanceFeaturesOut(
            chat=ai.llm_configured,
            transcription=ai.stt_enabled,
            rehearsal_audio=ai.tts_enabled,
        ),
        operator=InstanceOperatorOut(
            name=settings.operator_name,
            contact=settings.operator_contact,
            privacy_url=settings.privacy_url,
        ),
    )
