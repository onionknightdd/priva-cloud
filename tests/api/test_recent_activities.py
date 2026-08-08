from __future__ import annotations

import os
import tempfile
import unittest

from priva_agent_runner.routers.user_data import _recent_activities_with_recaps
from priva_agent_runner.services.claude_sdk import session_meta


class RecentActivityMetadataTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_overview_activity_uses_persisted_recap(self) -> None:
        await session_meta.record_recent_activity("/workspace/demo", "session-1")
        await session_meta.set_recap("session-1", "Implemented the chat layout", 6)

        activities = _recent_activities_with_recaps(session_meta.read_meta())

        self.assertEqual(
            activities,
            [{
                "session_id": "session-1",
                "cwd": "/workspace/demo",
                "recap": "Implemented the chat layout",
            }],
        )

    async def test_dismiss_only_removes_recent_activity(self) -> None:
        await session_meta.record_recent_activity("/workspace/demo", "session-1")
        await session_meta.set_recap("session-1", "Kept after dismissal", 4)

        await session_meta.dismiss_recent_activity("session-1")

        self.assertEqual(session_meta.get_recent_activities(), [])
        self.assertEqual(
            session_meta.get_recap("session-1"),
            {"text": "Kept after dismissal", "turns": 4},
        )

    async def test_recap_storage_is_limited_to_200_characters(self) -> None:
        await session_meta.set_recap("session-1", "x" * 240, 2)

        recap = session_meta.get_recap("session-1")

        self.assertEqual(len(recap["text"]), 200)
