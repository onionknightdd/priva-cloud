from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from priva_agent_runner.routers import agent as agent_router
from priva_agent_runner.services.claude_sdk import agent_communication_log


class AgentReplayLifecycleTests(unittest.TestCase):
    def test_records_only_true_stream_deliveries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sidecar = Path(tmp) / "session" / "agent-communications.jsonl"
            with patch.object(
                agent_communication_log,
                "_sidecar_path",
                return_value=sidecar,
            ):
                ignored = agent_communication_log.record_stream_delivery(
                    "/workspace",
                    "session",
                    "tool_result",
                    {
                        "uuid": "ordinary-result",
                        "parent_tool_use_id": "call-agent-a",
                        "content": [{"type": "tool_result", "content": "ok"}],
                    },
                    received_at_ms=1786240803000,
                )
                recorded = agent_communication_log.record_stream_delivery(
                    "/workspace",
                    "session",
                    "tool_result",
                    {
                        "uuid": "peer-delivery",
                        "parent_tool_use_id": "call-agent-a",
                        "content": (
                            "SDK preface<agent-message from=\"general-purpose\">"
                            "hello from B</agent-message>policy suffix"
                        ),
                    },
                    received_at_ms=1786240803125,
                )
                deliveries = agent_communication_log.read_stream_deliveries(
                    "/workspace",
                    "session",
                )

        self.assertIsNone(ignored)
        self.assertEqual(recorded["received_at"], 1786240803125)
        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0]["event_id"], "peer-delivery")
        self.assertEqual(deliveries[0]["body"], "hello from B")

    def test_attaches_async_launch_and_latest_terminal_notification_to_owner(self) -> None:
        rows = [
            {
                "type": "assistant",
                "uuid": "assistant-1",
                "timestamp": "2026-08-09T01:00:00.000Z",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call-agent",
                            "name": "Agent",
                            "input": {"description": "Sleep worker"},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "uuid": "result-1",
                "timestamp": "2026-08-09T01:00:01.000Z",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call-agent",
                            "content": "Async agent launched successfully.",
                        }
                    ]
                },
                "toolUseResult": {
                    "status": "async_launched",
                    "isAsync": True,
                    "agentId": "agent-1",
                },
            },
            {
                "type": "attachment",
                "timestamp": "2026-08-09T01:00:02.000Z",
                "attachment": {
                    "prompt": (
                        "<task-notification>"
                        "<task-id>agent-1</task-id>"
                        "<tool-use-id>call-agent</tool-use-id>"
                        "<status>killed</status>"
                        "<summary>Stopped by Claude</summary>"
                        "</task-notification>"
                    )
                },
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "session.jsonl"
            transcript.write_text(
                "".join(f"{json.dumps(row)}\n" for row in rows),
                encoding="utf-8",
            )
            with patch.object(agent_router, "_session_jsonl_path", return_value=transcript):
                metadata = agent_router._build_message_replay_metadata("/workspace", "session")

        owner = metadata["assistant-1"]
        self.assertEqual(
            owner["agent_tool_results"]["call-agent"]["status"],
            "async_launched",
        )
        self.assertEqual(
            owner["agent_task_notifications"]["call-agent"],
            {
                "taskId": "agent-1",
                "toolUseId": "call-agent",
                "status": "killed",
                "summary": "Stopped by Claude",
                "outputFile": None,
                "timestamp": "2026-08-09T01:00:02.000Z",
            },
        )

    def test_hydrates_nested_agent_peer_delivery_with_structured_origin(self) -> None:
        main_rows = [
            {
                "type": "assistant",
                "uuid": "main-agent-use",
                "message": {"content": [{
                    "type": "tool_use",
                    "id": "call-agent-a",
                    "name": "Agent",
                    "input": {"description": "Agent A"},
                }]},
            },
            {
                "type": "user",
                "uuid": "main-agent-result",
                "message": {"content": [{
                    "type": "tool_result",
                    "tool_use_id": "call-agent-a",
                    "content": "agentId: agent-a",
                }]},
                "toolUseResult": {"agentId": "agent-a", "status": "async_launched"},
            },
        ]
        agent_a_rows = [
            {
                "type": "assistant",
                "uuid": "nested-agent-use",
                "message": {"content": [{
                    "type": "tool_use",
                    "id": "call-agent-b",
                    "name": "Agent",
                    "input": {"description": "Agent B"},
                }]},
            },
            {
                "type": "user",
                "uuid": "nested-agent-result",
                "message": {"content": [{
                    "type": "tool_result",
                    "tool_use_id": "call-agent-b",
                    "content": "agentId: agent-b",
                }]},
                "toolUseResult": {"agentId": "agent-b", "status": "async_launched"},
            },
        ]
        agent_b_rows = [{
            "type": "user",
            "uuid": "peer-delivery",
            "timestamp": "2026-08-09T01:00:03.000Z",
            "isMeta": True,
            "origin": {
                "kind": "peer",
                "senderTaskId": "agent-a",
                "name": "general-purpose",
                "body": "hello from A",
            },
            "message": {
                "content": (
                    "SDK preface<agent-message from=\"general-purpose\">"
                    "hello from A</agent-message>policy suffix"
                )
            },
        }]

        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "session.jsonl"
            subagents = Path(tmp) / "session" / "subagents"
            subagents.mkdir(parents=True)
            transcript.write_text(
                "".join(f"{json.dumps(row)}\n" for row in main_rows),
                encoding="utf-8",
            )
            (subagents / "agent-agent-a.jsonl").write_text(
                "".join(f"{json.dumps(row)}\n" for row in agent_a_rows),
                encoding="utf-8",
            )
            (subagents / "agent-agent-b.jsonl").write_text(
                "".join(f"{json.dumps(row)}\n" for row in agent_b_rows),
                encoding="utf-8",
            )

            with patch.object(agent_router, "_session_jsonl_path", return_value=transcript):
                parent_map = agent_router._build_subagent_parent_map("/workspace", "session")
                hydrated = agent_router._load_subagent_session_messages("/workspace", "session")

        self.assertEqual(parent_map, {
            "agent-a": "call-agent-a",
            "agent-b": "call-agent-b",
        })
        peer = next(message for message in hydrated if message.uuid == "peer-delivery")
        self.assertEqual(peer.parent_tool_use_id, "call-agent-b")
        self.assertEqual(peer.metadata["agent_message"], {
            "direction": "received",
            "body": "hello from A",
            "sender_agent_id": "agent-a",
            "sender_name": "general-purpose",
            "recipient_agent_id": "agent-b",
        })

    def test_hydrates_coordinator_delivery_with_actual_receive_timestamp(self) -> None:
        main_rows = [
            {
                "type": "assistant",
                "uuid": "main-agent-use",
                "message": {"content": [{
                    "type": "tool_use",
                    "id": "call-agent-a",
                    "name": "Agent",
                    "input": {"description": "Agent A"},
                }]},
            },
            {
                "type": "user",
                "uuid": "main-agent-result",
                "message": {"content": [{
                    "type": "tool_result",
                    "tool_use_id": "call-agent-a",
                    "content": "agentId: agent-a",
                }]},
                "toolUseResult": {"agentId": "agent-a", "status": "async_launched"},
            },
        ]
        coordinator_body = "Send the peer a status update."
        agent_rows = [{
            "type": "user",
            "uuid": "main-delivery",
            "timestamp": "2026-08-09T01:00:03.125Z",
            "isMeta": True,
            "origin": {"kind": "coordinator"},
            "message": {
                "content": (
                    "The coordinator sent a message while you were working:\n"
                    f"{coordinator_body}\n\n"
                    "Address this before completing your current task."
                )
            },
        }]

        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "session.jsonl"
            subagents = Path(tmp) / "session" / "subagents"
            subagents.mkdir(parents=True)
            transcript.write_text(
                "".join(f"{json.dumps(row)}\n" for row in main_rows),
                encoding="utf-8",
            )
            (subagents / "agent-agent-a.jsonl").write_text(
                "".join(f"{json.dumps(row)}\n" for row in agent_rows),
                encoding="utf-8",
            )
            with patch.object(agent_router, "_session_jsonl_path", return_value=transcript):
                hydrated = agent_router._load_subagent_session_messages("/workspace", "session")

        delivery = next(message for message in hydrated if message.uuid == "main-delivery")
        self.assertEqual(delivery.metadata["timestamp"], "2026-08-09T01:00:03.125Z")
        self.assertEqual(delivery.metadata["agent_message"], {
            "direction": "received",
            "body": coordinator_body,
            "sender_agent_id": "main",
            "sender_name": "main",
            "recipient_agent_id": "agent-a",
        })

    def test_hydrates_sidecar_only_peer_delivery_and_deduplicates_event_ids(self) -> None:
        main_rows = [
            {
                "type": "assistant",
                "uuid": "main-agent-use",
                "message": {"content": [{
                    "type": "tool_use",
                    "id": "call-agent-a",
                    "name": "Agent",
                    "input": {"description": "Agent A"},
                }]},
            },
            {
                "type": "user",
                "uuid": "main-agent-result",
                "message": {"content": [{
                    "type": "tool_result",
                    "tool_use_id": "call-agent-a",
                    "content": "agentId: agent-a",
                }]},
                "toolUseResult": {"agentId": "agent-a", "status": "async_launched"},
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "session.jsonl"
            sidecar = Path(tmp) / "session" / "agent-communications.jsonl"
            transcript.write_text(
                "".join(f"{json.dumps(row)}\n" for row in main_rows),
                encoding="utf-8",
            )
            sidecar.parent.mkdir(parents=True)
            sidecar.write_text(
                "\n".join([
                    json.dumps({
                        "version": 1,
                        "event_id": "peer-delivery",
                        "parent_tool_use_id": "call-agent-a",
                        "source": "peer",
                        "body": "hello from B",
                        "sender_name": "general-purpose",
                        "sender_agent_id": None,
                        "received_at": 1786240803125,
                        "sequence": 1786240803125000,
                    }),
                    json.dumps({
                        "version": 1,
                        "event_id": "peer-delivery",
                        "parent_tool_use_id": "call-agent-a",
                        "source": "peer",
                        "body": "duplicate",
                        "sender_name": "general-purpose",
                        "sender_agent_id": None,
                        "received_at": 1786240803126,
                        "sequence": 1786240803126000,
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            with (
                patch.object(agent_router, "_session_jsonl_path", return_value=transcript),
                patch.object(agent_communication_log, "_sidecar_path", return_value=sidecar),
            ):
                hydrated = agent_router._load_subagent_session_messages("/workspace", "session")

        deliveries = [message for message in hydrated if message.uuid == "peer-delivery"]
        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0].metadata["timestamp"], 1786240803125)
        self.assertEqual(deliveries[0].metadata["agent_message"], {
            "direction": "received",
            "body": "hello from B",
            "sender_agent_id": None,
            "sender_name": "general-purpose",
            "recipient_agent_id": "agent-a",
            "sequence": 1786240803125000,
        })


if __name__ == "__main__":
    unittest.main()
