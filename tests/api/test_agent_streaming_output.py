from __future__ import annotations

import asyncio

import pytest
from claude_agent_sdk import AssistantMessage, TextBlock, UserMessage
from claude_agent_sdk.types import StreamEvent

from priva_agent_runner.services.claude_sdk import options as options_module
from priva_agent_runner.services.claude_sdk import run_registry as run_registry_module
from priva_agent_runner.services.claude_sdk.run_registry import (
    MAX_BUFFERED_BYTES,
    MAX_BUFFERED_EVENTS,
    MAX_SUBSCRIBER_EVENTS,
    SUBSCRIBER_OVERFLOW_EVENT,
    RunAlreadyActiveError,
    RunRecord,
    RunRegistry,
)
from priva_agent_runner.services.claude_sdk.bounded_queue import BoundedAsyncQueue
from priva_agent_runner.services.claude_sdk.service import _pump_stream_messages
from priva_agent_runner.services.claude_sdk.session_runtime_pool import (
    RuntimeMessageEnvelope,
)
from priva_common.models.agent import WsInitFrame
from priva_common.serialization import get_event_label, serialize_message


def _build_options(monkeypatch, tmp_path, **overrides):
    from priva_agent_runner.services import sandbox_venv, skills
    from priva_agent_runner.services.hooks import builder as hooks_builder
    from priva_common import user_store

    class RuntimeStore:
        def get_runtime_config(self):
            return {}

    monkeypatch.setattr(
        options_module,
        "read_runtime_settings",
        lambda: {
            "extra_env_enabled": False,
            "extra_env": {},
            "prompt_suggestion_enabled": False,
            "agent_teams_enabled": False,
        },
    )
    monkeypatch.setattr(
        options_module,
        "read_settings_env",
        lambda: {
            "ANTHROPIC_BASE_URL": "https://example.invalid",
            "ANTHROPIC_AUTH_TOKEN": "test-token",
            "ANTHROPIC_MODEL": "test-model",
        },
    )
    monkeypatch.setattr(sandbox_venv, "venv_env_overlay", lambda _env: {})
    monkeypatch.setattr(skills, "compute_enabled_skill_names", lambda _username: [])
    monkeypatch.setattr(user_store, "get_user_store", lambda: RuntimeStore())
    monkeypatch.setattr(hooks_builder, "build_hooks", lambda *_args, **_kwargs: {})

    return asyncio.run(options_module.build_agent_options(
        username="alice",
        cwd=str(tmp_path),
        auth_method="jwt",
        mcp_servers="disable",
        **overrides,
    ))


def test_ws_partial_messages_is_explicit_opt_in():
    base = {"message": "hello"}
    assert WsInitFrame(**base).include_partial_messages is False
    assert WsInitFrame(**base, include_partial_messages=True).include_partial_messages is True


@pytest.mark.parametrize("enabled", [False, True])
def test_sdk_options_preserve_partial_message_flag(monkeypatch, tmp_path, enabled):
    options = _build_options(
        monkeypatch,
        tmp_path,
        include_partial_messages=enabled,
    )
    assert options.include_partial_messages is enabled


def test_sdk_options_preserve_profile_side_model_identity(monkeypatch, tmp_path):
    from priva_agent_runner.services.llm_profiles import ResolvedProfile
    from priva_common.models.llm_profiles import LlmProfile

    profile = LlmProfile(
        id="gateway",
        label="Gateway",
        base_url="https://example.invalid",
        auth_token="secret",
        default_model="profile-model-alias",
    )
    monkeypatch.setattr(
        options_module,
        "resolve_model",
        lambda _reference: ResolvedProfile(
            profile=profile,
            model="profile-model-alias[1m]",
        ),
    )

    options = _build_options(
        monkeypatch,
        tmp_path,
        model_override="gateway:profile-model-alias",
    )

    assert options._priva_profile_id == "gateway"
    assert options._priva_model_id == "profile-model-alias"
    assert options._priva_model_capabilities == {"context": "1m"}
    assert options.model == "profile-model-alias[1m]"


def test_stream_event_uses_stable_whitelisted_wire_envelope():
    message = StreamEvent(
        uuid="transport-event-uuid",
        session_id="session-1",
        parent_tool_use_id=None,
        event={
            "type": "content_block_delta",
            "index": 2,
            "delta": {"type": "text_delta", "text": "hello"},
        },
    )

    assert get_event_label(message) == "stream_event"
    assert serialize_message(message) == {
        "type": "stream_event",
        "uuid": "transport-event-uuid",
        "session_id": "session-1",
        "parent_tool_use_id": None,
        "event": message.event,
    }


def test_complete_assistant_carries_reconciliation_identity():
    message = AssistantMessage(
        content=[TextBlock(text="done")],
        model="deepseek-v4-flash",
        message_id="provider-message-id",
        stop_reason="end_turn",
        session_id="session-1",
        uuid="assistant-event-uuid",
    )

    payload = serialize_message(message)
    assert payload["message_id"] == "provider-message-id"
    assert payload["session_id"] == "session-1"
    assert payload["uuid"] == "assistant-event-uuid"
    assert payload["stop_reason"] == "end_turn"


