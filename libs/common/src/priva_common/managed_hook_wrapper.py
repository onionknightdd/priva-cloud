#!/usr/bin/env python3
"""Managed-hook execution wrapper (stdlib-only; runs under the CLI's system python).

Claude Code executes managed (admin-enforced) hooks natively from
``/etc/claude-code/managed-settings.json``. Native execution would inherit the
CLI's full environment (which on Priva pods carries JWT/HMAC/BYOK secrets via
``envFrom``) and would be invisible to the runner's ``/hooks/logs`` + Prometheus.
This wrapper closes both gaps in one process, so the rendered command is:

    python3 <this> <hook_id> <interpreter> <script> [ALLOWED_ENV_VAR ...]

It (1) rebuilds a SCRUBBED environment (the same allowlist/deny-list as
``priva_common.hooks env.build_hook_env`` — kept in sync by test), (2) runs the
real hook with the forwarded stdin payload, (3) appends one HookLogEntry-shaped
JSONL line to the account's hook-log file, and (4) propagates the hook's stdout
and exit code UNCHANGED so exit-2 blocking still reaches the CLI.

CRITICAL: stdlib only, no ``priva_common`` import — the CLI may run a python that
cannot see the runner venv. Any failure in logging/scrub must NOT change the
hook's own outcome (fail-open around the observability, fail-closed only on the
hook's real exit code).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid

# Kept identical to priva_common.hooks.env (asserted by test_managed_hook_wrapper).
_BASE_ALLOWED = ("PATH", "HOME", "LANG", "TMPDIR", "TZ", "TERM")
_BASE_PREFIXES = ("LC_",)
_DENY_PREFIXES = ("ANTHROPIC_",)
_DENY_SUBSTRINGS = ("JWT", "HMAC", "DSN", "SECRET", "PASSWORD", "TOKEN")

# Context vars the runner/CLI provides that hooks legitimately need.
_CONTEXT_PASSTHROUGH = (
    "CLAUDE_PROJECT_DIR",
    "CLAUDE_HOOK_EVENT_NAME",
    "PRIVA_HOOK_DIR",
    "PRIVA_AUDIT_DIR",
    "PRIVA_LOG_DIR",
)

_DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin"


def is_denied_env(name: str) -> bool:
    upper = name.upper()
    if any(upper.startswith(p) for p in _DENY_PREFIXES):
        return True
    return any(s in upper for s in _DENY_SUBSTRINGS)


def build_scrubbed_env(allowed_env_vars: list[str]) -> dict[str, str]:
    """Reconstruct the hook's env from the wrapper's inherited env.

    Base allowlist + LC_* + context passthrough + per-policy allowed_env_vars,
    minus anything on the deny-list. Never a blanket copy of os.environ.
    """
    src = os.environ
    env: dict[str, str] = {}
    for key in _BASE_ALLOWED:
        if key in src:
            env[key] = src[key]
    for key, val in src.items():
        if any(key.startswith(p) for p in _BASE_PREFIXES) and not is_denied_env(key):
            env[key] = val
    for key in _CONTEXT_PASSTHROUGH:
        if key in src and not is_denied_env(key):
            env[key] = src[key]
    for key in allowed_env_vars:
        if key in src and not is_denied_env(key):
            env[key] = src[key]
    env.setdefault("PATH", _DEFAULT_PATH)
    return env


def _log_path() -> str | None:
    """Per-account daily hook-log file, matching services/hooks/log_store.py.

    ``{work_dir}/{username}/.priva.hooks.log.{YYYY-MM-DD}.jsonl``. Resolved from
    the wrapper's own (unscrubbed) env, which still has WORKSPACE_DIR/USERNAME.
    Returns None when either is missing (logging is then skipped, not fatal).
    """
    work_dir = os.environ.get("WORKSPACE_DIR") or os.environ.get("PRIVA_SERVER__WORK_DIR")
    username = os.environ.get("USERNAME")
    if not work_dir or not username:
        return None
    day = time.strftime("%Y-%m-%d", time.gmtime())
    return os.path.join(work_dir, username, f".priva.hooks.log.{day}.jsonl")


def _append_log(hook_id: str, payload: dict, exit_code: int, duration_ms: int, error: str) -> None:
    path = _log_path()
    if not path:
        return
    entry = {
        "id": uuid.uuid4().hex,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z",
        "event_type": str(payload.get("hook_event_name", "")),
        "matcher": "",
        "handler_type": "managed",
        "hook_id": hook_id,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "tool_name": str((payload.get("tool_input") or {}).get("name", "") or payload.get("tool_name", "")),
        "error": error[:500],
    }
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # observability must never break the hook


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        sys.stderr.write("managed_hook_wrapper: usage: <hook_id> <interpreter> <script> [ENV ...]\n")
        return 0  # misconfig is non-blocking, never wedge the tool call
    hook_id, interpreter, script = argv[1], argv[2], argv[3]
    allowed_env_vars = argv[4:]

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {}

    env = build_scrubbed_env(allowed_env_vars)
    started = time.monotonic()
    error = ""
    try:
        proc = subprocess.run(
            [interpreter, script],
            input=raw,
            capture_output=True,
            text=True,
            env=env,
        )
        rc, out, err = proc.returncode, proc.stdout, proc.stderr
    except Exception as exc:  # spawn failure: non-blocking, logged
        rc, out, err = 1, "", ""
        error = f"{type(exc).__name__}: {exc}"

    duration_ms = int((time.monotonic() - started) * 1000)
    _append_log(hook_id, payload, rc, duration_ms, error or err)

    # Propagate the hook's own output + exit code UNCHANGED (exit 2 = block).
    if out:
        sys.stdout.write(out)
    if err:
        sys.stderr.write(err)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
