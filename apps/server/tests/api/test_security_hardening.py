"""Regression tests for security boundaries (no network or live services)."""

from uuid import uuid4

import httpx
import pytest

from redream_api.core import request_limits
from redream_api.core.config import settings
from redream_api.services import storage_service


async def test_chunked_json_is_bounded_before_parsing(client, monkeypatch):
    monkeypatch.setattr(request_limits, "MAX_JSON_BODY_BYTES", 16)

    async def chunks():
        yield b'{"content":"'
        yield b"x" * 32
        yield b'"}'

    response = await client.post(
        "/api/v1/chats/1/message", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413


async def test_chunked_multipart_is_bounded_before_spooling(client, monkeypatch):
    monkeypatch.setattr(request_limits, "MAX_AUDIO_BODY_BYTES", 128)

    async def chunks():
        yield b'--test\r\nContent-Disposition: form-data; name="file"; filename="voice.mp3"\r\n\r\n'
        yield b"x" * 129
        yield b"\r\n--test--\r\n"

    response = await client.post(
        "/api/v1/audio/transcribe",
        content=chunks(),
        headers={"Content-Type": "multipart/form-data; boundary=test"},
    )
    assert response.status_code == 413


async def test_content_length_cannot_bypass_or_crash_limit(client):
    for value in ["-1", "not-a-number", "9" * 21]:
        r = await client.post(
            "/api/v1/audio/transcribe", content=b"x", headers={"Content-Length": value}
        )
        assert r.status_code == 400
    r = await client.post(
        "/api/v1/audio/transcribe", content=b"x", headers={"Content-Length": str(30 * 1024 * 1024)}
    )
    assert r.status_code == 413


async def test_audio_refresh_moves_legacy_path_and_signs_shortly(monkeypatch):
    user = uuid4()
    old = f"users/{user}/7/en-old.mp3"
    calls = []
    monkeypatch.setattr(settings, "supabase_url", "http://kong")
    monkeypatch.setattr(settings, "supabase_public_url", "https://storage.example")
    monkeypatch.setattr(settings, "supabase_service_role_key", "test-key")

    def storage(req):
        import json

        calls.append((req.url.path, json.loads(req.content)))
        assert req.url.host == "kong"
        if req.url.path.endswith("/move"):
            return httpx.Response(200, json={})
        return httpx.Response(
            200,
            json={
                "signedURL": (
                    f"/object/sign/rehearsal-audio/users/{user}/7/v2-en-old.mp3?token=short"
                )
            },
        )

    monkeypatch.setattr(storage_service, "_transport", httpx.MockTransport(storage))
    url = await storage_service.refresh_rehearsal_url(
        f"https://untrusted.invalid/storage/v1/object/sign/rehearsal-audio/{old}?token=year",
        user,
        7,
    )
    assert url.startswith("https://storage.example/")
    assert calls[0][1]["sourceKey"] == old
    assert calls[0][1]["destinationKey"] == old.replace("/en-", "/v2-en-")
    assert calls[1][1]["expiresIn"] == 300
    with pytest.raises(ValueError):
        await storage_service.refresh_rehearsal_url(
            f"https://storage.example/storage/v1/object/sign/rehearsal-audio/users/{uuid4()}/7/v2-en-a.mp3",
            user,
            7,
        )
