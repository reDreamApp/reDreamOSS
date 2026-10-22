"""POST /chats/{id}/message/stream — SSE proxy over the AI service (ADR 0009).

Exercises the backend frame contract:
    user_message -> content* -> done -> [DONE]              (normal)
    user_message -> safety_event -> [DONE]                  (high risk)
    user_message -> content* -> error -> [DONE]             (mid-stream failure,
                                                             user msg rolled back)
Pre-stream failures (409 finished) arrive as plain HTTP errors.

The FakeAiClient's `queue_chat_stream` takes frames exactly as the real
HttpxAiClient yields them: stripped `data: <payload>` strings.
"""

from __future__ import annotations

import json

from .conftest import start_chat

# A final frame as the AI service emits it: the full ChatResponse JSON.
_FINAL = {
    "stage": "rewriting",
    "response": "You turn and face it.",
    "structured": None,
    "usage": {"input_tokens": 11, "output_tokens": 22},
    "prompt_version": "irt-ported-2026-07",
    "safety": None,
    "nightmare_summary": "Chased through a forest.",
}


def _content(text: str) -> str:
    return "data: " + json.dumps(
        {"content": text, "stages": ["rewriting"], "language": "en"}
    )


def _frames(raw: str) -> list:
    """Decode the SSE body into payloads ('[DONE]' stays a string)."""
    out = []
    for part in raw.split("\n\n"):
        part = part.strip()
        if not part:
            continue
        body = part.removeprefix("data:").strip()
        out.append(body if body == "[DONE]" else json.loads(body))
    return out


