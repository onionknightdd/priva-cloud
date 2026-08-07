"""Pin / archive / recap metadata for sessions and workdirs (server-side, durable).

Pin, archive and recap are things the SDK has no concept of, so we keep them
ourselves in a single **account-level** index next to the SDK's data:

    ~/.claude/priva_meta.json
    {
      "sessions": {
        "<session_id>": {"pinned": bool, "archived": bool, "tags": [str, ...]}
      },
      "workdirs":  { "<canonical_cwd>": {"pinned": bool} },
      "recent_activities": [
        {"session_id": "<session_id>", "cwd": "<canonical_cwd>"}
      ],
      "scheduler_sessions": {
        "<session_id>": {"job_id": str, "job_name": str, "run_id": str}
      },
      "recaps":   { "<session_id>": {"text": str, "turns": int} },
      "last_response_models": {
        "<session_id>": {
          "profile_id": str | None, "model_id": str, "observed_at": int
        }
      },
      "tag_colors": { "<tag>": 0..99 }
    }

``scheduler_sessions`` marks sessions a scheduled job opened (design D3): the
sessions list surfaces them as ``origin='scheduler'`` (sidebar ⏰) and the D15
boot prune uses the index to delete only scheduler-origin transcripts.

``recaps`` is deliberately a **top-level** key rather than a field on the
``sessions`` entry: an entry is dropped once it carries neither a flag nor a
tag, so a recap parked in there could be deleted when a user clears metadata.
The recap *toggle* is not here at all — it is user config, so it lives in
``.priva.user.yml`` beside ``vision_model``; this file stays a pure per-session
index.

One file (not per-session sidecars like ``session_add_dirs``) because the runner
pod is per-account / single-writer: the sessions list reads the whole index once
per request, and the Settings → Archived panel can enumerate every archived
session without walking each project dir. Read-modify-write goes through a
module-level lock; writes are atomic (temp file + ``os.replace``).

The SDK ``tag`` is a single string, so Priva stores the canonical user-facing
``tags`` list here (up to three) while mirroring its first value back to the SDK
for older clients. ``tag_colors`` reserves one of 100 stable color slots for a
tag name; assignments survive refreshes and session deletion.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from claude_agent_sdk._internal.sessions import (
    _canonicalize_path,
    _get_claude_config_home_dir,
)

from priva_common.logging import get_app_logger

logger = get_app_logger(__name__)

_META_FILENAME = "priva_meta.json"
_RECENT_ACTIVITIES_LIMIT = 5
_MAX_SESSION_TAGS = 3
_TAG_COLOR_SLOTS = 100
# A recap is one sentence; anything longer is a model that ignored the prompt.
_RECAP_MAX_CHARS = 120

# Guards read-modify-write of the index. The pod is single-writer, but turns and
# list requests are concurrent coroutines, so serialize mutations.
_lock = asyncio.Lock()


def _index_path() -> Path:
    return _get_claude_config_home_dir() / _META_FILENAME


def _empty() -> dict:
    return {
        "sessions": {},
        "workdirs": {},
        "recent_activities": [],
        "scheduler_sessions": {},
        "recaps": {},
        "last_response_models": {},
        "tag_colors": {},
    }


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
        logger.warning("Malformed metadata index at {}", path)
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    sessions = data.get("sessions")
    workdirs = data.get("workdirs")
    scheduler_sessions = data.get("scheduler_sessions")
    recaps = data.get("recaps")
    last_response_models = data.get("last_response_models")
    tag_colors = data.get("tag_colors")
    return {
        "sessions": sessions if isinstance(sessions, dict) else {},
        "workdirs": workdirs if isinstance(workdirs, dict) else {},
        "recent_activities": _normalize_recent_activities(data.get("recent_activities")),
        "scheduler_sessions": scheduler_sessions if isinstance(scheduler_sessions, dict) else {},
        "recaps": recaps if isinstance(recaps, dict) else {},
        "last_response_models": (
            last_response_models if isinstance(last_response_models, dict) else {}
        ),
        "tag_colors": tag_colors if isinstance(tag_colors, dict) else {},
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


def _normalize_tags(raw: object, *, truncate: bool) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    tags: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        tag = item.strip()
        folded = tag.casefold()
        if not tag or folded in seen:
            continue
        if len(tags) >= _MAX_SESSION_TAGS:
            if truncate:
                break
            raise ValueError(f"Maximum {_MAX_SESSION_TAGS} tags per session")
        seen.add(folded)
        tags.append(tag)
    return tags


def normalize_session_tags(raw: object) -> list[str]:
    """Validate/normalize an API tag list while preserving user-entered case."""
    return _normalize_tags(raw, truncate=False)


def get_session_tags(
    session_id: str, meta: dict | None = None, *, fallback: str | None = None
) -> list[str]:
    """Canonical tags for a session, falling back to the SDK's legacy tag."""
    data = meta if meta is not None else _read_raw()
    entry = data.get("sessions", {}).get(session_id)
    if isinstance(entry, dict) and "tags" in entry:
        return _normalize_tags(entry.get("tags"), truncate=True)
    return _normalize_tags(fallback, truncate=True)


