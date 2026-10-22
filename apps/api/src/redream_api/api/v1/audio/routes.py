"""POST /audio/transcribe — dream voice journaling (ADR 0010).

Proxies the AI service's STT (Whisper-on-Scaleway primary, Voxtral fallback).
The transcript is returned to the client for the composer — nothing is
persisted here, and the audio bytes are discarded by both services.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from ....core.dependencies import require_consent
from ....services.ai_client import AIServiceError, BaseAiClient, get_ai_client
from .schemas import TranscribeOut

log = logging.getLogger(__name__)

router = APIRouter(prefix="/audio", tags=["audio"])

# A dream narration is a few minutes of speech; 25 MB (the common Whisper
# service cap) is generous for that and bounds memory per request.
MAX_AUDIO_BYTES = 25 * 1024 * 1024


@router.post(
    "/transcribe",
    response_model=TranscribeOut,
    dependencies=[Depends(require_consent)],
)
async def transcribe(
    file: UploadFile = File(...),
    ai: BaseAiClient = Depends(get_ai_client),
) -> TranscribeOut:
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "filename required")

    audio_bytes = await file.read(MAX_AUDIO_BYTES + 1)
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"audio exceeds {MAX_AUDIO_BYTES // (1024 * 1024)} MB",
        )

    try:
        result = await ai.transcribe(file_name=file.filename, audio_bytes=audio_bytes)
    except AIServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"transcribe: {exc}") from exc

    return TranscribeOut(
        text=str(result.get("text", "")),
        language_detected=result.get("language_detected"),
    )
