"""OpenAICompatibleLlmClient: the crisis classifier's client on LLM_BASE_URL.

Fully offline: an httpx.MockTransport stands in for the endpoint.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import openai
import pytest

from redream_ai.llm.base import LlmUnavailableError
from redream_ai.llm.openai_compatible import OpenAICompatibleLlmClient

_BASE_URL = "https://llm.example.test/v1"


def _client(
    handler, *, max_retries: int = 1
) -> tuple[OpenAICompatibleLlmClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def _logging_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(_logging_handler))
    llm = OpenAICompatibleLlmClient(
        _BASE_URL, "sk-test", timeout=7.0, max_retries=max_retries, http_client=http
    )
    return llm, requests


def _completion(content: str | None, usage: dict[str, int] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
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
    }
    if usage is not None:
        body["usage"] = usage
    return body


_USAGE = {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}


async def test_chat_request_and_reply():
    llm, requests = _client(
        lambda _: httpx.Response(200, json=_completion('{"risk":"none"}', _USAGE))
    )
    content, usage = await llm.chat(
        model="m-small",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    assert content == '{"risk":"none"}'
    assert usage == {"input_tokens": 12, "output_tokens": 5}
    (request,) = requests
    assert str(request.url) == f"{_BASE_URL}/chat/completions"
    assert request.headers["Authorization"] == "Bearer sk-test"
    body = json.loads(request.content)
    assert body == {
        "model": "m-small",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }


async def test_no_response_format_unless_asked():
    """LLM_JSON_MODE=false reaches the endpoint as no response_format at all."""
    llm, requests = _client(lambda _: httpx.Response(200, json=_completion("{}", _USAGE)))
    await llm.chat(model="m", messages=[{"role": "user", "content": "hi"}])
    body = json.loads(requests[0].content)
    assert "response_format" not in body
    assert "logprobs" not in body
    assert "top_logprobs" not in body


def test_endpoint_timeout_and_retries_reach_the_sdk():
    llm = OpenAICompatibleLlmClient(_BASE_URL, "sk-test", timeout=7.0)
    assert str(llm._client.base_url) == f"{_BASE_URL}/"
    assert llm._client.api_key == "sk-test"
    assert llm._client.timeout == 7.0
    assert llm._client.max_retries == 1


@pytest.mark.parametrize(("base_url", "api_key"), [("", "sk-test"), (_BASE_URL, "")])
def test_missing_endpoint_or_key_is_refused(base_url, api_key):
    with pytest.raises(ValueError, match="LLM_BASE_URL and LLM_API_KEY"):
        OpenAICompatibleLlmClient(base_url, api_key, timeout=7.0)


async def test_none_content_and_missing_usage_are_tolerated():
    llm, _ = _client(lambda _: httpx.Response(200, json=_completion(None)))
    content, usage = await llm.chat(model="m", messages=[])
    assert content == ""
    assert usage == {"input_tokens": 0, "output_tokens": 0}


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_rate_limit_and_server_errors_retry_once_then_are_unavailable(status):
    llm, requests = _client(lambda _: httpx.Response(status, headers={"retry-after-ms": "1"}))
    with pytest.raises(LlmUnavailableError):
        await llm.chat(model="m", messages=[{"role": "user", "content": "hi"}])
    assert len(requests) == 2


@pytest.mark.parametrize(
    ("error", "sdk_error"),
    [
        (httpx.ConnectError("refused"), openai.APIConnectionError),
        (httpx.ReadTimeout("slow"), openai.APITimeoutError),
    ],
)
async def test_connection_errors_and_timeouts_are_unavailable(error, sdk_error):
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    llm, requests = _client(handler, max_retries=0)
    with pytest.raises(LlmUnavailableError) as exc_info:
        await llm.chat(model="m", messages=[{"role": "user", "content": "hi"}])
    assert isinstance(exc_info.value.__cause__, sdk_error)
    assert len(requests) == 1


@pytest.mark.parametrize("status", [400, 401, 404])
async def test_client_errors_are_not_mapped(status):
    """A wrong model or key is a configuration bug, not an outage."""
    llm, requests = _client(lambda _: httpx.Response(status, json={"error": {"message": "no"}}))
    with pytest.raises(openai.APIStatusError) as exc_info:
        await llm.chat(model="m", messages=[{"role": "user", "content": "hi"}])
    assert not isinstance(exc_info.value, LlmUnavailableError)
    assert len(requests) == 1


def _sse(frames: list[dict[str, Any]]) -> httpx.Response:
    head = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m"}
    body = "".join(f"data: {json.dumps({**head, **f})}\n\n" for f in frames) + "data: [DONE]\n\n"
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


async def test_stream_yields_text_then_usage():
    frames = [
        {"choices": [{"index": 0, "delta": {"content": "Hel"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"content": "lo"}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}},
    ]
    llm, requests = _client(lambda _: _sse(frames))
    chunks = [c async for c in llm.chat_stream(model="m", messages=[])]
    assert "".join(c["text"] for c in chunks) == "Hello"
    assert chunks[1]["finish_reason"] == "stop"
    assert (chunks[-1]["input_tokens"], chunks[-1]["output_tokens"]) == (3, 2)
    assert json.loads(requests[0].content)["stream"] is True


async def test_stream_tolerates_empty_choices_and_no_usage():
    frames = [
        {"choices": []},
        {"choices": [{"index": 0, "delta": {"content": None}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"content": "Hi"}, "finish_reason": "stop"}]},
    ]
    llm, _ = _client(lambda _: _sse(frames))
    chunks = [c async for c in llm.chat_stream(model="m", messages=[])]
    assert chunks == [{"text": ""}, {"text": ""}, {"text": "Hi", "finish_reason": "stop"}]
