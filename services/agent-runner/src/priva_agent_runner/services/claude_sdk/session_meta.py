"""Pin / archive metadata for sessions and workdirs (server-side, durable).

Pin and archive are organizing flags the SDK has no concept of, so we keep them
ourselves in a single **account-level** index next to the SDK's data:

    ~/.claude/priva_meta.json
    {
      "sessions": { "<session_id>": {"pinned": bool, "archived": bool} },
      "workdirs":  { "<canonical_cwd>": {"pinned": bool} },
      "recent_activities": [
        {"session_id": "<session_id>", "cwd": "<canonical_cwd>"}
      ],
      "scheduler_sessions": {
        "<session_id>": {"job_id": str, "job_name": str, "run_id": str}
      }
    }

``scheduler_sessions`` marks sessions a scheduled job opened (design D3): the
sessions list surfaces them as ``origin='scheduler'`` (sidebar ⏰) and the D15
boot prune uses the index to delete only scheduler-origin transcripts.

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
_RECENT_ACTIVITIES_LIMIT = 5

# Guards read-modify-write of the index. The pod is single-writer, but turns and
# list requests are concurrent coroutines, so serialize mutations.
_lock = asyncio.Lock()


def _index_path() -> Path:
    return _get_claude_config_home_dir() / _META_FILENAME


def _empty() -> dict:
    return {"sessions": {}, "workdirs": {}, "recent_activities": [], "scheduler_sessions": {}}


def _normalize_recent_activities(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    activities: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        session_id = item.get("session_id")
        cwd = item.get("cwd")
        session_id = session_id if isinstance(session_id, str) and session_id else None
        cwd = _canonicalize_path(cwd) if isinstance(cwd, str) and cwd else None
        if not session_id and not cwd:
            continue
        key = ("session", session_id) if session_id else ("cwd", cwd or "")
        if key in seen:
            continue
        seen.add(key)
        activities.append({"session_id": session_id, "cwd": cwd})
        if len(activities) >= _RECENT_ACTIVITIES_LIMIT:
            break
    return activities


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
    scheduler_sessions = data.get("scheduler_sessions")
    return {
        "sessions": sessions if isinstance(sessions, dict) else {},
        "workdirs": workdirs if isinstance(workdirs, dict) else {},
        "recent_activities": _normalize_recent_activities(data.get("recent_activities")),
        "scheduler_sessions": scheduler_sessions if isinstance(scheduler_sessions, dict) else {},
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


def get_recent_activities(meta: dict | None = None) -> list[dict]:
    data = meta if meta is not None else _read_raw()
    return _normalize_recent_activities(data.get("recent_activities"))


def get_scheduler_info(session_id: str, meta: dict | None = None) -> dict | None:
    """``{"job_id", "job_name", "run_id"}`` if the session was opened by a
    scheduled job (origin=scheduler), else None."""
    data = meta if meta is not None else _read_raw()
    entry = data.get("scheduler_sessions", {}).get(session_id)
    return entry if isinstance(entry, dict) else None


def list_scheduler_sessions(meta: dict | None = None) -> dict[str, dict]:
    """The whole scheduler-origin index — drives the D15 retention prune."""
    data = meta if meta is not None else _read_raw()
    out = data.get("scheduler_sessions", {})
    return {k: v for k, v in out.items() if isinstance(v, dict)}


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


async def record_recent_activity(cwd: str | None, session_id: str | None) -> list[dict]:
    session_id = session_id if isinstance(session_id, str) and session_id else None
    if not session_id:
        return get_recent_activities()
    canonical_cwd = _canonicalize_path(cwd) if cwd else None
    async with _lock:
        data = _read_raw()
        entry = {"session_id": session_id, "cwd": canonical_cwd}
        activities = [
            item for item in get_recent_activities(data)
            if item.get("session_id") != session_id
        ]
        data["recent_activities"] = [entry, *activities][:_RECENT_ACTIVITIES_LIMIT]
        _write_raw(data)
        return data["recent_activities"]


async def set_scheduler_session(
    session_id: str, *, job_id: str, job_name: str, run_id: str
) -> None:
    """Mark a session as scheduler-origin (written when the executor learns the
    CLI-assigned session id from the run's system.init event)."""
    if not session_id:
        return
    async with _lock:
        data = _read_raw()
        data["scheduler_sessions"][session_id] = {
            "job_id": job_id, "job_name": job_name, "run_id": run_id,
        }
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
        changed = data["sessions"].pop(session_id, None) is not None
        changed = (data["scheduler_sessions"].pop(session_id, None) is not None) or changed
        recent_activities = [
            item for item in get_recent_activities(data)
            if item.get("session_id") != session_id
        ]
        if len(recent_activities) != len(data.get("recent_activities", [])):
            data["recent_activities"] = recent_activities
            changed = True
        if changed:
            _write_raw(data)
