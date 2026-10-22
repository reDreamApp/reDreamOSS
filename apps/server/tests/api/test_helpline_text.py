"""HELPLINE_TEXT_EN / HELPLINE_TEXT_DE: the message of a high-risk safety event.

Pins the neutral defaults (unset and blank), the operator's own text picked by
the chat's language with English as the fallback, on both the plain and the
streaming message route, and the startup check on its length.
"""

from __future__ import annotations

import json
import re

import pytest
from pydantic import ValidationError

from redream_api.core.config import (
    HELPLINE_TEXT_DE_DEFAULT,
    HELPLINE_TEXT_EN_DEFAULT,
    HELPLINE_TEXT_MAX_LEN,
    Settings,
    settings,
)

from .conftest import FakeAiClient, start_chat

_HIGH_RISK = {
    "risk": "high",
    "categories": ["suicidal_ideation"],
    "confidence": 0.92,
    "prompt_version": "safety-v0.1.0",
}


async def _helpline_message(client, fake_ai: FakeAiClient, language: str) -> str:
    chat_id = await start_chat(client, language)
    fake_ai.queue_safety(_HIGH_RISK)
    r = await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "..."})
    assert r.status_code == 200, r.text
    return str(r.json()["safety_event"]["message"])


# --- settings ------------------------------------------------------------------


def test_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("HELPLINE_TEXT_EN", raising=False)
    monkeypatch.delenv("HELPLINE_TEXT_DE", raising=False)
    s = Settings(_env_file=None)
    assert s.helpline_text_en == HELPLINE_TEXT_EN_DEFAULT
    assert s.helpline_text_de == HELPLINE_TEXT_DE_DEFAULT


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_blank_means_default(monkeypatch, blank):
    # HELPLINE_TEXT_DE= left in .env gets the default, not an empty overlay.
    monkeypatch.setenv("HELPLINE_TEXT_EN", blank)
    monkeypatch.setenv("HELPLINE_TEXT_DE", blank)
    s = Settings(_env_file=None)
    assert s.helpline_text_en == HELPLINE_TEXT_EN_DEFAULT
    assert s.helpline_text_de == HELPLINE_TEXT_DE_DEFAULT


def test_custom_text_is_stripped(monkeypatch):
    monkeypatch.setenv("HELPLINE_TEXT_DE", "  Ruf 116 123 an.  ")
    assert Settings(_env_file=None).helpline_text_de == "Ruf 116 123 an."


@pytest.mark.parametrize("var", ["HELPLINE_TEXT_EN", "HELPLINE_TEXT_DE"])
def test_too_long_text_stops_startup(monkeypatch, var):
    monkeypatch.setenv(var, "x" * (HELPLINE_TEXT_MAX_LEN + 1))
    with pytest.raises(ValidationError, match=var):
        Settings(_env_file=None)


def test_text_at_the_limit_is_accepted(monkeypatch):
    monkeypatch.setenv("HELPLINE_TEXT_EN", "x" * HELPLINE_TEXT_MAX_LEN)
    assert len(Settings(_env_file=None).helpline_text_en) == HELPLINE_TEXT_MAX_LEN


def test_text_for_language_falls_back_to_english():
    s = Settings(_env_file=None, helpline_text_en="EN text", helpline_text_de="DE text")
    assert s.helpline_text_for("de") == "DE text"
    assert s.helpline_text_for("en") == "EN text"
    assert s.helpline_text_for("fr") == "EN text"


def test_defaults_name_no_country_numbers():
    for text in (HELPLINE_TEXT_EN_DEFAULT, HELPLINE_TEXT_DE_DEFAULT):
        assert "0800" not in text
        assert "telefonseelsorge" not in text.lower()
        assert not re.search(r"(?<!\d)112(?!\d)", text)
        assert "findahelpline.com" in text


# --- the high-risk path ----------------------------------------------------------


@pytest.mark.parametrize(
    ("language", "expected"),
    [("en", HELPLINE_TEXT_EN_DEFAULT), ("de", HELPLINE_TEXT_DE_DEFAULT)],
)
async def test_default_text_on_high_risk(client, fake_ai, language, expected):
    # The suite's conftest leaves HELPLINE_TEXT_* blank: the default applies.
    assert await _helpline_message(client, fake_ai, language) == expected


@pytest.mark.parametrize(
    ("language", "expected"), [("en", "Custom EN help."), ("de", "Eigene Hilfe DE.")]
)
async def test_custom_text_by_language(client, fake_ai, monkeypatch, language, expected):
    monkeypatch.setattr(settings, "helpline_text_en", "Custom EN help.")
    monkeypatch.setattr(settings, "helpline_text_de", "Eigene Hilfe DE.")
    assert await _helpline_message(client, fake_ai, language) == expected


async def test_custom_text_on_stream_route(client, fake_ai, monkeypatch):
    monkeypatch.setattr(settings, "helpline_text_de", "Eigene Hilfe DE.")
    chat_id = await start_chat(client, "de")
    fake_ai.queue_safety(_HIGH_RISK)

    r = await client.post(f"/api/v1/chats/{chat_id}/message/stream", json={"content": "..."})

    assert r.status_code == 200
    events = [
        json.loads(part.strip().removeprefix("data:"))
        for part in r.text.split("\n\n")
        if part.strip() and part.strip() != "data: [DONE]"
    ]
    safety = next(e["safety_event"] for e in events if "safety_event" in e)
    assert safety["message"] == "Eigene Hilfe DE."
