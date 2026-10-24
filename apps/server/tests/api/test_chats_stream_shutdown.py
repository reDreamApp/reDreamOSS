"""A server stop during a chat reply leaves no unanswered user message.

The real app under a real uvicorn: on a stop (SIGTERM from `docker compose`),
uvicorn waits `--timeout-graceful-shutdown` seconds for open responses, then
cancels them. The streaming route must then roll back the user message, as on
a client disconnect, so a resend after the restart does not duplicate it.
"""

from __future__ import annotations

import asyncio
import json
import socket

import httpx
import uvicorn

from redream_api.core.dependencies import get_current_user, get_db
from redream_api.db.repositories.chat_message_repo import ChatMessageRepo
from redream_api.main import app
from redream_api.services.ai_client import get_ai_client

from .conftest import start_chat


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_shutdown_mid_reply_rolls_back_the_user_message(
    client, db_session_maker, fake_user, fake_ai, monkeypatch
) -> None:
    chat_id = await start_chat(client)  # also installs the dependency overrides

    async def chat_stream(payload):
        yield "data: " + json.dumps({"content": "You turn ", "stages": ["rewriting"]})
        await asyncio.Event().wait()  # the model is still writing

    monkeypatch.setattr(fake_ai, "chat_stream", chat_stream)
    assert app.dependency_overrides.keys() >= {get_db, get_current_user, get_ai_client}

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, timeout_graceful_shutdown=1, lifespan="on", ws="none"
        )
    )
    serving = asyncio.create_task(server.serve())

    async def started() -> None:
        while not server.started and not serving.done():  # noqa: ASYNC110 — only a flag
            await asyncio.sleep(0.02)

    await asyncio.wait_for(started(), 5)
    assert server.started, "the server did not start"

    content_seen = asyncio.Event()

    async def read_stream() -> None:
        async with httpx.AsyncClient(timeout=10) as http:
            async with http.stream(
                "POST",
                f"http://127.0.0.1:{port}/api/v1/chats/{chat_id}/message/stream",
                json={"content": "a storm tore the roof off"},
            ) as response:
                async for line in response.aiter_lines():
                    if '"content"' in line:
                        content_seen.set()

    reading = asyncio.create_task(read_stream())
    await asyncio.wait_for(content_seen.wait(), 5)
    server.should_exit = True  # what SIGTERM does
    await asyncio.wait_for(serving, 10)
    reading.cancel()

    async with db_session_maker() as session:
        assert await ChatMessageRepo(session).list_for_chat(chat_id) == []
