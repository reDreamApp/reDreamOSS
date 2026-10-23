"""reDreamAI setting names are reported by name at startup, never by value."""

from __future__ import annotations

import logging
import os

import pytest

from redream_api.core.config import legacy_setting_names
from redream_api.main import app, lifespan


def test_names_of_set_legacy_settings_only() -> None:
    environ = {
        "MISTRAL_API_KEY": "secret-1",
        "scaleway_api_key": "secret-2",
        "VOXTRAL_TTS_MODEL": "",
        "SUPABASE_URL": "https://x.supabase.co",
        "LLM_API_KEY": "secret-3",
        "MY_MISTRAL_NOTE": "x",
    }
    assert legacy_setting_names(environ) == ["MISTRAL_API_KEY", "SUPABASE_URL", "scaleway_api_key"]
    assert legacy_setting_names({"LLM_MODEL": "m"}) == []


async def test_startup_warns_without_values(monkeypatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "sk-should-never-be-logged")
    monkeypatch.setenv("VOXTRAL_VOICE_ID_EN", "voice-should-never-be-logged")
    with caplog.at_level(logging.WARNING, logger="redream_api.main"):
        async with lifespan(app):
            pass
    text = caplog.text
    assert "ignored reDreamAI settings: MISTRAL_API_KEY, VOXTRAL_VOICE_ID_EN" in text
    assert "should-never-be-logged" not in text


async def test_no_warning_without_legacy_settings(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    for name in legacy_setting_names(os.environ):
        monkeypatch.delenv(name)
    with caplog.at_level(logging.WARNING, logger="redream_api.main"):
        async with lifespan(app):
            pass
    assert "reDreamAI" not in caplog.text
