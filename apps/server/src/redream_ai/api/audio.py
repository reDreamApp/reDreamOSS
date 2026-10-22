"""transcribe + rehearsal_generate — STT/TTS (ADR 0010).

Both raise LlmUnavailableError while their half of speech is off
(`settings.stt_enabled` / `settings.tts_enabled`); the backend checks those
first and answers 503 ``voice_unavailable`` instead of calling.
"""

from __future__ import annotations

from fastapi import HTTPException

from ..audio import stt, tts
from ..models import RehearsalRequest, RehearsalResponse, TranscribeResponse

# Same cap as the backend's upload route; a larger body never reaches STT.
MAX_AUDIO_BYTES = 25 * 1024 * 1024


async def transcribe(*, file_name: str, audio_bytes: bytes) -> TranscribeResponse:
    if not file_name:
        raise HTTPException(400, "filename required")
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "audio exceeds 25 MB")
    return await stt.transcribe(audio_bytes=audio_bytes, file_name=file_name)


async def rehearsal_generate(req: RehearsalRequest) -> RehearsalResponse:
    if not req.rewritten_dream.strip():
        raise HTTPException(400, "rewritten_dream must not be empty")
    return await tts.generate_rehearsal(req)