def fallback_tag_color_index(tag: str) -> int:
    """Stable color slot for legacy tags that predate the persisted registry."""
    # FNV-1a is mirrored by the web client for old backends/responses that do
    # not yet include ``tag_colors``.
    value = 2166136261
    for byte in tag.lower().encode("utf-8"):
        value ^= byte
        value = (value * 16777619) & 0xFFFFFFFF
    return value % _TAG_COLOR_SLOTS


def _registered_tag_color(registry: dict, tag: str) -> int | None:
    """Find a valid slot by exact name first, then case-insensitively."""
    direct = registry.get(tag)
    if isinstance(direct, int) and 0 <= direct < _TAG_COLOR_SLOTS:
        return direct
    folded = tag.casefold()
    for candidate, index in registry.items():
        if (
            isinstance(candidate, str)
            and candidate.casefold() == folded
            and isinstance(index, int)
            and 0 <= index < _TAG_COLOR_SLOTS
        ):
            return index
    return None


def _reserve_tag_colors(data: dict, tags: object) -> bool:
    """Reserve unused slots for tag names, returning whether data changed."""
    values = [tags] if isinstance(tags, str) else tags
    if not isinstance(values, (list, tuple, set)):
        return False
    registry = data.get("tag_colors")
    if not isinstance(registry, dict):
        registry = {}
        data["tag_colors"] = registry
    used = {
        value
        for value in registry.values()
        if isinstance(value, int) and 0 <= value < _TAG_COLOR_SLOTS
    }
    changed = False
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        tag = value.strip()
        folded = tag.casefold()
        if not tag or folded in seen:
            continue
        seen.add(folded)
        if _registered_tag_color(registry, tag) is not None:
            continue
        start = fallback_tag_color_index(tag)
        slot = next(
            (
                (start + offset) % _TAG_COLOR_SLOTS
                for offset in range(_TAG_COLOR_SLOTS)
                if (start + offset) % _TAG_COLOR_SLOTS not in used
            ),
            start,
        )
        registry[tag] = slot
        used.add(slot)
        changed = True
    return changed


def get_tag_colors(tags: object, meta: dict | None = None) -> dict[str, int]:
    """Return each tag's persisted 0..99 color slot (stable-hash fallback)."""
    data = meta if meta is not None else _read_raw()
    registry = data.get("tag_colors", {})
    registry = registry if isinstance(registry, dict) else {}
    out: dict[str, int] = {}
    for tag in _normalize_tags(tags, truncate=True):
        index = _registered_tag_color(registry, tag)
        out[tag] = index if index is not None else fallback_tag_color_index(tag)
    return out


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


def get_recap(session_id: str, meta: dict | None = None) -> dict | None:
    """``{"text": str, "turns": int}`` for a session, or None if never recapped.

    ``turns`` is the transcript message count the text was derived from — the
    client uses it to tell a refreshed recap from the one it already has.
    """
    data = meta if meta is not None else _read_raw()
    entry = data.get("recaps", {}).get(session_id)
    if not isinstance(entry, dict):
        return None
    text = entry.get("text")
    if not isinstance(text, str) or not text:
        return None
    turns = entry.get("turns")
    return {"text": text, "turns": turns if isinstance(turns, int) else 0}


