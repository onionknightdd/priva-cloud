"""Managed-hook wrapper (rev-5 D1): env scrub + fire-log + exit passthrough.

The wrapper runs under the CLI's system python around every managed
(admin-enforced) hook. It must: rebuild a scrubbed env (no platform secrets
reach the hook), forward stdin, propagate the hook's stdout + exit code
UNCHANGED (so exit-2 still blocks), and append one HookLogEntry-shaped JSONL
line to the account's daily hook-log file — without ever letting a logging
failure change the hook's outcome.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from priva_common import managed_hook_wrapper as W

WRAPPER = Path(W.__file__)


def _write_hook(tmp: Path, body: str, name: str = "hook.py") -> Path:
    p = tmp / name
    p.write_text(body)
    return p


def _run(hook: Path, payload: dict, env: dict, allowed: list[str] | None = None):
    argv = [sys.executable, str(WRAPPER), "test-hook", sys.executable, str(hook), *(allowed or [])]
    return subprocess.run(
        argv, input=json.dumps(payload), capture_output=True, text=True, env=env
    )


def test_allowlist_matches_env_module():
    """The wrapper's scrub constants must stay in lockstep with build_hook_env."""
    from priva_agent_runner.services.hooks import env as e

    assert W._BASE_ALLOWED == e._BASE_ALLOWED
    assert W._BASE_PREFIXES == e._BASE_PREFIXES
    # wrapper deny-list is a superset (adds TOKEN) of env.py's
    assert set(e._DENY_PREFIXES) <= set(W._DENY_PREFIXES)
    assert set(e._DENY_SUBSTRINGS) <= set(W._DENY_SUBSTRINGS)


def test_env_is_scrubbed(tmp_path):
    hook = _write_hook(tmp_path, "import os,json,sys; json.dump(dict(os.environ), sys.stdout)")
    dirty = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "CLAUDE_PROJECT_DIR": "/workspace/alice",
        "ANTHROPIC_AUTH_TOKEN": "sekret",
        "PRIVA_AUTH__JWT_SECRET": "jwt",
        "SOME_HMAC_KEY": "h",
        "MY_PASSWORD": "p",
        "ALLOWED_EXTRA": "ok",
    }
    r = _run(hook, {"hook_event_name": "PreToolUse"}, dirty, allowed=["ALLOWED_EXTRA"])
    assert r.returncode == 0
    seen = json.loads(r.stdout)
    assert seen.get("PATH") and seen.get("HOME") and seen.get("LANG") == "C.UTF-8"
    assert seen.get("LC_ALL") == "C.UTF-8"
    assert seen.get("CLAUDE_PROJECT_DIR") == "/workspace/alice"
    assert seen.get("ALLOWED_EXTRA") == "ok"
    # secrets never reach the hook
    for leaked in ("ANTHROPIC_AUTH_TOKEN", "PRIVA_AUTH__JWT_SECRET", "SOME_HMAC_KEY", "MY_PASSWORD"):
        assert leaked not in seen


def test_exit_code_and_streams_passthrough(tmp_path):
    hook = _write_hook(
        tmp_path,
        "import sys; sys.stdout.write('OUT'); sys.stderr.write('ERR'); sys.exit(2)",
    )
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    r = _run(hook, {"hook_event_name": "PreToolUse"}, env)
    assert r.returncode == 2  # exit-2 block reaches the CLI unchanged
    assert r.stdout == "OUT"
    assert "ERR" in r.stderr


def test_fire_is_logged(tmp_path):
    hook = _write_hook(tmp_path, "print('{}')")
    state_parent = tmp_path / "state"
    app_dir = state_parent / "priva"
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "PRIVA_HOME": str(state_parent),
    }
    r = _run(hook, {"hook_event_name": "PreToolUse", "tool_name": "Bash"}, env)
    assert r.returncode == 0
    logs = list(app_dir.glob(".priva.hooks.log.*.jsonl"))
    assert len(logs) == 1
    rec = json.loads(logs[0].read_text().strip())
    assert rec["hook_id"] == "test-hook"
    assert rec["handler_type"] == "managed"
    assert rec["event_type"] == "PreToolUse"
    assert rec["tool_name"] == "Bash"
    assert rec["exit_code"] == 0
    assert "id" in rec and "timestamp" in rec and "duration_ms" in rec


def test_logging_failure_never_breaks_hook(tmp_path):
    """An unwritable app-dir shape never changes the hook's outcome."""
    hook = _write_hook(tmp_path, "import sys; sys.stdout.write('OK'); sys.exit(0)")
    not_a_dir = tmp_path / "file"
    not_a_dir.write_text("x")
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "PRIVA_HOME": str(not_a_dir),
    }
    r = _run(hook, {"hook_event_name": "PreToolUse"}, env)
    assert r.returncode == 0
    assert r.stdout == "OK"


def test_malformed_args_are_non_blocking(tmp_path):
    r = subprocess.run(
        [sys.executable, str(WRAPPER), "only-id"],
        input="{}", capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert r.returncode == 0  # misconfig must not wedge the tool call
