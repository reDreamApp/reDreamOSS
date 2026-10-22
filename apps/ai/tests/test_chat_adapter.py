"""POST /chat adapter over the ported IRT core (redream_ai.api.chat).

The adapter rebuilds a ported Conversation from the stateless wire request, calls
the ported orchestrator, and maps the result back. These tests MOCK the ported
`process_chat_message` (and the structured-summary pass) so nothing hits an LLM.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import redream_ai.api.chat as chatmod
from redream_ai.core import irt_app
from redream_ai.core.models import ChatResponse as PortedResponse
from redream_ai.main import app
from redream_ai.models import SummaryStructured


def _ported(stage, response, conv_stages, usage=None, safety=None):
    return PortedResponse(
        session_id="s",
        stage=stage,
        response=response,
        stages=conv_stages,
        language="en",
        usage=usage or {"input": 1, "output": 1, "total": 2},
        safety=safety,
    )


@pytest.fixture
def client(monkeypatch):
    """TestClient with the ported agents stubbed non-None (so /chat doesn't 503)."""
    monkeypatch.setattr(irt_app, "routing_agent", object(), raising=False)
    monkeypatch.setattr(irt_app, "response_agent", object(), raising=False)
    with TestClient(app) as c:
        yield c


def test_recording_turn_maps_to_wire(client, monkeypatch):
    async def fake(chat_input, conversation, safety_enabled=True):
        conversation.stages.append("recording")
        return _ported("recording", "Tell me more about the dream.", conversation.stages,
                       {"input": 10, "output": 5, "total": 15})

    monkeypatch.setattr(irt_app, "process_chat_message", fake)
    r = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "nightmare"}], "language": "en"},
    )
    assert r.status_code == 200
    b = r.json()
    assert b["stage"] == "recording"
    assert b["response"] == "Tell me more about the dream."
    assert b["usage"] == {"input_tokens": 10, "output_tokens": 5}
    assert b["structured"] is None
    assert b["prompt_version"] == chatmod.PROMPT_VERSION


def test_summary_turn_triggers_structured_pass(client, monkeypatch):
    async def fake(chat_input, conversation, safety_enabled=True):
        conversation.stages.append("summary")
        conversation.nightmare_summary = "I was chased through a forest."
        return _ported("summary", "Title: Forest\n\n...", conversation.stages)

    async def fake_extract(prose, language):
        return SummaryStructured(title="Forest", rewritten_summary="R",
                                 original_summary="O", dream_summary="O", intensity=None)

    monkeypatch.setattr(irt_app, "process_chat_message", fake)
    monkeypatch.setattr(chatmod, "extract_summary_structured", fake_extract)
    r = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "yes"}], "language": "en"},
    )
    b = r.json()
    assert b["stage"] == "summary"
    assert b["structured"]["title"] == "Forest"
    assert b["structured"]["dream_summary"] == b["structured"]["original_summary"]
    # once-generated nightmare summary is echoed back for the backend to persist
    assert b["nightmare_summary"] == "I was chased through a forest."


def test_safety_passthrough(client, monkeypatch):
    async def fake(chat_input, conversation, safety_enabled=True):
        conversation.stages.append("rewriting")
        return _ported("rewriting", "Would you like a summary?", conversation.stages,
                       safety={"is_safe": True, "node_id": "BEHAVIORAL_MASTERY"})

    monkeypatch.setattr(irt_app, "process_chat_message", fake)
    r = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "I turn and face it"}], "language": "en"},
    )
    assert r.json()["safety"] == {"is_safe": True, "node_id": "BEHAVIORAL_MASTERY"}


def test_rebuild_conversation_excludes_new_message(client, monkeypatch):
    captured = {}

    async def fake(chat_input, conversation, safety_enabled=True):
        captured["new_message"] = chat_input.message
        captured["history"] = [m.content for m in conversation.messages]
        captured["stages"] = list(conversation.stages)
        conversation.stages.append("rewriting")
        return _ported("rewriting", "ok", conversation.stages)

    monkeypatch.setattr(irt_app, "process_chat_message", fake)
    client.post("/chat", json={"messages": [
        {"role": "user", "content": "dream", "stage": "recording"},
        {"role": "assistant", "content": "rewrite?", "stage": "recording"},
        {"role": "user", "content": "yes go"},
    ], "language": "en"})
    assert captured["new_message"] == "yes go"          # last msg is the new user turn
    assert captured["history"] == ["dream", "rewrite?"]  # everything before it
    assert captured["stages"] == ["recording"]           # one resolved stage per assistant msg


def test_last_message_must_be_user(client):
    r = client.post(
        "/chat",
        json={"messages": [{"role": "assistant", "content": "hi"}], "language": "en"},
    )
    assert r.status_code == 400


def test_empty_messages_rejected(client):
    r = client.post("/chat", json={"messages": [], "language": "en"})
    assert r.status_code == 400


def test_503_when_agents_unconfigured(monkeypatch):
    monkeypatch.setattr(irt_app, "routing_agent", None, raising=False)
    with TestClient(app) as c:
        r = c.post("/chat", json={"messages": [{"role": "user", "content": "x"}], "language": "en"})
    assert r.status_code == 503