@pytest.mark.asyncio
async def test_pooled_message_pump_preserves_raw_peer_origin():
    origin = {
        "kind": "peer",
        "session_id": "sender-session",
        "name": "Research session",
    }
    message = UserMessage(
        content="Another Claude session sent a message: hello",
        uuid="peer-message-uuid",
    )

    class PooledClient:
        async def iter_response_items(self, *, prompt_suggestions_enabled):
            assert prompt_suggestions_enabled is False
            yield RuntimeMessageEnvelope(message=message, origin=origin)

    queue = BoundedAsyncQueue(maxsize=4, max_bytes=4096)
    await _pump_stream_messages(PooledClient(), queue)
    item = await queue.get()
    assert item is not None
    assert item["event"] == "user_message"
    assert item["data"]["origin"] == origin
    assert item["data"]["native_peer_turn"] is True
    assert await queue.get() is None


@pytest.mark.asyncio
async def test_cancelling_message_pump_does_not_block_on_full_output_queue():
    receiver_started = asyncio.Event()

    class WaitingClient:
        async def receive_response(self):
            receiver_started.set()
            await asyncio.Event().wait()
            yield  # pragma: no cover - keeps this an async generator

    queue = BoundedAsyncQueue(maxsize=1, max_bytes=4096)
    queue.put_nowait({"event": "already_full", "data": {}})
    pump = asyncio.create_task(_pump_stream_messages(WaitingClient(), queue))
    await receiver_started.wait()

    pump.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(pump, timeout=0.5)

    assert queue.qsize() == 1


def test_replay_gap_only_when_requested_event_fell_out_of_4000_tail():
    record = RunRecord("run-1")
    for index in range(MAX_BUFFERED_EVENTS + 1):
        record.record_event("stream_event", {"index": index})

    assert record.first_seq == 2
    assert record.has_replay_gap(0) is True
    assert record.has_replay_gap(1) is False
    assert record.replay_since(0)[0][0] == 2


def test_running_snapshot_exposes_first_retained_sequence():
    registry = RunRegistry()
    record = registry.create(session_id="running-sequence")
    for index in range(MAX_BUFFERED_EVENTS + 1):
        record.record_event("stream_event", {"index": index})
    try:
        [snapshot] = registry.list_active()
        assert snapshot["first_seq"] == record.first_seq == 2
        assert snapshot["last_seq"] == record.next_seq - 1
    finally:
        registry.finish(record, "completed")


def test_replay_buffer_is_also_bounded_by_serialized_bytes():
    record = RunRecord("run-bytes")
    record.record_event("tool_result", {"body": "x" * (MAX_BUFFERED_BYTES + 1)})

    assert record.events == []
    assert record.first_seq == 2
    assert record.has_replay_gap(0) is True

    record.record_event("result", {"session_id": "session-a"})
    assert record.events[0][0] == 2


def test_slow_subscriber_is_detached_with_unsequenced_overflow_marker():
    record = RunRecord("run-slow-subscriber")
    sub_id, queue = record.subscribe()
    for index in range(MAX_SUBSCRIBER_EVENTS + 1):
        record.record_event("stream_event", {"index": index})

    assert sub_id not in record.subscribers
    assert queue.qsize() == 1
    seq, event_type, _ = queue.get_nowait()
    assert seq == 0
    assert event_type == SUBSCRIBER_OVERFLOW_EVENT


def test_large_subscriber_event_hits_byte_limit_without_stopping_run(monkeypatch):
    monkeypatch.setattr(run_registry_module, "MAX_SUBSCRIBER_BYTES", 128)
    record = RunRecord("run-large-subscriber")
    sub_id, queue = record.subscribe()

    record.record_event("tool_result", {"body": "x" * 256})

    assert record.live
    assert record.events
    assert sub_id not in record.subscribers
    assert queue.qsize() == 1
    seq, event_type, _ = queue.get_nowait()
    assert seq == 0
    assert event_type == SUBSCRIBER_OVERFLOW_EVENT


def test_registry_atomically_rejects_two_live_runs_for_one_session():
    registry = RunRegistry()
    first = registry.create(session_id="session-a")
    with pytest.raises(RunAlreadyActiveError):
        registry.create(session_id="session-a")

    registry.finish(first, "completed")
    second = registry.create(session_id="session-a")
    registry.finish(second, "completed")


def test_terminal_registry_has_global_count_and_byte_bounds(monkeypatch):
    monkeypatch.setattr(run_registry_module, "MAX_TERMINAL_RECORDS", 2)
    monkeypatch.setattr(run_registry_module, "MAX_TERMINAL_BYTES", 900)
    registry = RunRegistry()
    records = []
    for index in range(3):
        record = registry.create(session_id=f"bounded-terminal-{index}")
        record.record_event("tool_result", {"body": "x" * 400})
        registry.finish(record, "completed")
        records.append(record)

    assert registry.get(session_id="bounded-terminal-0") is None
    retained = [
        record
        for record in records
        if registry.get(session_id=record.session_id) is record
    ]
    assert len(retained) <= 2
    assert sum(record.buffered_bytes for record in retained) <= 900


@pytest.mark.asyncio
async def test_registry_shutdown_signals_tasks_and_finalizes_orphan_records():
    registry = RunRegistry()
    task_started = asyncio.Event()

    async def owner(cancelled: asyncio.Event) -> None:
        task_started.set()
        await cancelled.wait()

    owned = registry.create(session_id="shutdown-owned")
    owned.task = asyncio.create_task(owner(owned.cancelled))
    orphan = registry.create(session_id="shutdown-orphan")
    await task_started.wait()

    tasks = registry.cancel_active()
    assert tasks == [owned.task]
    assert owned.cancelled.is_set()
    assert orphan.cancelled.is_set()
    await asyncio.gather(*tasks)

    registry.finish_cancelled()
    assert owned.status == "aborted"
    assert orphan.status == "aborted"
    assert owned.task is None
    assert any(kind == "__run_end__" for _, kind, _ in owned.events)
