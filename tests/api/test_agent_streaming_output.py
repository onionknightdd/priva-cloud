from __future__ import annotations

import asyncio

import pytest
from claude_agent_sdk import AssistantMessage, TextBlock
from claude_agent_sdk.types import StreamEvent

from priva_agent_runner.services.claude_sdk import options as options_module
from priva_agent_runner.services.claude_sdk.run_registry import (
    MAX_BUFFERED_EVENTS,
    RunRecord,
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
            model="profile-model-alias",
        ),
    )

    options = _build_options(
        monkeypatch,
        tmp_path,
        model_override="gateway:profile-model-alias",
    )

    assert options._priva_profile_id == "gateway"
    assert options._priva_model_id == "profile-model-alias"


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


def test_replay_gap_only_when_requested_event_fell_out_of_4000_tail():
    record = RunRecord("run-1")
    for index in range(MAX_BUFFERED_EVENTS + 1):
        record.record_event("stream_event", {"index": index})

    assert record.first_seq == 2
    assert record.has_replay_gap(0) is True
    assert record.has_replay_gap(1) is False
    assert record.replay_since(0)[0][0] == 2
