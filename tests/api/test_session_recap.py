from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from priva_agent_runner.services.claude_sdk import session_recap


class _FakeResponse:
    status_code = 200
    text = ""

    @staticmethod
    def json() -> dict:
        return {"content": [{"type": "text", "text": "A concise recap"}]}


class _FakeClient:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.request_json = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, *, headers, json):
        self.request_json = json
        return _FakeResponse()


class SessionRecapModelTests(unittest.IsolatedAsyncioTestCase):
    def test_prompt_uses_latest_user_input_language(self) -> None:
        self.assertIn("Choose the recap language from user input only", session_recap._SYSTEM_PROMPT)
        self.assertIn("latest substantive user message", session_recap._SYSTEM_PROMPT)
        self.assertIn("Do not infer the language from assistant messages", session_recap._SYSTEM_PROMPT)

    async def test_uses_configured_haiku_model(self) -> None:
        profile = SimpleNamespace(
            base_url="https://example.test",
            auth_token="token",
            haiku_model="claude-haiku",
            default_model="claude-default",
        )
        client = _FakeClient()

        with (
            patch.object(session_recap.store, "default", return_value=profile),
            patch.object(session_recap.httpx, "AsyncClient", return_value=client),
        ):
            result = await session_recap._ask_model("user: hello")

        self.assertEqual(result, "A concise recap")
        self.assertEqual(client.request_json["model"], "claude-haiku")
        self.assertEqual(client.request_json["max_tokens"], 256)

    async def test_does_not_fall_back_to_default_model(self) -> None:
        profile = SimpleNamespace(
            base_url="https://example.test",
            auth_token="token",
            haiku_model=None,
            default_model="claude-default",
        )

        with (
            patch.object(session_recap.store, "default", return_value=profile),
            patch.object(session_recap.httpx, "AsyncClient") as client_factory,
        ):
            result = await session_recap._ask_model("user: hello")

        self.assertEqual(result, "")
        client_factory.assert_not_called()
