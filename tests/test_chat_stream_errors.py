"""Regression tests for graceful SSE errors instead of broken HTTP streams."""

import asyncio
import json

import agent.langgraph_runtime as runtime
import agent.llm_client as llm_client


def test_chat_stream_serializes_unhandled_runtime_error(monkeypatch):
    async def broken_stream(*_args, **_kwargs):
        if False:
            yield ""
        raise RuntimeError("checkpoint unavailable")

    async def collect() -> list[str]:
        return [
            event
            async for event in llm_client.chat_stream(
                "test-user", "hello", "test-session"
            )
        ]

    monkeypatch.setattr(runtime, "langgraph_chat_stream", broken_stream)

    events = asyncio.run(collect())
    assert len(events) == 1
    assert events[0].startswith("data: ")
    payload = json.loads(events[0][6:])
    assert payload["type"] == "error"
    assert "RuntimeError" in payload["message"]
    assert "checkpoint unavailable" in payload["message"]
