"""`chat_stream` — SSE adapter over the ported streaming orchestrator (ADR 0009).

Most tests MOCK the ported `process_chat_message_stream` (and the structured-summary
pass) with fake async generators so nothing hits an LLM; one drives the REAL generator
with mocked internals to pin its wire frames against the adapter's parser. Frames are
collected straight from the generator `chat_stream` returns and checked against the
contract documented in the `redream_ai.api.chat` module docstring:

    content* -> usage? -> safety? -> final -> [DONE]   (from the core; the adapter
        swallows `usage`, folding it into `final.usage`, and swallows the core's
        own [DONE] — consumers see: content* -> safety? -> final -> [DONE])
    content* -> error -> [DONE]                        (failure; no final frame)
"""

from __future__ import annotations

import contextlib
import json
import types

import pytest
from fastapi import HTTPException

import redream_ai.api.chat as chatmod
from redream_ai.core import irt_app
from redream_ai.core.agent import MODELS, Agent
from redream_ai.models import ChatRequest, SummaryStructured

REQ = {"messages": [{"role": "user", "content": "I turn and face it"}], "language": "en"}


@pytest.fixture
def agents(monkeypatch):
    """The ported agents stubbed non-None (so `chat_stream` doesn't 503)."""
    monkeypatch.setattr(irt_app, "routing_agent", object(), raising=False)
    monkeypatch.setattr(irt_app, "response_agent", object(), raising=False)


async def _collect_frames(payload):
    """Run `chat_stream` and return the SSE frames (without the '\\n\\n' separators)."""
    items = [item async for item in chatmod.chat_stream(ChatRequest.model_validate(payload))]
    # One complete `data: ...` event per item, as the backend's client expects.
    assert all(i.startswith("data: ") and i.endswith("\n\n") for i in items), items
    text = "".join(items)
    assert text.endswith("\n\n")
    return [f for f in text.split("\n\n") if f]


def _payload(frame):
    return json.loads(frame.removeprefix("data: "))


async def test_passthrough_order_and_final_frame(agents, monkeypatch):
    """Content + safety pass through verbatim; usage is swallowed into final + [DONE]."""
    chunk1 = 'data: {"content": "You turn ", "stages": ["rewriting"], "language": "en"}'
    chunk2 = 'data: {"content": "and face it.", "stages": ["rewriting"], "language": "en"}'
    usage = 'data: {"usage": {"input": 17, "output": 43, "total": 60}}'
    safety = 'data: {"safety": {"is_safe": true, "node_id": "BEHAVIORAL_MASTERY"}}'

    async def fake(chat_input, conversation, safety_enabled=True):
        conversation.stages.append("rewriting")
        yield chunk1 + "\n\n"
        yield chunk2 + "\n\n"
        yield usage + "\n\n"  # the core's internal usage frame — swallowed into final
        yield safety + "\n\n"
        yield "data: [DONE]\n\n"  # the ported terminal — must be swallowed by the route

    monkeypatch.setattr(irt_app, "process_chat_message_stream", fake)
    frames = await _collect_frames(REQ)

    assert frames[:3] == [chunk1, chunk2, safety]  # verbatim pass-through; no usage frame
    assert _payload(frames[3])["final"] == {
        "stage": "rewriting",
        "response": "You turn and face it.",  # accumulated content chunks
        "structured": None,
        "usage": {"input_tokens": 17, "output_tokens": 43},  # folded from the usage frame
        "prompt_version": chatmod.PROMPT_VERSION,
        "safety": {"is_safe": True, "node_id": "BEHAVIORAL_MASTERY"},
        "nightmare_summary": None,
    }
    assert frames[4] == "data: [DONE]"
    assert len(frames) == 5  # usage + ported [DONE] swallowed; ours terminal


