"""Signed audio links (services/audio_links) and GET /audio/files/{key}.

The file route has no auth dependency: tests call it through an app client
without the conftest's user override, as an audio player would.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from redream_ai.audio import store
from redream_api.api.v1.audio import routes as audio_routes
from redream_api.core.config import Settings as ApiSettings
from redream_api.core.logging import _RedactAudioLinks
from redream_api.main import app
from redream_api.services import audio_links

_USER = UUID("0f8b6c1e-2a3d-4e5f-8a9b-0c1d2e3f4a5b")
_KEY = f"users/{_USER}/7/en-{'a' * 24}.mp3"
_DATA = bytes(range(256)) * 4
_NOW = 1_800_000_000
_FORBIDDEN = {"detail": "invalid or expired link"}


def _parts(link: str) -> tuple[str, str, str]:
    url = urlsplit(link)
    query = parse_qs(url.query)
    return url.path.removeprefix(audio_links.FILES_PATH), query["exp"][0], query["sig"][0]


@pytest.fixture
async def player() -> AsyncIterator[AsyncClient]:
    """A client with no auth header and no dependency overrides."""
    app.dependency_overrides.clear()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# --- links ------------------------------------------------------------------------


def test_link_round_trip():
    link = audio_links.sign(_KEY, now=_NOW)

    assert link.startswith(f"/api/v1/audio/files/{_KEY}?exp={_NOW + 300}&sig=")
    key, exp, sig = _parts(link)
    assert key == _KEY
    assert audio_links.verify(key, exp, sig, now=_NOW)
    assert audio_links.verify(key, exp, sig, now=_NOW + 300)  # until exp, inclusive


def test_tampered_links_fail():
    key, exp, sig = _parts(audio_links.sign(_KEY, now=_NOW))
    other_key = f"users/{_USER}/8/en-{'a' * 24}.mp3"
    flipped = ("0" if sig[0] != "0" else "1") + sig[1:]

    assert not audio_links.verify(other_key, exp, sig, now=_NOW)
    assert not audio_links.verify(key, str(int(exp) + 1), sig, now=_NOW)
    assert not audio_links.verify(key, exp, flipped, now=_NOW)
    assert not audio_links.verify(key, exp, sig.upper(), now=_NOW)
    assert not audio_links.verify(key, exp, sig[:-1], now=_NOW)
    assert not audio_links.verify(key, exp, "", now=_NOW)
    for bad_exp in ("", f"+{exp}", f" {exp}", f"{exp}.0", "-1", "x", "٣" * 10, f"0{exp}"):
        assert not audio_links.verify(key, bad_exp, sig, now=_NOW)


def test_a_link_from_another_process_fails(monkeypatch):
    key, exp, sig = _parts(audio_links.sign(_KEY, now=_NOW))
    monkeypatch.setattr(audio_links, "_secret", b"\x00" * 32)
    assert not audio_links.verify(key, exp, sig, now=_NOW)


def test_an_expired_link_fails():
    key, exp, sig = _parts(audio_links.sign(_KEY, now=_NOW))
    assert not audio_links.verify(key, exp, sig, now=_NOW + 301)


def test_a_link_too_far_in_the_future_fails():
    """A link signed for a later clock than the server's own (TTL + 60 s ahead)."""
    key, exp, sig = _parts(audio_links.sign(_KEY, now=_NOW + 61))
    assert audio_links.verify(key, exp, sig, now=_NOW + 1)
    assert not audio_links.verify(key, exp, sig, now=_NOW)


def test_only_valid_keys_are_signed():
    for key in ("users/../etc/passwd", f"users/{_USER}/7/v2-en-{'a' * 24}.mp3", ""):
        with pytest.raises(ValueError):
            audio_links.sign(key)


def test_link_for_skips_what_is_not_a_key():
    assert audio_links.link_for(None) is None
    assert audio_links.link_for("https://storage.example/old.mp3") is None
    link = audio_links.link_for(_KEY)
    assert link is not None and link.startswith(f"/api/v1/audio/files/{_KEY}?exp=")


def test_the_storage_settings_are_gone():
    for name in ("supabase_storage_bucket", "supabase_public_url"):
        assert name not in ApiSettings.model_fields
    assert ApiSettings.model_fields["rehearsal_url_ttl_seconds"].default == 300


def test_the_access_log_shows_neither_key_nor_signature():
    link = audio_links.sign(_KEY)
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", link, "1.1", 200),
        None,
    )

    assert _RedactAudioLinks().filter(record)

    message = record.getMessage()
    assert "/api/v1/audio/files/" in message
    assert str(_USER) not in message and "sig=" not in message
    assert logging.getLogger("uvicorn.access").filters  # installed by configure_logging


def test_the_access_log_redacts_links_behind_a_root_path():
    """Behind a proxy prefix (uvicorn --root-path) the logged path starts with it."""
    link = "/redream" + audio_links.sign(_KEY)
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", link, "1.1", 200),
        None,
    )

    assert _RedactAudioLinks().filter(record)

    message = record.getMessage()
    assert "/redream/api/v1/audio/files/…" in message
    assert str(_USER) not in message and "sig=" not in message