def get_last_response_model(session_id: str, meta: dict | None = None) -> dict | None:
    """Return the latest observed response model for a session, if known.

    This is historical metadata and must not be used as the next run's
    profile/model selection: the profile may have been deleted or changed.
    """
    data = meta if meta is not None else _read_raw()
    entry = data.get("last_response_models", {}).get(session_id)
    if not isinstance(entry, dict):
        return None
    model_id = entry.get("model_id")
    if not isinstance(model_id, str) or not model_id:
        return None
    profile_id = entry.get("profile_id")
    observed_at = entry.get("observed_at")
    return {
        "profile_id": profile_id if isinstance(profile_id, str) and profile_id else None,
        "model_id": model_id,
        "observed_at": observed_at if isinstance(observed_at, int) else None,
    }


# --- Writes (serialized read-modify-write) ------------------------------------


async def ensure_tag_colors(tags: object) -> dict:
    """Migrate legacy SDK tags into the stable color registry on first list."""
    async with _lock:
        data = _read_raw()
        if _reserve_tag_colors(data, tags):
            _write_raw(data)
        return data


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
        # Drop the entry entirely once it carries no active flags or tags.
        if (
            not entry.get("pinned")
            and not entry.get("archived")
            and not entry.get("tags")
        ):
            data["sessions"].pop(session_id, None)
        else:
            data["sessions"][session_id] = entry
        _write_raw(data)
        return {
            "pinned": bool(entry.get("pinned", False)),
            "archived": bool(entry.get("archived", False)),
        }


async def set_session_tags(session_id: str, tags: object) -> dict:
    """Persist up to three tags and reserve stable, unique color slots.

    The first 100 distinct tag names receive unique slots. The registry is kept
    after sessions are deleted so a reused name never changes color; names added
    after all slots are occupied fall back to their deterministic hash slot.
    """
    normalized = normalize_session_tags(tags)
    async with _lock:
        data = _read_raw()
        entry = data["sessions"].get(session_id)
        if not isinstance(entry, dict):
            entry = {"pinned": False, "archived": False}
        if normalized:
            entry["tags"] = normalized
        else:
            entry.pop("tags", None)
        if entry.get("pinned") or entry.get("archived") or entry.get("tags"):
            data["sessions"][session_id] = entry
        else:
            data["sessions"].pop(session_id, None)

        _reserve_tag_colors(data, normalized)

        _write_raw(data)
        return {
            "tags": normalized,
            "tag_colors": get_tag_colors(normalized, data),
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


async def set_recap(session_id: str, text: str, turns: int) -> None:
    """Store a session's one-line recap, replacing any previous one."""
    text = " ".join(text.split())[:_RECAP_MAX_CHARS].strip()
    if not session_id or not text:
        return
    async with _lock:
        data = _read_raw()
        data["recaps"][session_id] = {"text": text, "turns": int(turns)}
        _write_raw(data)


async def set_last_response_model(
    session_id: str,
    *,
    model_id: str | None,
    profile_id: str | None = None,
    observed_at: int | None = None,
) -> None:
    """Persist the provider-reported model of the latest assistant response."""
    if not session_id or not isinstance(model_id, str) or not model_id.strip():
        return
    model_id = model_id.strip()
    if not isinstance(profile_id, str) or not profile_id.strip():
        profile_id = None
    if not isinstance(observed_at, int):
        observed_at = int(time.time() * 1000)
    async with _lock:
        data = _read_raw()
        data["last_response_models"][session_id] = {
            "profile_id": profile_id.strip() if profile_id else None,
            "model_id": model_id,
            "observed_at": observed_at,
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
        changed = (data["recaps"].pop(session_id, None) is not None) or changed
        changed = (data["last_response_models"].pop(session_id, None) is not None) or changed
        recent_activities = [
            item for item in get_recent_activities(data)
            if item.get("session_id") != session_id
        ]
        if len(recent_activities) != len(data.get("recent_activities", [])):
            data["recent_activities"] = recent_activities
            changed = True
        if changed:
            _write_raw(data)
