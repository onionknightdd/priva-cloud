"""Per-account BYOK credentials live in the claude CLI's native *user* settings
file — ``$CLAUDE_CONFIG_DIR/settings.json`` (``/workspace/.claude/settings.json``
on the agent-runner pod). The CLI reads that file's top-level ``env`` block and
applies it via ``Object.assign(process.env, …)`` on every invocation, so the
agent-runner is the single owner: it merge-writes the 6 ``ENV_KEYS`` here and the
CLI honors them at run time — no process-env injection, no data-spine, no
wake-time Secret (which is also why a cred change is picked up with no re-wake).

The ``env`` block is one key among many (``hooks``/``mcpServers``/``permissions``
…), so reads/writes touch ONLY the top-level ``env`` key and preserve everything
else. Writes are atomic under ``flock(LOCK_EX)`` spanning the read-modify-write
and the file is kept ``0600`` (the auth token lives in it).
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
from pathlib import Path

from .logging import get_app_logger

logger = get_app_logger(__name__)

_lock = threading.Lock()

ENV_KEYS = [
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
]


def settings_json_path() -> Path:
    """The claude CLI's *user* settings file: ``$CLAUDE_CONFIG_DIR/settings.json``.

    On the agent-runner pod the operator sets ``CLAUDE_CONFIG_DIR=/workspace/.claude``
    (per-account NFS subPath), so this resolves to the same file the CLI reads and
    the hooks builder writes. Falls back to ``~/.claude`` for local dev where the
    runner shares the host home.
    """
    base = os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"
    return Path(base).expanduser() / "settings.json"


def _read_settings(path: Path) -> dict:
    """Load the whole settings.json (shared lock); ``{}`` if absent/corrupt."""
    if not path.exists():
        return {}
    try:
        with open(path, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                data = json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read settings.json at {}: {}", path, e)
        return {}


def read_settings_env(path: Path | None = None) -> dict:
    """Return the top-level ``env`` block (the cred dict), or ``{}`` if unset."""
    data = _read_settings(path or settings_json_path())
    env = data.get("env")
    return dict(env) if isinstance(env, dict) else {}


def write_settings_env(creds: dict, path: Path | None = None) -> None:
    """Merge the provided ANTHROPIC_* creds into the ``env`` block of settings.json.

    Atomic read-modify-write: ``flock(LOCK_EX)`` is held across the read AND the
    write (opened ``O_RDWR``, NOT truncate-on-open) so a concurrent writer can't
    interleave. Only ``ENV_KEYS`` are touched — a provided non-None value is set,
    and any ENV_KEY never seen is seeded ``""``; every OTHER top-level key
    (``hooks``/``mcpServers``/…) is preserved. The file is kept ``0600``.
    """
    path = path or settings_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                raw = f.read()
                try:
                    data = json.loads(raw) if raw.strip() else {}
                except (ValueError, TypeError):
                    data = {}
                if not isinstance(data, dict):
                    data = {}
                current_env = data.get("env")
                if not isinstance(current_env, dict):
                    current_env = {}
                for key in ENV_KEYS:
                    if key in creds and creds[key] is not None:
                        current_env[key] = creds[key]
                    elif key not in current_env:
                        current_env[key] = ""
                data["env"] = current_env
                f.seek(0)
                f.truncate()
                json.dump(data, f, indent=2)
                f.write("\n")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    # Pre-existing files may carry looser perms; the token lives here, so enforce 0600.
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def has_settings_env(path: Path | None = None) -> bool:
    """True when both required creds (base_url + auth_token) are present."""
    env = read_settings_env(path)
    return bool(env.get("ANTHROPIC_BASE_URL")) and bool(env.get("ANTHROPIC_AUTH_TOKEN"))


def mask_token(token: str | None) -> str | None:
    if not token:
        return token
    if len(token) <= 8:
        return "****"
    return token[:3] + "****" + token[-4:]
