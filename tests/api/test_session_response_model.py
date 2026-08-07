from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage

from priva_agent_runner.routers.agent import _session_info_to_response
from priva_agent_runner.services.claude_sdk import session_meta
from priva_agent_runner.services.claude_sdk import service


class SessionResponseModelMetadataTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._previous_config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self._tmp.name
        self.addCleanup(self._restore_config_dir)

    def _restore_config_dir(self) -> None:
        if self._previous_config_dir is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._previous_config_dir

    async def test_round_trip_keeps_profile_and_colon_in_model_id(self) -> None:
        await session_meta.set_last_response_model(
            "session-1",
            profile_id="ollama",
            model_id="ollama:llama3:8b",
            observed_at=123,
        )

        self.assertEqual(
            session_meta.get_last_response_model("session-1"),
            {
                "profile_id": "ollama",
                "model_id": "ollama:llama3:8b",
                "observed_at": 123,
            },
        )

    async def test_prune_removes_response_model_metadata(self) -> None:
        await session_meta.set_last_response_model(
            "session-1", profile_id="default", model_id="claude-sonnet-4-5"
        )

        await session_meta.prune_session("session-1")

        self.assertIsNone(session_meta.get_last_response_model("session-1"))


class SessionInfoResponseModelTests(unittest.TestCase):
    def test_session_info_includes_persisted_response_model(self) -> None:
        source = SimpleNamespace(
            session_id="session-1",
            summary="hello",
            last_modified=100,
            file_size=200,
            custom_title=None,
            first_prompt="hello",
            git_branch=None,
            cwd="/workspace/user",
            tag=None,
        )
        meta = {
            "sessions": {},
            "scheduler_sessions": {},
            "last_response_models": {
                "session-1": {
                    "profile_id": "default",
                    "model_id": "claude-sonnet-4-5",
                    "observed_at": 123,
                }
            },
            "tag_colors": {},
        }

        response = _session_info_to_response(source, meta)

        self.assertIsNotNone(response.last_response_model)
        self.assertEqual(response.last_response_model.profile_id, "default")
        self.assertEqual(response.last_response_model.model_id, "claude-sonnet-4-5")


class AgentRunResponseModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_run_persists_provider_model_not_request_reference(self) -> None:
        session_id = "11111111-2222-3333-4444-555555555555"

        class FakeClient:
            def __init__(self, options):
                self.options = options

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def query(self, _prompt):
                return None

            async def receive_response(self):
                yield SystemMessage("init", {"session_id": session_id})
                yield AssistantMessage(content=[], model="provider:model-with-colon")
                yield ResultMessage(
                    subtype="success",
                    duration_ms=1,
                    duration_api_ms=1,
                    is_error=False,
                    num_turns=1,
                    session_id=session_id,
                    result="done",
                    usage={"input_tokens": 1, "output_tokens": 1},
                )

        async def fake_build_options(*_args, **_kwargs):
            return SimpleNamespace(
                cwd="/tmp",
                resume=None,
                _priva_profile_id="profile-a",
            )

        persist_model = AsyncMock()
        with (
            patch.object(service, "ClaudeSDKClient", FakeClient),
            patch.object(service, "build_agent_options", new=fake_build_options),
            patch.object(
                service,
                "_model_ref_for_images",
                return_value=("profile-a:requested-model", "profile-a", None),
            ),
            patch.object(service, "_audit_skill_prompt"),
            patch.object(service, "_audit_run_completed"),
            patch.object(service, "_track_vision_session"),
            patch.object(service.session_meta, "record_recent_activity", new=AsyncMock()),
            patch.object(service.session_meta, "set_last_response_model", new=persist_model),
            patch.object(service.session_title, "spawn", return_value=None),
            patch.object(service.session_title, "settle", new=AsyncMock()),
            patch.object(service.session_recap, "spawn"),
            patch.object(service.asyncio, "sleep", new=AsyncMock()),
        ):
            await service.agent_run("hello", model_override="profile-a:requested-model")

        persist_model.assert_awaited_once_with(
            session_id,
            model_id="provider:model-with-colon",
            profile_id="profile-a",
        )

    async def test_agent_run_events_persists_observed_model_for_streaming_turn(self) -> None:
        session_id = "22222222-3333-4444-5555-666666666666"
        emitted: list[str] = []

        class FakeClient:
            def __init__(self, options):
                self.options = options

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def query(self, _prompt):
                return None

            async def interrupt(self):
                return None

            async def receive_response(self):
                yield SystemMessage("init", {"session_id": session_id})
                yield AssistantMessage(content=[], model="streamed-model")
                yield ResultMessage(
                    subtype="success",
                    duration_ms=1,
                    duration_api_ms=1,
                    is_error=False,
                    num_turns=1,
                    session_id=session_id,
                    result="done",
                    usage={"input_tokens": 1, "output_tokens": 1},
                )

        async def fake_build_options(*_args, **_kwargs):
            return SimpleNamespace(
                cwd="/tmp",
                resume=None,
                _priva_profile_id="profile-stream",
            )

        async def emit(event: str, _data: dict) -> None:
            emitted.append(event)

        persist_model = AsyncMock()
        with (
            patch.object(service, "ClaudeSDKClient", FakeClient),
            patch.object(service, "build_agent_options", new=fake_build_options),
            patch.object(service, "_make_unified_can_use_tool", return_value=None),
            patch.object(service, "_audit_skill_prompt"),
            patch.object(service, "_audit_run_completed"),
            patch.object(service, "_track_vision_session"),
            patch.object(service.session_meta, "record_recent_activity", new=AsyncMock()),
            patch.object(service.session_meta, "set_last_response_model", new=persist_model),
            patch.object(service.session_title, "spawn", return_value=None),
            patch.object(service.session_title, "settle", new=AsyncMock()),
            patch.object(service.session_recap, "spawn"),
            patch.object(service.asyncio, "sleep", new=AsyncMock()),
        ):
            await service.agent_run_events(
                "hello",
                username="user",
                emit=emit,
            )

        persist_model.assert_awaited_once_with(
            session_id,
            model_id="streamed-model",
            profile_id="profile-stream",
        )
        self.assertIn("result", emitted)
