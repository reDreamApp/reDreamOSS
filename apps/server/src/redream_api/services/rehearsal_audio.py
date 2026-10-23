"""Rehearsal audio on the local disk: what the dream and user routes need.

redream_ai writes the MP3s (`redream_ai.audio.store`); the dream row keeps
the key. This module checks a dream's file, resets a dream's rehearsal fields
and deletes the dream's or the user's folder — the blocking filesystem work
in a worker thread, off the event loop. redream_ai's store is imported on
first use, as everywhere in the backend.

Deletion raises (OSError, or ValueError for an unsafe path); the routes that
delete rows answer 503 then and leave the database untouched, so audio never
outlives its dream or account.

A rendering can still be running when its dream or account is deleted, and
then writes its file after the folder is gone. So every deletion first bumps
a counter (the user's, or the dream's), then deletes; the rehearsal route
reads the counters before it asks for audio and again once it has the file,
and deletes the file itself when they changed (`audio_epoch`,
`drop_if_stale`). Whichever comes last removes the file. The counters live
in this process, which is why the server runs as one worker.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any, TypeGuard
from uuid import UUID

from ..db.models.dream import Dream

log = logging.getLogger(__name__)

# The 503 detail when a dream's or user's audio folder cannot be deleted.
AUDIO_NOT_DELETED = "audio could not be deleted, try again"
# The 503 detail when a dream's audio file cannot be checked.
AUDIO_NOT_READ = "audio could not be read, try again"

# Deletions per user and per dream since start; one int per deleted or reset
# dream, never reused (user ids are UUIDs, dream ids a sequence).
_user_deletions: dict[UUID, int] = {}
_dream_deletions: dict[tuple[UUID, int], int] = {}


def audio_epoch(user_id: UUID, dream_id: int) -> tuple[int, int]:
    """The deletion counters of this user and dream, to compare later."""
    return _user_deletions.get(user_id, 0), _dream_deletions.get((user_id, dream_id), 0)


async def drop_if_stale(user_id: UUID, dream_id: int, started: tuple[int, int]) -> bool:
    """After a rendering: when the user's or the dream's audio was deleted
    since `started`, delete what the rendering may have written (the user's
    folder, or the dream's) and return True. A failure here is logged."""
    users, dreams = audio_epoch(user_id, dream_id)
    if users != started[0]:
        await _discard(delete_user_audio(user_id))
    elif dreams != started[1]:
        await _discard(delete_dream_audio(user_id, dream_id))
    else:
        return False
    return True


def clear_rehearsal(dream: Dream) -> bool:
    """Reset the five rehearsal fields. True when any was set."""
    had_any = any(
        value is not None
        for value in (
            dream.rehearsal_audio_key,
            dream.rehearsal_script,
            dream.rehearsal_duration,
            dream.rehearsal_language,
            dream.rehearsal_generated_at,
        )
    )
    dream.rehearsal_audio_key = None
    dream.rehearsal_script = None
    dream.rehearsal_duration = None
    dream.rehearsal_language = None
    dream.rehearsal_generated_at = None
    return had_any


def owns_key(key: object, user_id: UUID, dream_id: int) -> TypeGuard[str]:
    """True when `key` is a valid audio key of this user's dream."""
    from redream_ai.audio import store

    return store.belongs_to(key, user_id, dream_id)


async def audio_exists(dream: Dream) -> bool:
    """True when the dream's stored key is one of its own and the file exists.
    Raises OSError when the file cannot be checked (not when it is missing)."""
    from redream_ai.audio import store

    key = dream.rehearsal_audio_key
    if not store.belongs_to(key, dream.user_id, dream.id):
        return False
    try:
        return await asyncio.to_thread(store.exists, key)
    except ValueError:  # a symlink on the way: as good as missing
        return False


async def delete_dream_audio(user_id: UUID, dream_id: int) -> None:
    from redream_ai.audio import store

    # Count first: a rendering that writes after this sees it (drop_if_stale).
    _dream_deletions[(user_id, dream_id)] = _dream_deletions.get((user_id, dream_id), 0) + 1
    await asyncio.to_thread(store.delete_dream, user_id, dream_id)


async def delete_user_audio(user_id: UUID) -> None:
    from redream_ai.audio import store

    _user_deletions[user_id] = _user_deletions.get(user_id, 0) + 1
    await asyncio.to_thread(store.delete_user, user_id)


async def discard_dream_audio(user_id: UUID, dream_id: int) -> None:
    """Delete a dream's folder when its rewritten text changed. A failure is
    logged, not raised: the key is cleared anyway and account deletion
    removes what is left."""
    await _discard(delete_dream_audio(user_id, dream_id))


async def _discard(deletion: Coroutine[Any, Any, None]) -> None:
    try:
        await deletion
    except (OSError, ValueError) as exc:
        log.error("rehearsal audio could not be deleted (%s)", type(exc).__name__)