async def test_summary_stage_triggers_structured_extraction(agents, monkeypatch):
    async def fake(chat_input, conversation, safety_enabled=True):
        conversation.stages.append("summary")
        conversation.nightmare_summary = "I was chased through a forest."
        yield 'data: {"content": "Title: Forest", "stages": ["summary"], "language": "en"}\n\n'
        chunk2 = {"content": "\n\nRewritten Dream: ...", "stages": ["summary"], "language": "en"}
        yield f"data: {json.dumps(chunk2)}\n\n"
        yield "data: [DONE]\n\n"

    captured = {}

    async def fake_extract(prose, language):
        captured["prose"] = prose
        captured["language"] = language
        return SummaryStructured(title="Forest", rewritten_summary="R",
                                 original_summary="O", dream_summary="O", intensity=None)

    monkeypatch.setattr(irt_app, "process_chat_message_stream", fake)
    monkeypatch.setattr(chatmod, "extract_summary_structured", fake_extract)
    frames = await _collect_frames(REQ)

    final = _payload(frames[2])["final"]
    assert captured["prose"] == "Title: Forest\n\nRewritten Dream: ..."  # full accumulated text
    assert captured["language"] == "en"
    assert final["stage"] == "summary"
    assert final["structured"]["title"] == "Forest"
    assert final["structured"]["dream_summary"] == final["structured"]["original_summary"]
    # once-generated nightmare summary is echoed back for the backend to persist
    assert final["nightmare_summary"] == "I was chased through a forest."
    assert frames[3] == "data: [DONE]"


async def test_non_summary_stage_skips_extraction_and_echoes_request_nightmare_summary(
    agents, monkeypatch
):
    async def fake(chat_input, conversation, safety_enabled=True):
        conversation.stages.append("recording")
        yield 'data: {"content": "Tell me more.", "stages": ["recording"], "language": "en"}\n\n'
        yield "data: [DONE]\n\n"

    async def boom(prose, language):
        raise AssertionError("extract_summary_structured must not run for non-summary stages")

    monkeypatch.setattr(irt_app, "process_chat_message_stream", fake)
    monkeypatch.setattr(chatmod, "extract_summary_structured", boom)
    frames = await _collect_frames({**REQ, "nightmare_summary": "already generated"})

    final = _payload(frames[1])["final"]
    assert final["stage"] == "recording"
    assert final["structured"] is None
    # no usage frame from the core -> zeros fallback, never a missing field
    assert final["usage"] == {"input_tokens": 0, "output_tokens": 0}
    # nightmare_summary from the request survives the rebuilt conversation untouched
    assert final["nightmare_summary"] == "already generated"


async def test_real_generator_emits_usage_frame_folded_into_final(agents, monkeypatch):
    """Drive the REAL process_chat_message_stream (mocked internals): its usage frame
    must reach final.usage through the adapter — pins the core↔adapter wire contract."""

    async def fake_determine_stage(user_input, conversation):
        conversation.stages.append("recording")  # the real router appends before generating
        return "recording"

    async def fake_response_stream(stage, full_prompt, conversation, language,
                                   safety_constraint=None):
        yield "Tell me ", None
        yield "more.", None
        yield "", {"input": 5, "output": 7, "total": 12}  # usage arrives on the last tuple

    monkeypatch.setattr(irt_app, "determine_stage_async", fake_determine_stage)
    monkeypatch.setattr(irt_app, "get_response_stream_async", fake_response_stream)
    frames = await _collect_frames(REQ)

    payloads = [_payload(f) for f in frames[:-1]]
    assert "usage" not in {k for p in payloads for k in p}  # never forwarded raw
    assert [p["content"] for p in payloads[:2]] == ["Tell me ", "more."]
    final = _payload(frames[-2])["final"]
    assert final["response"] == "Tell me more."
    assert final["usage"] == {"input_tokens": 5, "output_tokens": 7}
    assert frames[-1] == "data: [DONE]"


async def test_error_frame_passes_through_then_done_without_final(agents, monkeypatch):
    error = 'data: {"error": "upstream exploded"}'

    async def fake(chat_input, conversation, safety_enabled=True):
        yield 'data: {"content": "part", "stages": ["rewriting"], "language": "en"}\n\n'
        yield error + "\n\n"  # the ported generator ends after its error frame (no [DONE])

    monkeypatch.setattr(irt_app, "process_chat_message_stream", fake)
    frames = await _collect_frames(REQ)

    assert frames == [
        'data: {"content": "part", "stages": ["rewriting"], "language": "en"}',
        error,
        "data: [DONE]",  # terminal frame still emitted; no final frame on failure
    ]


