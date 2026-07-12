"""User hook configuration in Claude Code ``settings.json`` (CLI-native).

User-configured hooks live in the settings files the CLI actually loads, so it
executes them directly in SDK runs AND terminal ``claude`` — no programmatic
injection (config-source consistency, item D5). Two scopes, mirroring the
subagents / MCP model:

- scope ``"user"``    -> ``$CLAUDE_CONFIG_DIR/settings.json``   (every run)
- scope ``"project"`` -> ``{cwd}/.claude/settings.json``        (that workdir only)

``settings.local.json`` is intentionally NEVER written: the runner passes
``setting_sources=["project", "user"]``, so the CLI does not load the "local"
source and a hook placed there would silently never fire.

Writes are surgical — only the top-level ``hooks`` key is touched, under
``flock(LOCK_EX)`` spanning the read-modify-write (``O_RDWR``, no
truncate-on-open), so a concurrent credentials writer on the same user-scope
file (the ``env`` block owned by ``priva_common.user_env``) is never clobbered.
The user-scope file is kept ``0600`` (it holds the auth token).
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
from pathlib import Path
from typing import Callable

from priva_common.logging import get_app_logger
from priva_common.paths import claude_config_dir
from priva_common.workspace import get_workspace_for_username

logger = get_app_logger(__name__)

_PRIVA_ENFORCED_KEY = "__priva_enforced"
_lock = threading.Lock()

VALID_SCOPES = ("user", "project")

# transform(current_hooks) -> (new_hooks | None, changed)
_HooksTransform = Callable[[dict], "tuple[dict | None, bool]"]


def settings_path_for(username: str, scope: str, cwd: str | None) -> Path:
    """Resolve ``(scope, cwd)`` to its ``settings.json`` (no mkdir).

    - ``"user"``    -> ``$CLAUDE_CONFIG_DIR/settings.json``
    - ``"project"`` -> ``{cwd}/.claude/settings.json`` (``cwd=None`` -> default workspace)
    """
    if scope == "user":
        return claude_config_dir() / "settings.json"
    base = cwd or get_workspace_for_username(username)
    return Path(base).expanduser() / ".claude" / "settings.json"


def _read_settings(path: Path) -> dict:
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
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read {}: {}", path, exc)
        return {}


def read_hooks_key(path: Path) -> dict:
    """Return the top-level ``hooks`` block of *path*, or ``{}``."""
    hooks = _read_settings(path).get("hooks")
    return hooks if isinstance(hooks, dict) else {}


def _rewrite_hooks_key(path: Path, transform: _HooksTransform, *, user_scope: bool) -> None:
    """Atomically read-modify-write ONLY the ``hooks`` key of *path*.

    ``transform`` receives the current hooks dict and returns
    ``(new_hooks, changed)``. When ``changed`` is False nothing is written; when
    ``new_hooks`` is falsy the key is removed. ``flock(LOCK_EX)`` spans the read
    and the write; every OTHER top-level key (``env``/``mcpServers``/…) is preserved.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600 if user_scope else 0o644)
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
                current = data.get("hooks")
                current = current if isinstance(current, dict) else {}
                new_hooks, changed = transform(current)
                if not changed:
                    return
                if new_hooks:
                    data["hooks"] = new_hooks
                else:
                    data.pop("hooks", None)
                f.seek(0)
                f.truncate()
                json.dump(data, f, indent=2)
                f.write("\n")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    if user_scope:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


