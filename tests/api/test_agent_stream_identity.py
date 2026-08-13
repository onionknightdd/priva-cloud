from __future__ import annotations

import uuid

import pytest

from priva_agent_runner.services.claude_sdk import service


@pytest.mark.asyncio
async def test_sse_fork_preallocates_target_distinct_from_source(monkeypatch):
    source_session_id = "11111111-2222-3333-4444-555555555555"
    captured: dict[str, object] = {}

    async def fake_agent_run_events(
        *_args,
        emit,
        new_session_id=None,
        fork_session=False,
        **_kwargs,
    ):
        captured["new_session_id"] = new_session_id
        captured["fork_session"] = fork_session
        await emit("result", {
            "session_id": new_session_id,
            "is_error": False,
            "usage": {},
        })

    monkeypatch.setattr(service, "agent_run_events", fake_agent_run_events)
    chunks = [
        chunk
        async for chunk in service.agent_run_stream(
            "fork this",
            session_id=source_session_id,
            fork_session=True,
        )
    ]

    target = captured["new_session_id"]
    assert isinstance(target, str)
    assert uuid.UUID(target)
    assert target != source_session_id
    assert captured["fork_session"] is True
    assert any(chunk.startswith("event: result\n") for chunk in chunks)
