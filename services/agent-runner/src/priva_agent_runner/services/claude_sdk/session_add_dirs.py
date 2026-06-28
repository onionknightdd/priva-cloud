"""Per-session ``add_dirs`` persistence (server-side, recover-on-resume).

The Claude Agent SDK does NOT record ``add_dirs`` in the transcript JSONL, so a
resume can't recover the extra directories a session was granted. We persist them
ourselves in a small sidecar file next to the transcript:

    ~/.claude/projects/<sanitized-cwd>/<session_id>.add_dirs.json   ->  {"add_dirs": [...]}

A **sidecar** (not an in-transcript metadata line) is deliberate: the agent CLI
subprocess is concurrently appending to ``<session_id>.jsonl`` while a turn runs,
so writing our own file avoids racing the subprocess and avoids feeding the CLI an
unknown transcript line type. Reads/writes are independent of any running stream.

The project directory is derived from the session's cwd exactly the way the SDK
derives the transcript path (canonicalize -> sanitize), so the sidecar always lands
beside the right transcript even when cwd is user-configured.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from claude_agent_sdk._internal.sessions import _canonicalize_path, _get_project_dir

from priva_common.logging import get_app_logger

logger = get_app_logger(__name__)


def _sidecar_path(cwd: str, session_id: str) -> Path:
    project_dir = _get_project_dir(_canonicalize_path(cwd))
    return project_dir / f"{session_id}.add_dirs.json"


def read_add_dirs(cwd: str, session_id: str | None) -> list[str]:
    """Return the session's stored add_dirs, or [] if none/unreadable."""
    if not session_id:
        return []
    path = _sidecar_path(cwd, session_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Malformed add_dirs sidecar at %s", path)
        return []
    dirs = data.get("add_dirs") if isinstance(data, dict) else None
    if isinstance(dirs, list):
        return [d for d in dirs if isinstance(d, str)]
    return []


def write_add_dirs(cwd: str, session_id: str, dirs: list[str]) -> None:
    """Persist add_dirs for a session (atomic temp-file + os.replace)."""
    path = _sidecar_path(cwd, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".json.{os.getpid()}.tmp")
    payload = json.dumps({"add_dirs": list(dirs)}, separators=(",", ":"))
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def delete_add_dirs(cwd: str, session_id: str) -> None:
    """Remove a session's add_dirs sidecar (best-effort; ignores missing)."""
    try:
        _sidecar_path(cwd, session_id).unlink()
    except OSError:
        pass