class HookConfigManager:
    """Scope-aware read/write of user hooks in the CLI's ``settings.json`` files."""

    def __init__(self, username: str):
        self.username = username

    def _path(self, scope: str, cwd: str | None) -> Path:
        return settings_path_for(self.username, scope, cwd)

    # -- Reads --------------------------------------------------------------

    def read_scope_hooks(self, scope: str, cwd: str | None = None) -> dict:
        return read_hooks_key(self._path(scope, cwd))

    def read_all(self) -> list[tuple[str, str | None, dict]]:
        """``[(scope, cwd, hooks), …]`` — user scope first, then every project
        workdir that actually has hooks (empty scopes are omitted, except user
        which is always present so the UI can offer it as an add target)."""
        out: list[tuple[str, str | None, dict]] = [("user", None, self.read_scope_hooks("user"))]

        from ..mcp.config_manager import list_user_workdirs

        for cwd in list_user_workdirs(self.username):
            hooks = self.read_scope_hooks("project", cwd)
            if hooks:
                out.append(("project", cwd, hooks))
        return out

    # -- Writes -------------------------------------------------------------

    def write_scope_hooks(self, scope: str, cwd: str | None, hooks: dict) -> None:
        """Replace the ``hooks`` block of one scope's ``settings.json``."""
        self.purge_legacy_enforced(scope, cwd)  # never leave orphans behind a write
        _rewrite_hooks_key(
            self._path(scope, cwd), lambda _current: (hooks, True), user_scope=(scope == "user")
        )

    # -- Legacy cleanup -----------------------------------------------------

    def purge_legacy_enforced(self, scope: str = "project", cwd: str | None = None) -> None:
        """Strip leftover ``__priva_enforced`` entries from a scope's settings.json.

        Admin hooks are delivered natively (managed ConfigMap) or programmatically
        (fallback callbacks) — never mirrored into settings files. Entries tagged by
        the long-removed ``ensure_admin_hooks`` channel are orphans the CLI would
        still execute; delete them once, idempotently.
        """

        def _strip(current: dict) -> tuple[dict, bool]:
            changed = False
            cleaned: dict = {}
            for event, entries in current.items():
                if not isinstance(entries, list):
                    cleaned[event] = entries
                    continue
                kept = [
                    e for e in entries
                    if not (isinstance(e, dict) and e.get(_PRIVA_ENFORCED_KEY))
                ]
                if len(kept) != len(entries):
                    changed = True
                if kept:
                    cleaned[event] = kept
                else:
                    changed = True  # drop the now-empty event key entirely
            return cleaned, changed

        _rewrite_hooks_key(self._path(scope, cwd), _strip, user_scope=(scope == "user"))

    def purge_all_legacy(self) -> None:
        """Strip ``__priva_enforced`` orphans from every project workdir scope."""
        from ..mcp.config_manager import list_user_workdirs

        for cwd in list_user_workdirs(self.username):
            self.purge_legacy_enforced("project", cwd)


def migrate_local_hooks(username: str) -> int:
    """Relocate user hooks from CLI-invisible ``settings.local.json`` into the
    CLI-loaded project ``settings.json`` for every known workdir of *username*.

    ``setting_sources=["project","user"]`` means the "local" source is never
    loaded, so pre-D5 user hooks written to ``settings.local.json`` silently never
    fired natively (they only ran via the removed programmatic path). Merge them
    into ``{cwd}/.claude/settings.json`` (existing project entries first, then the
    migrated local ones) and drop the ``hooks`` key from the local file. Idempotent
    — a second pass finds no local hooks and is a no-op. Returns workdirs migrated.
    """
    from ..mcp.config_manager import list_user_workdirs

    migrated = 0
    for cwd in list_user_workdirs(username):
        local_path = Path(cwd).expanduser() / ".claude" / "settings.local.json"
        local_hooks = read_hooks_key(local_path)
        if not local_hooks:
            continue

        def _merge(current: dict, _lh: dict = local_hooks) -> tuple[dict, bool]:
            merged: dict = {
                k: (list(v) if isinstance(v, list) else v) for k, v in current.items()
            }
            for event, entries in _lh.items():
                if not isinstance(entries, list):
                    continue
                merged[event] = (merged.get(event) or []) + entries
            return merged, True

        _rewrite_hooks_key(settings_path_for(username, "project", cwd), _merge, user_scope=False)
        _rewrite_hooks_key(local_path, lambda _current: ({}, True), user_scope=False)
        migrated += 1
    return migrated
