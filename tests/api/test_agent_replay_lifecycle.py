from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from priva_agent_runner.routers import agent as agent_router


class AgentReplayLifecycleTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
