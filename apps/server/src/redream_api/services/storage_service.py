"""Supabase Storage cleanup for rehearsal audio (GDPR purge + dream deletion).

redream_ai writes rehearsal MP3s to ``users/{user_id}/{dream_id}/<file>.mp3`` in
the rehearsal-audio bucket. This module deletes them through the Storage REST
API with the service-role key:

- ``POST   /storage/v1/object/list/{bucket}``  ``{"prefix": ...}`` lists one
  folder level (sub-folders come back with ``id: null``)
- ``DELETE /storage/v1/object/{bucket}``       ``{"prefixes": [paths]}``

Best-effort: failures are logged, never raised, so
the Postgres deletion (the load-bearing part) always completes.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import httpx

from ..core.config import settings

log = logging.getLogger(__name__)

_LIST_LIMIT = 1000
_TIMEOUT = 30.0

# Test seam: tests install an httpx.MockTransport here; None means real HTTP.
_transport: httpx.AsyncBaseTransport | None = None


async def delete_rehearsal_audio_for_user(user_id: UUID) -> int:
    """Delete every rehearsal audio object owned by `user_id`. Returns count."""
    return await _purge(f"users/{user_id}", recursive=True)


async def delete_rehearsal_audio_for_dream(user_id: UUID, dream_id: int) -> int:
    """Delete the rehearsal audio of one dream. Returns count."""
    return await _purge(f"users/{user_id}/{dream_id}", recursive=False)


async def _purge(prefix: str, *, recursive: bool) -> int:
    if not settings.supabase_url or not settings.supabase_service_role_key:
        log.warning("storage.delete.skipped reason=supabase_not_configured prefix=%s", prefix)
        return 0
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, transport=_transport) as client:
            paths = await _list_files(client, prefix, recursive=recursive)
            if paths:
                r = await client.request(
                    "DELETE",
                    f"{_base()}/object/{settings.supabase_storage_bucket}",
                    headers=_headers(),
                    json={"prefixes": paths},
                )
                r.raise_for_status()
    except httpx.HTTPError as exc:
        log.error("storage.delete.failed prefix=%s error=%s", prefix, exc)
        return 0
    log.info("storage.delete.ok prefix=%s count=%d", prefix, len(paths))
    return len(paths)


async def _list_files(client: httpx.AsyncClient, prefix: str, *, recursive: bool) -> list[str]:
    files: list[str] = []
    offset = 0
    while True:
        r = await client.post(
            f"{_base()}/object/list/{settings.supabase_storage_bucket}",
            headers=_headers(),
            json={
                "prefix": prefix,
                "limit": _LIST_LIMIT,
                "offset": offset,
                "sortBy": {"column": "name", "order": "asc"},
            },
        )
        r.raise_for_status()
        entries: list[dict[str, Any]] = r.json()
        for entry in entries:
            path = f"{prefix}/{entry['name']}"
            if entry.get("id") is not None:
                files.append(path)
            elif recursive:
                files += await _list_files(client, path, recursive=True)
        if len(entries) < _LIST_LIMIT:
            break
        offset += _LIST_LIMIT
    return files


def _base() -> str:
    return f"{settings.supabase_url.rstrip('/')}/storage/v1"


def _headers() -> dict[str, str]:
    key = settings.supabase_service_role_key
    return {"Authorization": f"Bearer {key}", "apikey": key}


async def refresh_rehearsal_url(url: str, user_id: UUID, dream_id: int) -> str:
    """Validate the owner path, revoke legacy links by moving, then sign briefly.

    Never contact the stored URL's host. Moves are recoverable after a crash:
    a missing source is accepted only if the destination actually exists.
    """
    from urllib.parse import quote, unquote, urlsplit

    marker = f"/storage/v1/object/sign/{settings.supabase_storage_bucket}/"
    decoded = unquote(urlsplit(url).path)
    if not decoded.startswith(marker):
        raise ValueError("invalid rehearsal object URL")
    path = decoded[len(marker) :]
    prefix = f"users/{user_id}/{dream_id}/"
    name = path.removeprefix(prefix)
    if (
        not path.startswith(prefix)
        or not name.endswith(".mp3")
        or "/" in name
        or "\\" in name
        or ".." in name
    ):
        raise ValueError("invalid rehearsal owner path")
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise ValueError("rehearsal storage not configured")
    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=_transport) as client:
        if not name.startswith("v2-"):
            destination = prefix + "v2-" + name
            await _move_legacy(client, path, destination)
            path = destination
        signed = await client.post(
            f"{_base()}/object/sign/{settings.supabase_storage_bucket}/{quote(path, safe='/')}",
            headers=_headers(),
            json={"expiresIn": settings.rehearsal_url_ttl_seconds},
        )
        signed.raise_for_status()
        suffix = signed.json().get("signedURL") or signed.json().get("signedUrl")
        if not isinstance(suffix, str) or not suffix.startswith(
            f"/object/sign/{settings.supabase_storage_bucket}/{path}?"
        ):
            raise ValueError("storage returned invalid signed URL")
        public = (
            settings.supabase_public_url
            or settings.supabase_jwt_iss.removesuffix("/auth/v1")
            or settings.supabase_url
        ).rstrip("/")
        return f"{public}/storage/v1{suffix}"


async def _move_legacy(client: httpx.AsyncClient, source: str, destination: str) -> None:
    move = await client.post(
        f"{_base()}/object/move",
        headers=_headers(),
        json={
            "bucketId": settings.supabase_storage_bucket,
            "sourceKey": source,
            "destinationKey": destination,
        },
    )
    if move.status_code < 300:
        return
    folder = source.rpartition("/")[0]
    files = await _list_files(client, folder, recursive=False)
    if destination not in files:
        move.raise_for_status()
    if source in files:
        # New code may have synthesized the same content before migration.
        # Revocation still requires removing the legacy path.
        removed = await client.request(
            "DELETE",
            f"{_base()}/object/{settings.supabase_storage_bucket}",
            headers=_headers(),
            json={"prefixes": [source]},
        )
        removed.raise_for_status()
