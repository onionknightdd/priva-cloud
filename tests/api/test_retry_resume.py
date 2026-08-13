"""Tests for the retry/resume helpers used by service.agent_run_events.

Covers the on-disk session JSONL transformations that run between retry
attempts: stripping synthetic-error rows so the model never sees its own
error, and healing orphan tool_use blocks so the resumed conversation
remains structurally valid for the Anthropic API.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage
from claude_agent_sdk._internal.sessions import _canonicalize_path, _get_project_dir

from priva_agent_runner.services.claude_sdk import service
from priva_agent_runner.services.claude_sdk.retry import strip_synthetic_records
from priva_agent_runner.services.claude_sdk.session_heal import heal_orphan_tool_uses


SESSION_ID = "11111111-2222-3333-4444-555555555555"


def _session_path(config_dir: Path, cwd: str) -> Path:
    project_dir = _get_project_dir(_canonicalize_path(cwd))
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir / f"{SESSION_ID}.jsonl"


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


class StripSyntheticRecordsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._prev_config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self._tmp.name
        self.addCleanup(self._restore_env)
        # cwd must point at a real, canonicalisable directory.
        self._cwd_obj = tempfile.TemporaryDirectory()
        self.addCleanup(self._cwd_obj.cleanup)
        self.cwd = self._cwd_obj.name

    def _restore_env(self) -> None:
        if self._prev_config_dir is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._prev_config_dir

    def test_removes_synthetic_keeps_real(self) -> None:
        path = _session_path(Path(self._tmp.name), self.cwd)
        _write_jsonl(path, [
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            {"type": "assistant", "message": {"model": "claude-sonnet-4-5", "content": [{"type": "text", "text": "hello"}]}},
            {"type": "assistant", "message": {"model": "<synthetic>", "content": [{"type": "text", "text": "API Error"}]}},
            {"type": "assistant", "message": {"model": "claude-sonnet-4-5", "content": [{"type": "text", "text": "bye"}]}},
        ])

        removed = strip_synthetic_records(SESSION_ID, self.cwd)

        self.assertEqual(removed, 1)
        remaining = _read_jsonl(path)
        self.assertEqual(len(remaining), 3)
        models = [r.get("message", {}).get("model") for r in remaining]
        self.assertNotIn("<synthetic>", models)

    def test_noop_when_no_synthetic(self) -> None:
        path = _session_path(Path(self._tmp.name), self.cwd)
        _write_jsonl(path, [
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            {"type": "assistant", "message": {"model": "claude-sonnet-4-5", "content": []}},
        ])
        before = path.read_bytes()

        removed = strip_synthetic_records(SESSION_ID, self.cwd)

        self.assertEqual(removed, 0)
        # File untouched — no rewrite when nothing to remove.
        self.assertEqual(path.read_bytes(), before)

    def test_missing_file_returns_zero(self) -> None:
        removed = strip_synthetic_records(SESSION_ID, self.cwd)
        self.assertEqual(removed, 0)

    def test_missing_session_id_returns_zero(self) -> None:
        # Path-less call (defensive — service code skips this branch but the
        # helper should not crash if invoked with empty inputs).
        self.assertEqual(strip_synthetic_records(None, self.cwd), 0)
        self.assertEqual(strip_synthetic_records(SESSION_ID, None), 0)


class HealOrphanToolUsesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._prev_config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self._tmp.name
        self.addCleanup(self._restore_env)
        self._cwd_obj = tempfile.TemporaryDirectory()
        self.addCleanup(self._cwd_obj.cleanup)
        self.cwd = self._cwd_obj.name

    def _restore_env(self) -> None:
        if self._prev_config_dir is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._prev_config_dir

    def test_appends_synthetic_tool_result_for_orphan(self) -> None:
        path = _session_path(Path(self._tmp.name), self.cwd)
        _write_jsonl(path, [
            {"type": "user", "message": {"role": "user", "content": "read file"}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "tu_paired", "name": "Read"},
                {"type": "tool_use", "id": "tu_orphan", "name": "Bash"},
            ]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "tu_paired", "content": "ok"},
            ]}},
        ])

        healed = heal_orphan_tool_uses(SESSION_ID, self.cwd)

        self.assertEqual(healed, 1)
        records = _read_jsonl(path)
        last = records[-1]
        self.assertEqual(last["type"], "user")
        blocks = last["message"]["content"]
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["type"], "tool_result")
        self.assertEqual(blocks[0]["tool_use_id"], "tu_orphan")
        self.assertTrue(blocks[0]["is_error"])
        self.assertIn("Bash", blocks[0]["content"])

    def test_noop_when_all_paired(self) -> None:
        path = _session_path(Path(self._tmp.name), self.cwd)
        _write_jsonl(path, [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "tu_1", "name": "Read"},
            ]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": "ok"},
            ]}},
        ])
        before = path.read_bytes()

        healed = heal_orphan_tool_uses(SESSION_ID, self.cwd)

        self.assertEqual(healed, 0)
        self.assertEqual(path.read_bytes(), before)

    def test_missing_file_returns_zero(self) -> None:
        self.assertEqual(heal_orphan_tool_uses(SESSION_ID, self.cwd), 0)

    def test_missing_session_id_returns_zero(self) -> None:
        self.assertEqual(heal_orphan_tool_uses(None, self.cwd), 0)


class RetryOrchestrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_run_resumes_pending_turn_without_resending_prompt(self) -> None:
        queries: list[tuple[str | None, str | list[dict]]] = []
        preallocated_session_id: str | None = None

        class FakeClient:
            attempts = 0

            def __init__(self, options):
                nonlocal preallocated_session_id
                self.options = options
                if preallocated_session_id is None:
                    preallocated_session_id = options.session_id
                type(self).attempts += 1
                self.attempt = type(self).attempts

            async def __aenter__(self):
                if self.attempt == 2:
                    raise RuntimeError("transient reconnect failure")
                return self

            async def __aexit__(self, *_args):
                return False

            async def query(self, prompt):
                if isinstance(prompt, str):
                    submitted: str | list[dict] = prompt
                else:
                    submitted = [item async for item in prompt]
                queries.append((self.options.resume, submitted))

            async def receive_response(self):
                session_id = self.options.resume or self.options.session_id
                assert session_id
                if self.attempt == 1:
                    yield SystemMessage("init", {"session_id": session_id})
                    yield AssistantMessage(
                        content=[],
                        model=service.retry.SYNTHETIC_MODEL,
                        error="server_error",
                    )
                    return
                yield ResultMessage(
                    subtype="success",
                    duration_ms=1,
                    duration_api_ms=1,
                    is_error=False,
                    num_turns=1,
                    session_id=session_id,
                    result="done",
                )

        async def fake_build_options(*_args, **_kwargs):
            return SimpleNamespace(cwd="/tmp", resume=None)

        audit_prompt = Mock()
        with (
            patch.object(service, "ClaudeSDKClient", FakeClient),
            patch.object(service, "build_agent_options", new=fake_build_options),
            patch.object(service, "_resolve_vision_model", return_value=None),
            patch.object(service, "_audit_skill_prompt", audit_prompt),
            patch.object(service, "_audit_run_completed"),
            patch.object(service, "_track_vision_session"),
            patch.object(service, "heal_orphan_tool_uses", return_value=0),
            patch.object(service.retry, "strip_synthetic_records", return_value=0),
            patch.object(service.retry, "backoff", return_value=0),
            patch.object(service.retry, "MAX_ATTEMPTS", 3),
            patch.object(service.session_title, "spawn", return_value=None),
            patch.object(service.session_title, "settle", new=AsyncMock()),
            patch.object(service.session_meta, "record_recent_activity", new=AsyncMock()),
            patch.object(service.session_recap, "spawn"),
            patch.object(service.asyncio, "sleep", new=AsyncMock()),
        ):
            result = await service.agent_run("perform one side effect")

        self.assertIsNotNone(preallocated_session_id)
        self.assertEqual(
            queries,
            [
                (None, "perform one side effect"),
                (preallocated_session_id, []),
            ],
        )
        self.assertEqual(audit_prompt.call_count, 1)
        self.assertEqual(result["attempts"], 3)
        self.assertEqual(result["result"], "done")

    async def test_agent_run_events_resumes_after_completed_tool_without_resending_prompt(
        self,
    ) -> None:
        queries: list[tuple[str | None, str | list[dict]]] = []
        emitted: list[str] = []
        session_id = SESSION_ID
        pump_attempt = 0

        class FakeCoordinator:
            def __init__(self):
                self.event_queue = None
                self.owner_username = None
                self.session_id = None
                self.cancelled = False

            def cancel_all(self):
                self.cancelled = True

        coordinator = FakeCoordinator()

        class FakeClient:
            def __init__(self, options):
                self.options = options

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def query(self, prompt):
                if isinstance(prompt, str):
                    submitted: str | list[dict] = prompt
                else:
                    submitted = [item async for item in prompt]
                queries.append((self.options.resume, submitted))

        async def fake_build_options(*_args, **_kwargs):
            return SimpleNamespace(cwd="/tmp", resume=None)

        async def fake_pump(_client, output_queue, *_args, **_kwargs):
            nonlocal pump_attempt
            pump_attempt += 1
            if pump_attempt == 1:
                await output_queue.put({
                    "event": "system",
                    "data": {"subtype": "init", "data": {"session_id": session_id}},
                })
                await output_queue.put({
                    "event": "tool_use",
                    "data": {
                        "content": [{"type": "tool_use", "id": "tu_done", "name": "Write"}]
                    },
                })
                await output_queue.put({
                    "event": "tool_result",
                    "data": {
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": "tu_done",
                            "content": "done",
                        }]
                    },
                })
                await output_queue.put({
                    "_retry_signal": "synthetic",
                    "payload": {"code": "server_error", "message": "API Error"},
                })
            else:
                await output_queue.put({
                    "event": "result",
                    "data": {"session_id": session_id, "usage": {}},
                })
            await output_queue.put(None)

        async def emit(event: str, _data: dict):
            emitted.append(event)

        audit_prompt = Mock()
        with (
            patch.object(service, "ClaudeSDKClient", FakeClient),
            patch.object(service, "build_agent_options", new=fake_build_options),
            patch.object(service, "_pump_stream_messages", new=fake_pump),
            patch.object(service, "_resolve_vision_model", return_value=None),
            patch.object(service, "_make_unified_can_use_tool", return_value=None),
            patch.object(service, "_audit_skill_prompt", audit_prompt),
            patch.object(service, "_audit_run_completed"),
            patch.object(service, "_track_vision_session"),
            patch.object(service, "heal_orphan_tool_uses", return_value=0),
            patch.object(service.retry, "strip_synthetic_records", return_value=0),
            patch.object(service.retry, "backoff", return_value=0),
            patch.object(service.retry, "MAX_ATTEMPTS", 2),
            patch.object(service.session_title, "spawn", return_value=None),
            patch.object(service.session_title, "settle", new=AsyncMock()),
            patch.object(service.session_meta, "record_recent_activity", new=AsyncMock()),
            patch.object(service.session_recap, "spawn"),
            patch.object(service.asyncio, "sleep", new=AsyncMock()),
        ):
            await service.agent_run_events(
                "perform one side effect",
                emit=emit,
                coordinator_out=[coordinator],
                new_session_id=session_id,
            )

        self.assertEqual(
            queries,
            [
                (None, "perform one side effect"),
                (session_id, []),
            ],
        )
        self.assertEqual(audit_prompt.call_count, 1)
        self.assertEqual(
            emitted,
            ["stream_init", "system", "tool_use", "tool_result", "retry_attempt", "result"],
        )
        self.assertTrue(coordinator.cancelled)


if __name__ == "__main__":
    unittest.main()
