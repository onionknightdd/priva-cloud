"""Pin / archive metadata for sessions and workdirs (server-side, durable).

Pin and archive are organizing flags the SDK has no concept of, so we keep them
ourselves in a single **account-level** index next to the SDK's data:

    ~/.claude/priva_meta.json
    {
      "sessions": { "<session_id>": {"pinned": bool, "archived": bool} },
      "workdirs":  { "<canonical_cwd>": {"pinned": bool} }
    }

One file (not per-session sidecars like ``session_add_dirs``) because the runner
pod is per-account / single-writer: the sessions list reads the whole index once
per request, and the Settings → Archived panel can enumerate every archived
session without walking each project dir. Read-modify-write goes through a
module-level lock; writes are atomic (temp file + ``os.replace``).

Pin/archive are deliberately separate from the SDK ``tag`` (a single string that
can't hold pinned + archived + a user tag at once); keeping them out of ``tag``
is also what excludes them from the tag-filter chips.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from claude_agent_sdk._internal.sessions import (
    _canonicalize_path,
    _get_claude_config_home_dir,
)

from priva_common.logging import get_app_logger

logger = get_app_logger(__name__)

_META_FILENAME = "priva_meta.json"

# Guards read-modify-write of the index. The pod is single-writer, but turns and
# list requests are concurrent coroutines, so serialize mutations.
_lock = asyncio.Lock()


def _index_path() -> Path:
    return _get_claude_config_home_dir() / _META_FILENAME


def _empty() -> dict:
    return {"sessions": {}, "workdirs": {}}


def _read_raw() -> dict:
    """Read and normalize the index; returns an empty shape if missing/corrupt."""
    path = _index_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return _empty()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Malformed metadata index at %s", path)
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    sessions = data.get("sessions")
    workdirs = data.get("workdirs")
    return {
        "sessions": sessions if isinstance(sessions, dict) else {},
        "workdirs": workdirs if isinstance(workdirs, dict) else {},
    }


def _write_raw(data: dict) -> None:
    path = _index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".json.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


# --- Reads (lock-free; a single read of the file is consistent enough) --------


def read_meta() -> dict:
    """The full normalized index — read once per sessions-list request."""
    return _read_raw()


def get_session_flags(session_id: str, meta: dict | None = None) -> dict:
    """``{"pinned": bool, "archived": bool}`` for a session (defaults False)."""
    data = meta if meta is not None else _read_raw()
    entry = data.get("sessions", {}).get(session_id)
    if not isinstance(entry, dict):
        return {"pinned": False, "archived": False}
    return {
        "pinned": bool(entry.get("pinned", False)),
        "archived": bool(entry.get("archived", False)),
    }


def get_workdir_pinned(cwd: str, meta: dict | None = None) -> bool:
    data = meta if meta is not None else _read_raw()
    entry = data.get("workdirs", {}).get(_canonicalize_path(cwd))
    return bool(entry.get("pinned", False)) if isinstance(entry, dict) else False


# --- Writes (serialized read-modify-write) ------------------------------------


async def set_session_flags(
    session_id: str, *, pinned: bool | None = None, archived: bool | None = None
) -> dict:
    """Set a session's pinned/archived flags (only the given ones change)."""
    async with _lock:
        data = _read_raw()
        entry = data["sessions"].get(session_id)
        if not isinstance(entry, dict):
            entry = {"pinned": False, "archived": False}
        if pinned is not None:
            entry["pinned"] = bool(pinned)
        if archived is not None:
            entry["archived"] = bool(archived)
        # Drop the entry entirely once it carries no active flags — keeps the
        # index from accumulating all-False rows.
        if not entry.get("pinned") and not entry.get("archived"):
            data["sessions"].pop(session_id, None)
        else:
            data["sessions"][session_id] = entry
        _write_raw(data)
        return {
            "pinned": bool(entry.get("pinned", False)),
            "archived": bool(entry.get("archived", False)),
        }


async def set_workdir_pinned(cwd: str, pinned: bool) -> None:
    key = _canonicalize_path(cwd)
    async with _lock:
        data = _read_raw()
        if pinned:
            data["workdirs"][key] = {"pinned": True}
        else:
            data["workdirs"].pop(key, None)
        _write_raw(data)


async def archive_workdir(session_ids: list[str]) -> None:
    """Cascade: mark every given session archived in one write."""
    async with _lock:
        data = _read_raw()
        for sid in session_ids:
            entry = data["sessions"].get(sid)
            if not isinstance(entry, dict):
                entry = {"pinned": False, "archived": False}
            entry["archived"] = True
            data["sessions"][sid] = entry
        _write_raw(data)


async def prune_session(session_id: str) -> None:
    """Drop a deleted session's entry so the index doesn't accumulate dead ids."""
    async with _lock:
        data = _read_raw()
        if data["sessions"].pop(session_id, None) is not None:
            _write_raw(data)
