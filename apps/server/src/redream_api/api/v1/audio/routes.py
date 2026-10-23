"""POST /audio/transcribe — dream voice journaling (ADR 0010).
GET (and HEAD) /audio/files/{key} — rehearsal MP3s, behind a signed link.

Transcribe hands the upload to redream_ai's STT (the STT_* provider). The
transcript is returned to the client for the composer — nothing is persisted,
and the audio bytes are discarded after transcription.

While speech input is off (no STT_MODEL), the route answers 503
``voice_unavailable`` — after the consent gate, so a user without consent
still gets 403 ``consent_required`` first.

The file route takes no auth header and no consent check: audio players send
neither. The link's signature (services/audio_links) is the authorization;
the dream routes hand out fresh links.
"""

from __future__ import annotations

import asyncio
import logging
import os
import stat
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from ....core.dependencies import require_consent
from ....services import audio_links
from ....services.ai_client import (
    VOICE_UNAVAILABLE,
    AIServiceError,
    BaseAiClient,
    get_ai_client,
)
from ....services.rehearsal_audio import AUDIO_NOT_READ
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
    if not ai.stt_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, VOICE_UNAVAILABLE)
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


# One answer for every refusal: it does not say which check failed.
_FORBIDDEN = "invalid or expired link"


@router.api_route("/files/{key:path}", methods=["GET", "HEAD"], response_class=FileResponse)
async def audio_file(key: str, exp: str = "", sig: str = "") -> FileResponse:
    from redream_ai.audio import store

    if not store.is_key(key) or not audio_links.verify(key, exp, sig):
        raise HTTPException(status.HTTP_403_FORBIDDEN, _FORBIDDEN)
    try:
        found = await asyncio.to_thread(_existing_file, key)
    except ValueError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, _FORBIDDEN) from exc
    except OSError as exc:
        # Not the path: it names the user.
        log.error("rehearsal audio could not be read (%s)", type(exc).__name__)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, AUDIO_NOT_READ) from exc
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "audio not found")
    path, stat_result = found
    # FileResponse answers Range requests (206 + Content-Range) and HEAD itself;
    # the stat taken here spares it a second one that could fail with the path.
    return FileResponse(
        path,
        media_type="audio/mpeg",
        stat_result=stat_result,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


def _existing_file(key: str) -> tuple[Path, os.stat_result] | None:
    """`key`'s file and its stat, None when missing. Raises ValueError for an
    unsafe path, OSError when the file cannot be checked."""
    from redream_ai.audio import store

    path = store.path_for(key)
    try:
        stat_result = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    return (path, stat_result) if stat.S_ISREG(stat_result.st_mode) else None
