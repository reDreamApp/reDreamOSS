"""A NUL character in a request body is a 422, never a 500.

Postgres text cannot hold U+0000 (the SQLite of these tests can, which is
why the routes are checked for the 422 itself). Every JSON body is a
RequestModel, which refuses it.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute
from pydantic import BaseModel

from redream_api.api.v1.auth.schemas import LoginRequest
from redream_api.core.request_model import RequestModel
from redream_api.main import app

from .conftest import start_chat

NUL_TEXT = "a storm\x00 tore the roof off"


def test_every_json_body_refuses_nul_characters() -> None:
    models = {
        f"{sorted(route.methods)} {route.path}": param.field_info.annotation
        for route in app.routes
        if isinstance(route, APIRoute)
        for param in route.dependant.body_params
        if isinstance(param.field_info.annotation, type)
        and issubclass(param.field_info.annotation, BaseModel)
    }
    assert models, "no JSON bodies found: the check would be empty"
    unchecked = {
        path: body.__name__
        for path, body in models.items()
        if not issubclass(body, RequestModel) and body is not LoginRequest
    }
    assert unchecked == {}


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/v1/chats/{chat}/message", {"content": NUL_TEXT}),
        ("/api/v1/chats/{chat}/message/stream", {"content": NUL_TEXT}),
        ("/api/v1/dreams", {"title": "Storm", "original_dream": NUL_TEXT}),
        ("/api/v1/progress/emotion", {"emotion": "calm\x00"}),
        ("/api/v1/profile", {"gender": "\x00"}),
    ],
)
async def test_nul_in_a_body_is_refused(client, fake_ai, path, body) -> None:
    chat_id = await start_chat(client)
    r = await client.post(path.format(chat=chat_id), json=body)

    assert r.status_code == 422
    assert "NUL" in r.text
    assert not [c for c in fake_ai.calls if c["method"] in ("chat", "chat_stream")]
    assert (await client.get(f"/api/v1/chats/{chat_id}/messages")).json() == []


async def test_nul_in_a_dream_update_is_refused(client) -> None:
    created = await client.post(
        "/api/v1/dreams", json={"title": "Storm", "original_dream": "A storm."}
    )
    dream_id = created.json()["id"]

    r = await client.put(f"/api/v1/dreams/{dream_id}", json={"rewritten_dream": NUL_TEXT})

    assert r.status_code == 422
    assert (await client.get(f"/api/v1/dreams/{dream_id}")).json()["rewritten_dream"] is None


async def test_nul_in_a_sign_up_is_refused_without_echo(auth_client) -> None:
    r = await auth_client.post(
        "/api/v1/auth/register",
        json={"email": "a@example.org", "password": "pass\x00word-1", "invite_code": "x" * 8},
    )

    assert r.status_code == 422
    assert "pass" not in r.text
