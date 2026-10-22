"""FakeLlmClient sanity checks."""

from __future__ import annotations

import pytest

from redream_ai.llm.fake_client import FakeLlmClient


async def test_chat_returns_enqueued_response(fake_llm: FakeLlmClient) -> None:
    fake_llm.enqueue("That sounds heavy.", input_tokens=12, output_tokens=4)
    content, usage = await fake_llm.chat(model="x", messages=[])
    assert content == "That sounds heavy."
    assert usage == {"input_tokens": 12, "output_tokens": 4}


async def test_chat_records_call_args(fake_llm: FakeLlmClient) -> None:
    fake_llm.enqueue("ok")
    await fake_llm.chat(
        model="ministral-8b-2512",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    assert len(fake_llm.calls) == 1
    call = fake_llm.calls[0]
    assert call["model"] == "ministral-8b-2512"
    assert call["temperature"] == 0.1
    assert call["response_format"] == {"type": "json_object"}


async def test_empty_queue_raises(fake_llm: FakeLlmClient) -> None:
    with pytest.raises(RuntimeError, match="queue is empty"):
        await fake_llm.chat(model="x", messages=[])


async def test_stream_yields_word_by_word(fake_llm: FakeLlmClient) -> None:
    fake_llm.enqueue("hello world friend", input_tokens=3, output_tokens=3)
    chunks = [c async for c in fake_llm.chat_stream(model="x", messages=[])]

    # 3 text chunks + 1 final usage chunk
    assert len(chunks) == 4
    assembled = "".join(c.get("text", "") for c in chunks)
    assert assembled == "hello world friend"
    final = chunks[-1]
    assert final.get("finish_reason") == "stop"
    assert final.get("input_tokens") == 3
    assert final.get("output_tokens") == 3