async def test_generator_exception_becomes_error_frame_plus_done(agents, monkeypatch):
    async def fake(chat_input, conversation, safety_enabled=True):
        yield 'data: {"content": "part", "stages": ["rewriting"], "language": "en"}\n\n'
        raise RuntimeError("boom mid-stream")

    monkeypatch.setattr(irt_app, "process_chat_message_stream", fake)
    frames = await _collect_frames(REQ)

    assert _payload(frames[1]) == {"error": "boom mid-stream"}
    assert frames[2] == "data: [DONE]"
    assert len(frames) == 3  # never a half-open stream: error + terminal, no final


def test_empty_messages_rejected(agents):
    # Raised by the call itself, before any frame — like `chat`.
    with pytest.raises(HTTPException) as exc_info:
        chatmod.chat_stream(ChatRequest.model_validate({"messages": [], "language": "en"}))
    assert exc_info.value.status_code == 400


def test_last_message_must_be_user(agents):
    req = ChatRequest.model_validate(
        {"messages": [{"role": "assistant", "content": "hi"}], "language": "en"}
    )
    with pytest.raises(HTTPException) as exc_info:
        chatmod.chat_stream(req)
    assert exc_info.value.status_code == 400


def test_503_when_agents_unconfigured(monkeypatch):
    monkeypatch.setattr(irt_app, "routing_agent", None, raising=False)
    req = ChatRequest.model_validate(
        {"messages": [{"role": "user", "content": "x"}], "language": "en"}
    )
    with pytest.raises(HTTPException) as exc_info:
        chatmod.chat_stream(req)
    assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# Disconnects: closing the stream early closes every layer down to the provider.
# ---------------------------------------------------------------------------


class _ProviderStream:
    """Stand-in for the SDK's AsyncStream: async-iterable, with an async close()
    that closes the HTTP response (optionally failing while it does)."""

    def __init__(self, texts, close_error=None):
        self._texts = texts
        self._close_error = close_error
        self.closed = False

    async def close(self):
        self.closed = True
        if self._close_error is not None:
            raise self._close_error

    def __aiter__(self):
        return self._chunks()

    async def _chunks(self):
        for text in self._texts:
            delta = types.SimpleNamespace(content=text)
            yield types.SimpleNamespace(choices=[types.SimpleNamespace(delta=delta)], usage=None)


@pytest.mark.parametrize("close_error", [None, OSError("close failed")])
async def test_closing_early_closes_the_provider_stream(agents, monkeypatch, close_error):
    """The client went away after the first chunk: closing the adapter's stream
    must close the provider response at once (aclosing in the adapter and the
    core, close() in Agent's finally), not whenever GC finalizes the chain. A
    close that raises must not turn the early close into an error frame
    yielded during aclose (RuntimeError: async generator ignored GeneratorExit)."""

    async def fake_determine_stage(user_input, conversation):
        conversation.stages.append("recording")
        return "recording"

    provider_stream = _ProviderStream(["Tell me ", "more ", "about it."], close_error)

    async def create(**kwargs):
        return provider_stream

    response_agent = Agent(MODELS["MISTRAL_LARGE"], "system prompt")
    response_agent.client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    )
    monkeypatch.setattr(irt_app, "response_agent", response_agent)
    monkeypatch.setattr(irt_app, "determine_stage_async", fake_determine_stage)

    stream = chatmod.chat_stream(ChatRequest.model_validate(REQ))
    async with contextlib.aclosing(stream):
        first = await anext(stream)
        assert json.loads(first.removeprefix("data: "))["content"] == "Tell me "
        assert not provider_stream.closed

    assert provider_stream.closed


async def test_error_frame_closes_the_core_generator(agents, monkeypatch):
    """After the core's error frame the adapter stops reading; the core's
    generator is closed right then, even with frames left in it."""
    closed = []

    async def fake(chat_input, conversation, safety_enabled=True):
        try:
            yield 'data: {"error": "upstream exploded"}\n\n'
            yield 'data: {"content": "never read"}\n\n'
        finally:
            closed.append(True)

    monkeypatch.setattr(irt_app, "process_chat_message_stream", fake)
    frames = await _collect_frames(REQ)

    assert frames == ['data: {"error": "upstream exploded"}', "data: [DONE]"]
    assert closed == [True]