# --- the file route ---------------------------------------------------------------


async def test_a_signed_link_serves_the_file_without_auth(player):
    store.write(_KEY, _DATA)

    r = await player.get(audio_links.sign(_KEY))

    assert r.status_code == 200
    assert r.content == _DATA
    assert r.headers["content-type"] == "audio/mpeg"
    assert r.headers["cache-control"] == "private, no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["accept-ranges"] == "bytes"


async def test_a_range_request_gets_206_and_those_bytes(player):
    store.write(_KEY, _DATA)

    r = await player.get(audio_links.sign(_KEY), headers={"Range": "bytes=100-199"})

    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 100-199/{len(_DATA)}"
    assert r.content == _DATA[100:200]
    assert r.headers["content-type"] == "audio/mpeg"
    assert r.headers["cache-control"] == "private, no-store"


async def test_head_on_a_signed_link_answers_headers_only(player):
    store.write(_KEY, _DATA)

    r = await player.head(audio_links.sign(_KEY))

    assert r.status_code == 200
    assert r.content == b""
    assert r.headers["content-length"] == str(len(_DATA))
    assert r.headers["content-type"] == "audio/mpeg"
    assert r.headers["cache-control"] == "private, no-store"


async def test_bad_or_expired_signatures_are_403(player):
    store.write(_KEY, _DATA)
    _, exp, sig = _parts(audio_links.sign(_KEY))
    past = int(time.time()) - 10
    _, old_exp, old_sig = _parts(audio_links.sign(_KEY, now=past - 300))
    base = f"{audio_links.FILES_PATH}{_KEY}"

    for url in (
        f"{base}?exp={exp}&sig={'0' * 64}",
        f"{base}?exp={int(exp) + 1}&sig={sig}",
        f"{base}?exp={old_exp}&sig={old_sig}",
        f"{base}?exp={exp}",
        f"{base}?sig={sig}",
        base,
    ):
        r = await player.get(url)
        assert r.status_code == 403, url
        assert r.json() == _FORBIDDEN


@pytest.mark.parametrize(
    "key",
    [
        f"users/{str(_USER).upper()}/7/en-{'a' * 24}.mp3",
        f"users/{_USER}/7/v2-en-{'a' * 24}.mp3",
        f"users/{_USER}/0/en-{'a' * 24}.mp3",
        f"users/{_USER}/7/en-{'a' * 24}.MP3",
        f"users/{_USER}/7/%2e%2e/7/en-{'a' * 24}.mp3",
        f"users%5c{_USER}/7/en-{'a' * 24}.mp3",
        "etc/passwd",
    ],
)
async def test_an_invalid_key_is_403(player, audio_dir: Path, key):
    """Correctly signed (for the key the route sees, after URL decoding), so
    only the key check can refuse it."""
    exp = int(time.time()) + 300
    sig = audio_links._signature(unquote(key), exp)
    r = await player.get(f"{audio_links.FILES_PATH}{key}?exp={exp}&sig={sig}")
    assert r.status_code == 403
    assert r.json() == _FORBIDDEN


async def test_a_valid_link_to_a_missing_file_is_404(player):
    r = await player.get(audio_links.sign(_KEY))
    assert r.status_code == 404


async def test_a_symlinked_folder_is_403(player, audio_dir: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / f"en-{'a' * 24}.mp3").write_bytes(b"not in AUDIO_DIR")
    (audio_dir / "users" / str(_USER)).mkdir(parents=True)
    (audio_dir / "users" / str(_USER) / "7").symlink_to(outside, target_is_directory=True)

    r = await player.get(audio_links.sign(_KEY))

    assert r.status_code == 403
    assert r.json() == _FORBIDDEN


async def test_a_folder_linked_to_another_users_folder_is_403(player, audio_dir: Path):
    """The link stays inside AUDIO_DIR: a valid link for A's key must not
    serve B's file."""
    other = uuid4()
    store.write(store.key_for(other, 7, "en", "a" * 24), b"B's audio")
    (audio_dir / "users" / str(_USER)).mkdir(mode=0o700)
    (audio_dir / "users" / str(_USER) / "7").symlink_to(
        audio_dir / "users" / str(other) / "7", target_is_directory=True
    )

    r = await player.get(audio_links.sign(_KEY))

    assert r.status_code == 403
    assert r.json() == _FORBIDDEN


async def test_a_file_that_cannot_be_read_is_503_and_the_path_is_not_logged(
    player, monkeypatch, caplog
):
    store.write(_KEY, _DATA)

    def broken(path):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(audio_routes, "os", SimpleNamespace(stat=broken))
    with caplog.at_level(logging.ERROR):
        r = await player.get(audio_links.sign(_KEY))

    assert r.status_code == 503
    assert r.json() == {"detail": "audio could not be read, try again"}
    assert "PermissionError" in caplog.text
    assert str(_USER) not in caplog.text
