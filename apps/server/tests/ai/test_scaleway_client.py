"""ScalewayLlmClient: the crisis classifier's primary (ADR 0010).

Fully offline: an httpx.MockTransport stands in for api.scaleway.ai.
"""

from __future__ import annotations

import json

import httpx
import pytest

from redream_ai.llm.base import LlmUnavailableError
from redream_ai.llm.scaleway_client import ScalewayLlmClient


def _client(handler, project_id: str = "") -> tuple[ScalewayLlmClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def _logging_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(_logging_handler))
    return ScalewayLlmClient("scw-test", project_id, http_client=http), requests


def _completion(content: str) -> dict:
    return {
        "id": "x",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
    }


async def test_chat_request_and_reply():
    llm, requests = _client(
        lambda _: httpx.Response(200, json=_completion('{"risk":"none"}')), "proj-1"
    )
    content, usage = await llm.chat(
        model="mistral-small-3.2-24b-instruct-2506",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    assert content == '{"risk":"none"}'
    assert usage == {"input_tokens": 12, "output_tokens": 5}
    (request,) = requests
    # A project-restricted key only works with the project in the URL.
    assert str(request.url) == "https://api.scaleway.ai/proj-1/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer scw-test"
    body = json.loads(request.content)
    assert body["model"] == "mistral-small-3.2-24b-instruct-2506"
    assert body["temperature"] == 0.0
    assert body["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize("status", [429, 503])
async def test_transient_errors_retry_once_then_raise(status):
    llm, requests = _client(lambda _: httpx.Response(status, headers={"retry-after-ms": "1"}))
    with pytest.raises(LlmUnavailableError):
        await llm.chat(model="m", messages=[{"role": "user", "content": "hi"}])
    assert len(requests) == 2


async def test_stream_yields_text_then_usage():
    frames = [
        {"choices": [{"index": 0, "delta": {"content": "Hel"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"content": "lo"}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}},
    ]
    head = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m"}
    sse = "".join(f"data: {json.dumps({**head, **f})}\n\n" for f in frames) + "data: [DONE]\n\n"
    llm, _ = _client(
        lambda _: httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
    )
    chunks = [c async for c in llm.chat_stream(model="m", messages=[])]
    assert "".join(c["text"] for c in chunks) == "Hello"
    assert chunks[1]["finish_reason"] == "stop"
    assert (chunks[-1]["input_tokens"], chunks[-1]["output_tokens"]) == (3, 2)
