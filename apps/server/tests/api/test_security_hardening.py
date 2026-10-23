"""Regression tests for security boundaries (no network or live services)."""

from redream_api.core import request_limits


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
