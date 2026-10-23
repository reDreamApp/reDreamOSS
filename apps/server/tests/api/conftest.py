"""pytest fixtures for the backend.

Per-test SQLite file under /tmp, dependency-overrides for `get_db`,
`get_current_user`, and `get_ai_client`. No live AI. `auth_client` leaves
`get_current_user` real: requests carry bearer tokens from /auth/login.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

# Set env BEFORE importing redream_api so the global Settings constructs cleanly.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
# The developer's local .env may disable the crisis classifier (dev flag);
# tests always run with the production default (env var beats dotenv).
os.environ["SAFETY_CLASSIFIER_ENABLED"] = "true"
# The IRT core builds its agents from the LLM_* settings at first import
# (test_in_process_ai_client imports it). Same throwaway endpoint and keys as
# tests/ai's conftest, so `pytest tests/api` alone is pinned like the full run.
os.environ.setdefault("LLM_BASE_URL", "http://llm.invalid/v1")
os.environ.setdefault("LLM_API_KEY", "dummy")
os.environ.setdefault("LLM_MODEL", "m-large")
os.environ.setdefault("LLM_SMALL_MODEL", "m-small")
# Speech is off unless a test switches it on; a local .env may configure it.
os.environ.update(
    dict.fromkeys(
        (
            "STT_BASE_URL",
            "STT_API_KEY",
            "STT_MODEL",
            "TTS_BASE_URL",
            "TTS_API_KEY",
            "TTS_MODEL",
            "TTS_VOICE",
            "TTS_VOICE_EN",
            "TTS_VOICE_DE",
        ),
        "",
    )
)
os.environ["TTS_API_STYLE"] = "openai"
os.environ["SAFETY_CRITIC_ENABLED"] = "true"
os.environ["LLM_JSON_MODE"] = "true"
# A local .env may set an operator's helpline; tests use the neutral defaults.
os.environ["HELPLINE_TEXT_EN"] = ""
os.environ["HELPLINE_TEXT_DE"] = ""
os.environ["HELPLINE_PROMPT_EN"] = ""
os.environ["HELPLINE_PROMPT_DE"] = ""
# Registration as shipped (invite); a local .env may choose another mode.
os.environ["REGISTRATION_MODE"] = "invite"
os.environ["INVITE_CODE"] = "test-invite-code"
os.environ["DAILY_AI_REQUESTS_PER_USER"] = "100"

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from redream_api.core import limits
from redream_api.core.config import settings
from redream_api.core.dependencies import get_current_user, get_db
from redream_api.db import models as _models  # noqa: F401 — register tables
from redream_api.db.base import Base
from redream_api.db.models.user import User
from redream_api.db.models.user_consent import UserConsent
from redream_api.main import app
from redream_api.services.ai_client import get_ai_client


# A password_hash no password verifies against: fake users sign in through
# the get_current_user override, never with a password.
NO_PASSWORD = "!"


# ---------------------------------------------------------------------------
# FakeAiClient — substitutes for the real AI service in tests.
# ---------------------------------------------------------------------------


class FakeAiClient:
    """A queue-driven test double for the AI service.

    Use `queue_chat`, `queue_safety`, `queue_transcribe`, `queue_rehearsal` to
    pre-load responses. Calls are recorded on `self.calls` for assertions.

    Default behaviour (queue empty) for `safety_classify` is `risk=none`, and
    for `language_check` the chat's current language (no switch) — the common
    cases, which keep tests focused on the interesting paths.

    Speech is off (`stt_enabled` / `tts_enabled` False), as with no STT_MODEL /
    TTS_MODEL: the audio routes answer 503. The `voice_on` fixture switches
    both halves on.
    """

    def __init__(self) -> None:
        self._chat: list[dict[str, Any]] = []
        self._chat_stream: list[Any] = []
        self._safety: list[dict[str, Any]] = []
        self._language: list[str] = []
        self._transcribe: list[dict[str, Any]] = []
        self._rehearsal: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.stt_enabled = False
        self.tts_enabled = False

    def queue_chat(self, response: dict[str, Any]) -> None:
        self._chat.append(response)

    def queue_chat_stream(self, frames: list[str]) -> None:
        """Frames exactly as InProcessAiClient.chat_stream yields them: stripped
        ``data: <payload>`` strings, one per SSE event, no ``\\n\\n``."""
        self._chat_stream.append(frames)

    def queue_chat_stream_error(self, exc: Exception) -> None:
        self._chat_stream.append(exc)

    def queue_safety(self, response: dict[str, Any]) -> None:
        self._safety.append(response)

    def queue_language(self, language: str) -> None:
        """The language the next `language_check` returns (like the real
        client, a failure is already folded into "the current language")."""
        self._language.append(language)

    def queue_transcribe(self, response: dict[str, Any]) -> None:
        self._transcribe.append(response)

    def queue_rehearsal(self, response: dict[str, Any]) -> None:
        self._rehearsal.append(response)

    def queue_chat_error(self, exc: Exception) -> None:
        """Queue an exception to be raised by the next `chat` call."""
        self._chat.append({"__raise__": exc})

    def queue_safety_error(self, exc: Exception) -> None:
        """Queue an exception to be raised by the next `safety_classify` call."""
        self._safety.append({"__raise__": exc})

    def queue_transcribe_error(self, exc: Exception) -> None:
        """Queue an exception to be raised by the next `transcribe` call."""
        self._transcribe.append({"__raise__": exc})

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"method": "chat", "payload": payload})
        if not self._chat:
            raise RuntimeError("FakeAiClient: no chat response queued")
        response = self._chat.pop(0)
        if "__raise__" in response:
            raise response["__raise__"]
        return response

    async def chat_stream(self, payload: dict[str, Any]) -> AsyncIterator[str]:
        self.calls.append({"method": "chat_stream", "payload": payload})
        if not self._chat_stream:
            raise RuntimeError("FakeAiClient: no chat stream queued")
        item = self._chat_stream.pop(0)
        if isinstance(item, Exception):
            raise item
        for frame in item:
            yield frame

    async def safety_classify(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"method": "safety_classify", "payload": payload})
        if not self._safety:
            return {
                "risk": "none",
                "categories": [],
                "confidence": 0.0,
                "prompt_version": "fake-safety-v0",
            }
        response = self._safety.pop(0)
        if "__raise__" in response:
            raise response["__raise__"]
        return response

    async def language_check(self, message: str, language: str) -> str:
        self.calls.append(
            {"method": "language_check", "payload": {"message": message, "language": language}}
        )
        if not self._language:
            return language
        return self._language.pop(0)

    async def transcribe(self, *, file_name: str, audio_bytes: bytes) -> dict[str, Any]:
        self.calls.append(
            {"method": "transcribe", "file_name": file_name, "size": len(audio_bytes)}
        )
        if not self._transcribe:
            raise RuntimeError("FakeAiClient: no transcribe response queued")
        response = self._transcribe.pop(0)
        if "__raise__" in response:
            raise response["__raise__"]
        return response

    async def rehearsal_generate(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"method": "rehearsal_generate", "payload": payload})
        if not self._rehearsal:
            raise RuntimeError("FakeAiClient: no rehearsal response queued")
        return self._rehearsal.pop(0)


# ---------------------------------------------------------------------------
# DB fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_path() -> AsyncIterator[Path]:
    fd, path_str = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    path = Path(path_str)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


@pytest.fixture
async def db_engine(db_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys(dbapi_connection, _record) -> None:
        # ON DELETE CASCADE as on Postgres: SQLite enforces foreign keys only
        # when asked, per connection.
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_session_maker(db_engine):
    return async_sessionmaker(bind=db_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def db(db_session_maker) -> AsyncIterator[AsyncSession]:
    async with db_session_maker() as session:
        yield session


@pytest.fixture
async def fake_user(db_session_maker) -> User:
    """A user WITH current Art. 9 consent granted — the normal state for
    every feature test. Consent-gating tests use `fake_user_no_consent`."""
    async with db_session_maker() as session:
        user = User(id=uuid4(), email="test@example.com", password_hash=NO_PASSWORD)
        session.add(user)
        session.add(
            UserConsent(
                user_id=user.id,
                version=settings.consent_version,
                granted=True,
            )
        )
        await session.commit()
        await session.refresh(user)
    return user


@pytest.fixture(autouse=True)
def _fresh_limits() -> None:
    """Every test starts with no rate-limit or daily-cap counts (core/limits)."""
    limits.reset()


@pytest.fixture
def day(monkeypatch: pytest.MonkeyPatch):
    """The daily cap's clock, two hours before UTC midnight; movable."""

    class Day:
        now = datetime(2026, 10, 23, 22, 0, tzinfo=UTC)

    monkeypatch.setattr(limits, "_utcnow", lambda: Day.now)
    return Day


