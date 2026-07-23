"""Lazy resume guard — a session deleted in the web UI while a Feishu chat is
still bound to it must not wedge the chat (feat_feishu_DM.md, user ruling
2026-07-23: run optimistically, catch, check, warn ⚠️, rerun fresh; any other
failure surfaces unchanged).

The full attempt loop needs a live SDK; these tests pin the two pure halves:
the missing-target check (runner) and the session_reset fold (connector card).
"""

from __future__ import annotations

import json
import os
import sys

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_CONN_SRC = os.path.join(_REPO, "services", "channel-connector", "src")
if _CONN_SRC not in sys.path:
    sys.path.insert(0, _CONN_SRC)

from priva_agent_runner.services.claude_sdk import service as sdk_service  # noqa: E402
from priva_channel_connector.sse import reduce_sse  # noqa: E402


# --- runner: _resume_target_missing ------------------------------------------
def test_resume_target_missing_only_when_provably_gone(monkeypatch):
    calls = []

    def fake_info(session_id, directory=None):
        calls.append(session_id)
        return {"gone": None, "alive": object(), "boom": None}[session_id] \
            if session_id != "boom" else (_ for _ in ()).throw(RuntimeError("lookup failed"))

    monkeypatch.setattr(sdk_service, "get_session_info", fake_info)
    assert sdk_service._resume_target_missing("gone") is True       # provably deleted
    assert sdk_service._resume_target_missing("alive") is False     # exists → genuine error path
    assert sdk_service._resume_target_missing("boom") is False      # can't prove → don't swallow
    assert sdk_service._resume_target_missing(None) is False        # no resume requested
    assert sdk_service._resume_target_missing("") is False
    assert calls == ["gone", "alive", "boom"]


def test_resume_lost_error_carries_payload():
    e = sdk_service._ResumeTargetLostError({"code": "ProcessError", "message": "exit 1"})
    assert e.payload["code"] == "ProcessError" and "exit 1" in str(e)


# --- connector: session_reset folds into the card/reply ----------------------
def test_session_reset_event_warns_then_fresh_reply_flows():
    frames = [
        ("session_reset", json.dumps({
            "old_session_id": "dead-sess",
            "message": sdk_service._SESSION_RESET_NOTE,
            "code": "ProcessError",
        })),
        ("assistant", '{"content": [{"type": "text", "text": "新会话的回复"}]}'),
        ("result", '{"type": "result", "session_id": "fresh-sess", "is_error": false}'),
    ]
    out = reduce_sse(frames)
    assert out.is_error is False
    assert out.session_id == "fresh-sess"      # worker commit_session rebinds the chat
    assert out.text.startswith("⚠️ ")           # warning line first…
    assert "原会话已不存在" in out.text
    assert "新会话的回复" in out.text            # …then the fresh run's reply


def test_session_reset_event_default_note():
    out = reduce_sse([("session_reset", "{}")])
    assert out.text.startswith("⚠️") and "已自动开启新会话" in out.text
