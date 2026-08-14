from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage

from priva_agent_runner.routers import agent as agent_router
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
            model={
                "id": "ollama:llama3:8b",
                "capabilities": {"context": "1m"},
            },
            observed_at=123,
        )

        self.assertEqual(
            session_meta.get_last_response_model("session-1"),
            {
                "profile_id": "ollama",
                "model": {
                    "id": "ollama:llama3:8b",
                    "capabilities": {"context": "1m"},
                },
                "observed_at": 123,
            },
        )
        self.assertEqual(
            session_meta.read_meta()["last_response_models"]["session-1"]["model_source"],
            "profile",
        )

    def test_legacy_suffixed_model_id_migrates_to_context_capability(self) -> None:
        meta = {
            "last_response_models": {
                "session-1": {
                    "profile_id": "default",
                    "model_id": "claude-sonnet-4-5[1M]",
                    "model_source": "profile",
                    "observed_at": 456,
                }
            }
        }

        self.assertEqual(
            session_meta.get_last_response_model("session-1", meta),
            {
                "profile_id": "default",
                "model": {
                    "id": "claude-sonnet-4-5",
                    "capabilities": {"context": "1m"},
                },
                "observed_at": 456,
            },
        )

    async def test_response_model_without_profile_is_not_persisted(self) -> None:
        await session_meta.set_last_response_model(
            "session-1",
            profile_id=None,
            model={"id": "unqualified-model", "capabilities": {"context": None}},
        )

        self.assertIsNone(session_meta.get_last_response_model("session-1"))

    async def test_prune_removes_response_model_metadata(self) -> None:
        await session_meta.set_last_response_model(
            "session-1",
            profile_id="default",
            model={"id": "claude-sonnet-4-5", "capabilities": {"context": None}},
        )

        await session_meta.prune_session("session-1")

        self.assertIsNone(session_meta.get_last_response_model("session-1"))

    async def test_live_message_snapshot_returns_matching_sequence_barrier(self) -> None:
        session_id = "live-snapshot-session"
        record = agent_router.run_registry.create(session_id=session_id)
        record.record_event("assistant", {"content": []})
        try:
            with (
                patch.object(agent_router, "_find_session_cwd", return_value=self._tmp.name),
                patch.object(agent_router, "get_session_messages", return_value=[]),
                patch.object(agent_router, "_build_message_replay_metadata", return_value={}),
                patch.object(agent_router, "_load_subagent_session_messages", return_value=[]),
                patch.object(agent_router, "read_add_dirs", return_value=[]),
                patch.object(
                    session_meta,
                    "ensure_existing_session_run_mode",
                    new=AsyncMock(return_value="agent"),
                ),
                patch.object(
                    session_meta,
                    "record_recent_activity",
                    new=AsyncMock(),
                ),
            ):
                response = await agent_router.get_agent_session_messages(
                    session_id,
                    user=None,
                )

            self.assertEqual(response.live_run_id, record.run_id)
            self.assertEqual(response.live_seq, record.next_seq - 1)
            self.assertEqual(response.live_first_seq, record.first_seq)
        finally:
            agent_router.run_registry.finish(record, "completed")