async def test_stream_happy_path_frames_and_persistence(client, fake_ai):
    chat_id = await start_chat(client)
    fake_ai.queue_chat_stream(
        [
            _content("You turn "),
            _content("and face it."),
            # internal AI frames — must NOT be forwarded to the app client:
            "data: " + json.dumps({"safety": {"is_safe": True}}),
            "data: " + json.dumps({"final": _FINAL}),
            "data: [DONE]",
        ]
    )

    r = await client.post(f"/api/v1/chats/{chat_id}/message/stream", json={"content": "hi"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    frames = _frames(r.text)

    assert "user_message" in frames[0]
    assert frames[0]["user_message"]["content"] == "hi"
    assert [f["content"] for f in frames[1:3]] == ["You turn ", "and face it."]
    done = frames[3]["done"]
    assert done["ai_message"]["content"] == "You turn and face it."
    assert done["stage"] == "rewriting"
    assert done["is_done"] is False
    assert frames[4] == "[DONE]"
    assert len(frames) == 5  # AI's internal safety/final/[DONE] were consumed

    # Persistence: both messages stored, stage advanced.
    msgs = (await client.get(f"/api/v1/chats/{chat_id}/messages")).json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    chats = (await client.get("/api/v1/chats")).json()
    assert chats[0]["current_stage"] == "rewriting"


async def test_stream_persists_and_echoes_nightmare_summary(client, fake_ai):
    chat_id = await start_chat(client)
    fake_ai.queue_chat_stream([_content("a"), "data: " + json.dumps({"final": _FINAL})])
    r = await client.post(f"/api/v1/chats/{chat_id}/message/stream", json={"content": "hi"})
    assert r.status_code == 200

    # First turn: the AI was called with no summary yet.
    first_call = next(c for c in fake_ai.calls if c["method"] == "chat_stream")
    assert first_call["payload"]["nightmare_summary"] is None

    # Second turn: the summary persisted from the final frame is echoed back.
    fake_ai.queue_chat_stream([_content("b"), "data: " + json.dumps({"final": _FINAL})])
    r = await client.post(f"/api/v1/chats/{chat_id}/message/stream", json={"content": "more"})
    assert r.status_code == 200
    second_call = [c for c in fake_ai.calls if c["method"] == "chat_stream"][1]
    assert second_call["payload"]["nightmare_summary"] == "Chased through a forest."


async def test_stream_high_risk_short_circuits(client, fake_ai):
    chat_id = await start_chat(client)
    fake_ai.queue_safety(
        {"risk": "high", "categories": ["self_harm"], "confidence": 0.93,
         "prompt_version": "safety-v0.3.0"}
    )

    r = await client.post(f"/api/v1/chats/{chat_id}/message/stream", json={"content": "..."})
    assert r.status_code == 200
    frames = _frames(r.text)

    assert "user_message" in frames[0]
    assert frames[1]["safety_event"]["risk"] == "high"
    assert frames[1]["safety_event"]["message"]  # localized helpline text
    assert frames[2] == "[DONE]"
    assert len(frames) == 3
    # No AI turn ran, and the stage did not advance (ADR 0005).
    assert all(c["method"] != "chat_stream" for c in fake_ai.calls)
    chats = (await client.get("/api/v1/chats")).json()
    assert chats[0]["current_stage"] == "recording"


async def test_stream_error_frame_rolls_back_user_message(client, fake_ai):
    chat_id = await start_chat(client)
    fake_ai.queue_chat_stream(
        [_content("part"), "data: " + json.dumps({"error": "upstream exploded"})]
    )

    r = await client.post(f"/api/v1/chats/{chat_id}/message/stream", json={"content": "hi"})
    frames = _frames(r.text)

    assert "error" in frames[-2] and frames[-1] == "[DONE]"
    assert all("done" not in f for f in frames if isinstance(f, dict))
    # Parity with the non-streaming 502: the user message must not survive to
    # be replayed alongside the client's retry.
    msgs = (await client.get(f"/api/v1/chats/{chat_id}/messages")).json()
    assert msgs == []


async def test_stream_without_final_frame_is_an_error(client, fake_ai):
    chat_id = await start_chat(client)
    fake_ai.queue_chat_stream([_content("only content, then the stream dies")])

    r = await client.post(f"/api/v1/chats/{chat_id}/message/stream", json={"content": "hi"})
    frames = _frames(r.text)

    assert "error" in frames[-2] and frames[-1] == "[DONE]"
    msgs = (await client.get(f"/api/v1/chats/{chat_id}/messages")).json()
    assert msgs == []


async def test_stream_summary_stage_upserts_dream(client, fake_ai):
    chat_id = await start_chat(client)
    final = {
        **_FINAL,
        "stage": "summary",
        "structured": {
            "title": "Forest",
            "original_summary": "Chased.",
            "rewritten_summary": "I turn and face it.",
            "dream_summary": "Chased.",
            "intensity": 7,
        },
    }
    fake_ai.queue_chat_stream([_content("Title: Forest"), "data: " + json.dumps({"final": final})])

    r = await client.post(f"/api/v1/chats/{chat_id}/message/stream", json={"content": "yes"})
    assert r.status_code == 200

    dreams = (await client.get("/api/v1/dreams")).json()
    assert len(dreams) == 1
    assert dreams[0]["title"] == "Forest"
    assert dreams[0]["intensity"] == 7


async def test_stream_finished_chat_409_before_streaming(client, fake_ai):
    chat_id = await start_chat(client)
    fake_ai.queue_chat_stream(
        ["data: " + json.dumps({"final": {**_FINAL, "stage": "final"}})]
    )
    r = await client.post(f"/api/v1/chats/{chat_id}/message/stream", json={"content": "bye"})
    assert _frames(r.text)[-2]["done"]["is_done"] is True

    r = await client.post(f"/api/v1/chats/{chat_id}/message/stream", json={"content": "more"})
    assert r.status_code == 409  # plain JSON error, not SSE


# ---------------------------------------------------------------------------
# Failure semantics (from the 2026-07-02 adversarial review): disconnects are
# BaseExceptions the old `except Exception` never saw, post-finalize failures
# must keep committed rows, and a poisoned session must not crash the handler.
# ---------------------------------------------------------------------------


async def _make_setup(db, fake_user, fake_ai, content="hi"):
    """Build a chat + run the shared pre-turn gates, like the route does."""
    from redream_api.api.v1.chats import routes as chats_routes
    from redream_api.api.v1.chats.schemas import ChatMessageRequest
    from redream_api.db.repositories.chat_repo import ChatRepo

    chat = await ChatRepo(db).create(user_id=fake_user.id)
    chat_id = chat.id  # snapshot: the generator's rollback expires ORM attrs
    setup = await chats_routes._pre_turn(
        chat_id, ChatMessageRequest(content=content), fake_user, db, fake_ai
    )
    return chats_routes, chat_id, setup


async def test_disconnect_pre_finalize_rolls_back_user_message(db, fake_user, fake_ai):
    """Client vanishes mid-content (chat switch / app kill): aclose() raises
    GeneratorExit inside the generator — the orphaned user message must go."""
    from redream_api.db.repositories.chat_message_repo import ChatMessageRepo

    fake_ai.queue_chat_stream([_content("You turn "), _content("and face it.")])
    chats_routes, chat_id, setup = await _make_setup(db, fake_user, fake_ai)

    gen = chats_routes._stream_ai_turn(db, fake_ai, fake_user, setup)
    assert "user_message" in await gen.__anext__()
    assert "content" in await gen.__anext__()
    await gen.aclose()  # simulates Starlette tearing the generator down

    assert await ChatMessageRepo(db).list_for_chat(chat_id) == []


async def test_disconnect_after_done_keeps_the_turn(db, fake_user, fake_ai):
    fake_ai.queue_chat_stream(
        [_content("a"), "data: " + json.dumps({"final": _FINAL})]
    )
    chats_routes, chat_id, setup = await _make_setup(db, fake_user, fake_ai)

    gen = chats_routes._stream_ai_turn(db, fake_ai, fake_user, setup)
    frames = [await gen.__anext__() for _ in range(3)]  # user_message, content, done
    assert "done" in frames[-1]
    await gen.aclose()

    from redream_api.db.repositories.chat_message_repo import ChatMessageRepo

    msgs = await ChatMessageRepo(db).list_for_chat(chat_id)
    assert [m.role for m in msgs] == ["user", "assistant"]


async def test_disconnect_on_safety_path_keeps_moderation_records(db, fake_user, fake_ai):
    """ADR 0005: the flagged message + safety_events row must survive a
    disconnect — the cancellation rollback must not touch the safety path."""
    from redream_api.db.repositories.chat_message_repo import ChatMessageRepo

    fake_ai.queue_safety(
        {"risk": "high", "categories": ["self_harm"], "confidence": 0.9,
         "prompt_version": "safety-v0.3.0"}
    )
    chats_routes, chat_id, setup = await _make_setup(db, fake_user, fake_ai)
    assert setup.safety_payload is not None

    gen = chats_routes._stream_ai_turn(db, fake_ai, fake_user, setup)
    assert "user_message" in await gen.__anext__()
    await gen.aclose()  # disconnect before the safety_event frame was read

    msgs = await ChatMessageRepo(db).list_for_chat(chat_id)
    assert len(msgs) == 1 and msgs[0].safety_flagged is True


async def test_post_finalize_failure_keeps_both_rows_and_emits_error(
    client, fake_ai, monkeypatch
):
    """A DB error AFTER the AI message committed (here: the Dream upsert from
    the structured summary poisons the session with a failed flush) must keep
    both rows — parity with the non-streaming 500 — and still emit the error
    frame + [DONE] instead of crashing the handler on the poisoned session."""
    from redream_api.api.v1.chats import routes as chats_routes
    from redream_api.db.models.chat_message import ChatMessage

    async def poison(*, db, **kwargs):
        db.add(ChatMessage(chat_id=None, role="assistant", content="x", stage=None))
        await db.commit()  # NOT NULL violation → failed flush → poisoned session

    monkeypatch.setattr(chats_routes, "_upsert_dream_from_structured", poison)

    chat_id = await start_chat(client)
    final = {**_FINAL, "stage": "summary", "structured": {"title": "Forest"}}
    fake_ai.queue_chat_stream([_content("a"), "data: " + json.dumps({"final": final})])
    r = await client.post(f"/api/v1/chats/{chat_id}/message/stream", json={"content": "hi"})
    frames = _frames(r.text)

    assert "error" in frames[-2], frames  # handler survived the poisoned session
    assert frames[-1] == "[DONE]"
    assert all("done" not in f for f in frames if isinstance(f, dict))
    # Both rows survive: deleting the user message would orphan the reply.
    msgs = (await client.get(f"/api/v1/chats/{chat_id}/messages")).json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
