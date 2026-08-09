from __future__ import annotations

import asyncio

from claude_agent_sdk import ResultMessage

from priva_agent_runner.services.claude_sdk import service


class _RawQuery:
    def __init__(self, frames):
        self.frames = frames

    async def receive_messages(self):
        for frame in self.frames:
            yield frame


class _Client:
    def __init__(self, frames=(), public_messages=()):
        self._query = _RawQuery(frames)
        self.public_messages = public_messages

    async def receive_response(self):
        for message in self.public_messages:
            yield message


def _result():
    return ResultMessage(
        subtype="success",
        duration_ms=10,
        duration_api_ms=8,
        is_error=False,
        num_turns=1,
        session_id="session-1",
        result="done",
    )


async def _collect(client, *, enabled):
    return [
        item
        async for item in service._receive_response_items(
            client,
            prompt_suggestions_enabled=enabled,
        )
    ]


def test_raw_bridge_delivers_suggestion_after_result(monkeypatch):
    result = _result()
    frames = [
        {"type": "result"},
        {
            "type": "prompt_suggestion",
            "suggestion": "Run the focused tests",
            "session_id": "session-1",
            "uuid": "suggestion-1",
        },
    ]
    monkeypatch.setattr(
        service,
        "parse_message",
        lambda raw: result if raw.get("type") == "result" else None,
    )

    items = asyncio.run(_collect(_Client(frames), enabled=True))

    assert items == [
        result,
        {
            "suggestion": "Run the focused tests",
            "session_id": "session-1",
            "uuid": "suggestion-1",
        },
    ]


def test_raw_bridge_ignores_empty_suggestion(monkeypatch):
    result = _result()
    monkeypatch.setattr(
        service,
        "parse_message",
        lambda raw: result if raw.get("type") == "result" else None,
    )

    items = asyncio.run(_collect(_Client([
        {"type": "result"},
        {"type": "prompt_suggestion", "suggestion": "   "},
    ]), enabled=True))

    assert items == [result]


def test_disabled_bridge_uses_public_sdk_response_path():
    result = _result()

    items = asyncio.run(_collect(_Client(public_messages=[result]), enabled=False))

    assert items == [result]


def test_stream_pump_exposes_prompt_suggestion_as_a_wire_event(monkeypatch):
    async def fake_items(*_args, **_kwargs):
        yield {"suggestion": "Inspect the failing test", "session_id": "session-1"}

    monkeypatch.setattr(service, "_receive_response_items", fake_items)

    async def exercise():
        queue = asyncio.Queue()
        await service._pump_stream_messages(
            _Client(),
            queue,
            prompt_suggestions_enabled=True,
        )
        return [await queue.get(), await queue.get()]

    assert asyncio.run(exercise()) == [
        {
            "event": "prompt_suggestion",
            "data": {
                "suggestion": "Inspect the failing test",
                "session_id": "session-1",
            },
        },
        None,
    ]