class SessionInfoResponseModelTests(unittest.TestCase):
    @staticmethod
    def _source(session_id: str = "session-1") -> SimpleNamespace:
        return SimpleNamespace(
            session_id=session_id,
            summary="hello",
            last_modified=100,
            file_size=200,
            custom_title=None,
            first_prompt="hello",
            git_branch=None,
            cwd="/workspace/user",
            tag=None,
        )

    def test_session_info_includes_persisted_response_model(self) -> None:
        meta = {
            "sessions": {},
            "scheduler_sessions": {},
            "last_response_models": {
                "session-1": {
                    "profile_id": "default",
                    "model": {
                        "id": "claude-sonnet-4-5",
                        "capabilities": {"context": "1m"},
                    },
                    "model_source": "profile",
                    "observed_at": 123,
                }
            },
            "tag_colors": {},
        }

        response = _session_info_to_response(self._source(), meta)

        self.assertIsNotNone(response.last_response_model)
        self.assertEqual(response.last_response_model.profile_id, "default")
        self.assertEqual(response.last_response_model.model.id, "claude-sonnet-4-5")
        self.assertEqual(response.last_response_model.model.capabilities.context, "1m")

    def test_session_info_backfills_latest_real_model_from_legacy_transcript(self) -> None:
        rows = [
            {
                "type": "assistant",
                "timestamp": "2026-07-15T14:37:00.000Z",
                "message": {"model": "older-model"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-07-15T14:38:26.123Z",
                "message": {"model": "legacy-model"},
            },
            {
                "type": "assistant",
                "isSidechain": True,
                "timestamp": "2026-07-15T14:39:00.000Z",
                "message": {"model": "sidechain-model"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-07-15T14:40:00.000Z",
                "message": {"model": "<synthetic>"},
            },
        ]
        meta = {
            "sessions": {},
            "scheduler_sessions": {},
            "last_response_models": {},
            "tag_colors": {},
        }

        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "session-legacy.jsonl"
            transcript.write_text(
                "\n".join(json.dumps(row) for row in rows),
                encoding="utf-8",
            )
            agent_router._cached_transcript_response_model.cache_clear()
            with patch.object(
                agent_router,
                "_session_jsonl_path",
                return_value=transcript,
            ):
                response = _session_info_to_response(
                    self._source("session-legacy"),
                    meta,
                    {"legacy-model": "profile-legacy"},
                )

        self.assertIsNotNone(response.last_response_model)
        self.assertEqual(response.last_response_model.profile_id, "profile-legacy")
        self.assertEqual(response.last_response_model.model.id, "legacy-model")
        self.assertIsNone(response.last_response_model.model.capabilities.context)
        self.assertEqual(response.last_response_model.observed_at, 1784126306123)

    def test_transcript_backfill_rejects_gateway_mapped_model(self) -> None:
        rows = [
            {
                "type": "assistant",
                "timestamp": "2026-07-15T14:38:26.123Z",
                "message": {"model": "provider-backend-model"},
            },
        ]
        meta = {
            "sessions": {},
            "scheduler_sessions": {},
            "last_response_models": {},
            "tag_colors": {},
        }

        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "session-gateway.jsonl"
            transcript.write_text(
                "\n".join(json.dumps(row) for row in rows),
                encoding="utf-8",
            )
            agent_router._cached_transcript_response_model.cache_clear()
            with patch.object(
                agent_router,
                "_session_jsonl_path",
                return_value=transcript,
            ):
                response = _session_info_to_response(
                    self._source("session-gateway"),
                    meta,
                    {"profile-model-alias": "profile-a"},
                )

        self.assertIsNone(response.last_response_model)

    def test_legacy_gateway_metadata_is_not_restored_as_profile_model(self) -> None:
        meta = {
            "sessions": {},
            "scheduler_sessions": {},
            "last_response_models": {
                "session-1": {
                    "profile_id": "profile-a",
                    "model_id": "provider-backend-model",
                    "observed_at": 123,
                }
            },
            "tag_colors": {},
        }

        with patch.object(agent_router, "_last_response_model_from_transcript") as backfill:
            response = _session_info_to_response(
                self._source(),
                meta,
                {"profile-model-alias": "profile-a"},
            )

        self.assertIsNone(response.last_response_model)
        backfill.assert_not_called()

    def test_session_info_infers_missing_profile_for_persisted_model(self) -> None:
        meta = {
            "sessions": {},
            "scheduler_sessions": {},
            "last_response_models": {
                "session-1": {
                    "profile_id": None,
                    "model_id": "configured-model",
                    "observed_at": 123,
                }
            },
            "tag_colors": {},
        }

        response = _session_info_to_response(
            self._source(),
            meta,
            {"configured-model": "profile-a"},
        )

        self.assertIsNotNone(response.last_response_model)
        self.assertEqual(response.last_response_model.profile_id, "profile-a")

    def test_profile_inference_requires_unique_model_owner(self) -> None:
        profiles = [
            SimpleNamespace(
                id="profile-a",
                default_model="shared-model",
                opus_model="unique-a",
            ),
            SimpleNamespace(
                id="profile-b",
                default_model="shared-model",
                haiku_model="unique-b",
            ),
        ]

        with patch.object(
            agent_router.llm_profile_store,
            "read",
            return_value=(profiles, "profile-a"),
        ):
            profile_by_model = agent_router._configured_profile_by_model()

        self.assertIsNone(profile_by_model["shared-model"])
        self.assertEqual(profile_by_model["unique-a"], "profile-a")
        self.assertEqual(profile_by_model["unique-b"], "profile-b")


class AgentRunResponseModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_run_persists_profile_model_not_gateway_response_model(self) -> None:
        preallocated_session_id: str | None = None

        class FakeClient:
            def __init__(self, options):
                nonlocal preallocated_session_id
                self.options = options
                preallocated_session_id = options.session_id

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def query(self, _prompt):
                return None

            async def receive_response(self):
                session_id = self.options.session_id
                assert session_id
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
                _priva_model_id="requested-model",
                _priva_model_capabilities={"context": "1m"},
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

        self.assertIsNotNone(preallocated_session_id)
        persist_model.assert_awaited_once_with(
            preallocated_session_id,
            model={
                "id": "requested-model",
                "capabilities": {"context": "1m"},
            },
            profile_id="profile-a",
        )

    async def test_agent_run_events_persists_profile_model_for_streaming_turn(self) -> None:
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
                yield AssistantMessage(content=[], model="provider-streamed-model")
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
                _priva_model_id="profile-stream-model",
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
                new_session_id=session_id,
            )

        persist_model.assert_awaited_once_with(
            session_id,
            model={
                "id": "profile-stream-model",
                "capabilities": {"context": None},
            },
            profile_id="profile-stream",
        )
        self.assertIn("result", emitted)

    async def test_vision_scoped_runtime_is_closed_after_turn(self) -> None:
        session_id = "33333333-4444-5555-6666-777777777777"
        exited: list[bool] = []

        class FakeClient:
            def __init__(self, options):
                self.options = options

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                exited.append(True)
                return False

            async def query(self, _prompt):
                return None

            async def interrupt(self):
                return None

            async def set_permission_mode(self, _mode):
                return None

            async def receive_response(self):
                yield SystemMessage("init", {"session_id": session_id})
                yield ResultMessage(
                    subtype="success",
                    duration_ms=1,
                    duration_api_ms=1,
                    is_error=False,
                    num_turns=1,
                    session_id=session_id,
                    result="done",
                    usage={},
                )

        async def fake_build_options(*_args, **_kwargs):
            return SimpleNamespace(
                cwd="/tmp",
                resume=None,
                permission_mode="bypassPermissions",
                _priva_profile_id="vision-profile",
                _priva_model_id="vision-model",
                _priva_vision_image_paths=("/tmp/one-image.png",),
            )

        async def emit(_event: str, _data: dict) -> None:
            return None

        with (
            patch.object(service, "ClaudeSDKClient", FakeClient),
            patch.object(service, "build_agent_options", new=fake_build_options),
            patch.object(service, "_make_unified_can_use_tool", return_value=None),
            patch.object(service, "_audit_skill_prompt"),
            patch.object(service, "_audit_run_completed"),
            patch.object(service, "_track_vision_session"),
            patch.object(service.session_meta, "record_recent_activity", new=AsyncMock()),
            patch.object(service.session_title, "spawn", return_value=None),
            patch.object(service.session_title, "settle", new=AsyncMock()),
            patch.object(service.session_recap, "spawn"),
            patch.object(service.asyncio, "sleep", new=AsyncMock()),
        ):
            await service.agent_run_events(
                "inspect image",
                username="user",
                emit=emit,
                new_session_id=session_id,
            )

        self.assertEqual(exited, [True])