@pytest.fixture(autouse=True)
def audio_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """AUDIO_DIR for this test: a fresh folder under tmp_path, never the repo."""
    from redream_ai.config import settings as ai_settings

    path = tmp_path.resolve() / "audio"
    monkeypatch.setattr(ai_settings, "audio_dir", path)
    return path


@pytest.fixture
def fake_ai() -> FakeAiClient:
    return FakeAiClient()


@pytest.fixture
def voice_on(fake_ai: FakeAiClient) -> FakeAiClient:
    """Speech configured: transcription and rehearsal audio switched on."""
    fake_ai.stt_enabled = True
    fake_ai.tts_enabled = True
    return fake_ai


@pytest.fixture
async def client(db_session_maker, fake_user, fake_ai) -> AsyncIterator[AsyncClient]:
    """HTTP client with deps overridden to use the per-test DB + fake AI + fake user."""

    async def _db_dep() -> AsyncIterator[AsyncSession]:
        async with db_session_maker() as session:
            yield session

    async def _user_dep() -> User:
        return fake_user

    def _ai_dep() -> FakeAiClient:
        return fake_ai

    app.dependency_overrides[get_db] = _db_dep
    app.dependency_overrides[get_current_user] = _user_dep
    app.dependency_overrides[get_ai_client] = _ai_dep

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture
async def auth_client(db_session_maker, fake_ai) -> AsyncIterator[AsyncClient]:
    """HTTP client on the per-test DB + fake AI, with the REAL get_current_user:
    requests authenticate with bearer tokens."""

    async def _db_dep() -> AsyncIterator[AsyncSession]:
        async with db_session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = _db_dep
    app.dependency_overrides[get_ai_client] = lambda: fake_ai

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helpers usable by tests.
# ---------------------------------------------------------------------------


async def start_chat(client: AsyncClient, language: str = "en") -> int:
    r = await client.post("/api/v1/chats/start", json={"language": language})
    assert r.status_code == 201, r.text
    return int(r.json()["chat_id"])
