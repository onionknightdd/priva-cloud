"""Read/write CLAUDE.md memory at the User and Project scopes (config-source
consistency, item E).

- ``user``    -> ``$CLAUDE_CONFIG_DIR/CLAUDE.md``  (every run)
- ``project`` -> ``{cwd}/CLAUDE.md``               (that workdir; project root)

Both are loaded natively by the CLI, so an edit here is picked up by SDK runs
AND terminal ``claude`` on the next invocation — no restart, no injection.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import threading
from pathlib import Path

from fastapi import HTTPException

# The SDK's own project-dir derivation (realpath -> NFC -> sanitize), which
# respects CLAUDE_CONFIG_DIR — the same functions session_add_dirs.py uses so a
# sidecar lands beside the transcript. Reusing them means the auto-memory dir we
# resolve is byte-for-byte the one the CLI writes to.
from claude_agent_sdk._internal.sessions import _canonicalize_path, _get_project_dir

from priva_common.logging import get_app_logger
from priva_common.models.memory import (
    AutoMemoryContent,
    AutoMemoryFile,
    AutoMemoryListResponse,
    AutoMemoryProject,
    MemoryContent,
    MemoryListResponse,
    MemoryScopeInfo,
)
from priva_common.paths import claude_config_dir
from priva_common.workspace import get_workspace_for_username

logger = get_app_logger(__name__)

VALID_SCOPES = ("user", "project")
MAX_MEMORY_BYTES = 512 * 1024  # generous headroom; guards against pathological input

# Auto-memory files are flat markdown notes; anything path-like is rejected.
_AUTO_FILE_RE = re.compile(r"^[A-Za-z0-9._-]+\.md$")
_settings_lock = threading.Lock()  # serializes in-process autoMemoryEnabled writers


def _memory_path(username: str, scope: str, cwd: str | None) -> Path:
    """Resolve (scope, cwd) to its CLAUDE.md (no mkdir).

    - ``user``    -> $CLAUDE_CONFIG_DIR/CLAUDE.md
    - ``project`` -> {cwd}/CLAUDE.md (project root; cwd=None -> default workspace)
    """
    if scope == "user":
        return claude_config_dir() / "CLAUDE.md"
    if cwd:
        if not os.path.isabs(cwd):
            raise HTTPException(400, "An absolute 'cwd' is required for project memory")
        base = cwd
    else:
        base = get_workspace_for_username(username)
    return Path(base).expanduser() / "CLAUDE.md"


def _validate_scope(scope: str) -> None:
    if scope not in VALID_SCOPES:
        raise HTTPException(422, f"Invalid scope: {scope}")


def _info(scope: str, cwd: str | None, path: Path) -> MemoryScopeInfo:
    exists = path.is_file()
    return MemoryScopeInfo(
        scope=scope,
        cwd=cwd,
        path=str(path),
        exists=exists,
        size=path.stat().st_size if exists else 0,
    )


def list_memory(username: str) -> MemoryListResponse:
    """User scope + one entry per known project workdir (existence flagged)."""
    from .mcp.config_manager import list_user_workdirs

    scopes = [_info("user", None, _memory_path(username, "user", None))]
    for cwd in list_user_workdirs(username):
        scopes.append(_info("project", cwd, _memory_path(username, "project", cwd)))
    return MemoryListResponse(scopes=scopes)


def read_memory(username: str, scope: str, cwd: str | None) -> MemoryContent:
    _validate_scope(scope)
    path = _memory_path(username, scope, cwd)
    if path.is_file():
        if path.stat().st_size > MAX_MEMORY_BYTES:
            raise HTTPException(413, "CLAUDE.md is too large to edit here")
        content = path.read_text(encoding="utf-8", errors="replace")
        exists = True
    else:
        content = ""
        exists = False
    return MemoryContent(scope=scope, cwd=cwd, path=str(path), content=content, exists=exists)


def write_memory(username: str, scope: str, cwd: str | None, content: str) -> MemoryContent:
    _validate_scope(scope)
    if len(content.encode("utf-8")) > MAX_MEMORY_BYTES:
        raise HTTPException(413, "CLAUDE.md content is too large")
    path = _memory_path(username, scope, cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replace so a concurrent CLI read never sees a half-written file.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    return MemoryContent(scope=scope, cwd=cwd, path=str(path), content=content, exists=True)


# --- Auto memory (Claude-written) -------------------------------------------
#
# Curate the memory files Claude Code keeps for itself per project. We only
# browse / edit / delete existing files and flip the per-project toggle — we
# never author files (that is Claude's job). See models.memory for the layout.


def _auto_memory_dir(cwd: str) -> Path:
    """The Claude-written auto-memory dir for a session cwd:
    ``$CLAUDE_CONFIG_DIR/projects/<sanitized-cwd>/memory/`` (via the SDK's own
    derivation, so it matches the CLI byte-for-byte and honors CLAUDE_CONFIG_DIR)."""
    return _get_project_dir(_canonicalize_path(cwd)) / "memory"


def _auto_settings_path(username: str, cwd: str) -> Path:
    """``{cwd}/.claude/settings.json`` — where the per-project ``autoMemoryEnabled``
    toggle is written (the CLI reads it natively on the next run)."""
    from .hooks.config_manager import settings_path_for

    return settings_path_for(username, "project", cwd)


def _known_workdir(username: str, cwd: str) -> None:
    """Constrain ``cwd`` to the user's own known workdirs — the same set the list
    endpoint returns — so a request can only reach memory for a project the user
    actually has, never an arbitrary path."""
    from .mcp.config_manager import list_user_workdirs

    if cwd not in list_user_workdirs(username):
        raise HTTPException(404, "Unknown project workdir")


def _auto_file_path(username: str, cwd: str, name: str) -> Path:
    """Validate (cwd, name) and resolve to a file strictly inside the memory dir."""
    _known_workdir(username, cwd)
    if not name or "/" in name or "\\" in name or ".." in name or not _AUTO_FILE_RE.match(name):
        raise HTTPException(422, "Invalid memory file name")
    base = _auto_memory_dir(cwd)
    path = base / name
    # Defense in depth: the resolved file must not escape the memory dir.
    try:
        path.resolve().relative_to(base.resolve())
    except (ValueError, OSError):
        raise HTTPException(422, "Invalid memory file path")
    return path


def _read_auto_enabled(path: Path) -> bool:
    """``autoMemoryEnabled`` from a settings.json, defaulting to True (the CLI
    default for >= 2.1.59)."""
    if not path.exists():
        return True
    try:
        with open(path) as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                data = json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except (json.JSONDecodeError, OSError):
        return True
    val = data.get("autoMemoryEnabled") if isinstance(data, dict) else None
    return val if isinstance(val, bool) else True


def _write_auto_enabled(path: Path, enabled: bool) -> None:
    """Surgically set ONLY the ``autoMemoryEnabled`` key of a settings.json under
    ``flock(LOCK_EX)`` — every other key (``hooks``/…) is preserved. Project scope,
    so 0644 (no secrets here, unlike the user-scope credential file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _settings_lock:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
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
                data["autoMemoryEnabled"] = enabled
                f.seek(0)
                f.truncate()
                json.dump(data, f, indent=2)
                f.write("\n")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


def _list_auto_files(memory_dir: Path) -> list[AutoMemoryFile]:
    if not memory_dir.is_dir():
        return []
    out: list[AutoMemoryFile] = []
    for entry in memory_dir.iterdir():
        if entry.is_file() and _AUTO_FILE_RE.match(entry.name):
            out.append(AutoMemoryFile(
                name=entry.name,
                path=str(entry),
                size=entry.stat().st_size,
                is_index=(entry.name == "MEMORY.md"),
            ))
    # MEMORY.md (the always-loaded index) first, then the rest alphabetically.
    out.sort(key=lambda f: (not f.is_index, f.name.lower()))
    return out


def list_auto_memory(username: str) -> AutoMemoryListResponse:
    """One entry per known project workdir: its auto-memory dir, files, and the
    per-project on/off state."""
    from .mcp.config_manager import list_user_workdirs

    projects: list[AutoMemoryProject] = []
    for cwd in list_user_workdirs(username):
        memory_dir = _auto_memory_dir(cwd)
        projects.append(AutoMemoryProject(
            cwd=cwd,
            label=Path(cwd).name or cwd,
            memory_dir=str(memory_dir),
            enabled=_read_auto_enabled(_auto_settings_path(username, cwd)),
            exists=memory_dir.is_dir(),
            files=_list_auto_files(memory_dir),
        ))
    return AutoMemoryListResponse(projects=projects)


def read_auto_memory(username: str, cwd: str, name: str) -> AutoMemoryContent:
    path = _auto_file_path(username, cwd, name)
    if path.is_file():
        if path.stat().st_size > MAX_MEMORY_BYTES:
            raise HTTPException(413, "Memory file is too large to edit here")
        content = path.read_text(encoding="utf-8", errors="replace")
        exists = True
    else:
        content = ""
        exists = False
    return AutoMemoryContent(cwd=cwd, name=name, path=str(path), content=content, exists=exists)


def write_auto_memory(username: str, cwd: str, name: str, content: str) -> AutoMemoryContent:
    """Edit an EXISTING auto-memory file (atomic replace). Create is intentionally
    unsupported — Claude authors these files; the platform only curates them, so a
    write to a missing file is a 404 rather than a silent create."""
    path = _auto_file_path(username, cwd, name)
    if not path.is_file():
        raise HTTPException(404, "Memory file does not exist (create is not supported)")
    if len(content.encode("utf-8")) > MAX_MEMORY_BYTES:
        raise HTTPException(413, "Memory content is too large")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    return AutoMemoryContent(cwd=cwd, name=name, path=str(path), content=content, exists=True)


def delete_auto_memory(username: str, cwd: str, name: str) -> str:
    """Delete one auto-memory file. Returns its path (for the audit log)."""
    path = _auto_file_path(username, cwd, name)
    try:
        path.unlink()
    except FileNotFoundError:
        raise HTTPException(404, "Memory file not found")
    except OSError as exc:
        raise HTTPException(500, f"Could not delete memory file: {exc}")
    return str(path)


def set_auto_memory_enabled(username: str, cwd: str, enabled: bool) -> bool:
    """Per-project auto-memory switch: ``autoMemoryEnabled`` in
    ``{cwd}/.claude/settings.json``. The CLI honors it on the next run (SDK +
    terminal)."""
    _known_workdir(username, cwd)
    _write_auto_enabled(_auto_settings_path(username, cwd), enabled)
    return enabled
