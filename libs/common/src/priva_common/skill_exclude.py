"""Per-user ``skill_exclude`` denylist + generic ``.priva.user.yml`` accessors.

Extracted from ``api/services/channels/config_store.py`` (Phase 2) so the skill
execution path (agent-runner) and the skill-config face (control-panel) can read
and write the denylist without either service importing ``channels``. The file
layout (``$work_dir/<username>/.priva.user.yml``) and the lazy
``enable_global_skills`` -> ``skill_exclude`` migration are preserved verbatim.
"""

from __future__ import annotations

import fcntl
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

import yaml

from .config import get_settings
from .logging import get_app_logger

logger = get_app_logger(__name__)

_lock = threading.Lock()


def _get_work_dir() -> Path:
    settings = get_settings()
    return Path(settings.server.work_dir).expanduser()


def _get_user_config_path(username: str) -> Path:
    return _get_work_dir() / username / ".priva.user.yml"


def _read_user_yaml(username: str) -> dict:
    """Read full .priva.user.yml as a dict."""
    path = _get_user_config_path(username)
    if not path.exists():
        return {}
    try:
        with open(path, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                return yaml.safe_load(f) or {}
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except Exception:
        logger.warning("Failed to read user yaml for {}", username)
        return {}


def get_user_yaml_key(username: str, key: str, default: Any = None) -> Any:
    """Read a top-level key from .priva.user.yml."""
    return _read_user_yaml(username).get(key, default)


def save_user_yaml_key(username: str, key: str, value: Any) -> None:
    """Atomically set (or delete, when ``value`` is ``None``) a top-level key in
    .priva.user.yml. Passing ``None`` pops the key entirely."""
    path = _get_user_config_path(username)
    path.parent.mkdir(parents=True, exist_ok=True)

    with _lock:
        existing = _read_user_yaml(username)
        if value is None:
            existing.pop(key, None)
        else:
            existing[key] = value

        fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix=".priva.user.")
        try:
            with os.fdopen(fd, "w") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    yaml.dump(existing, f, default_flow_style=False, allow_unicode=True)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def _list_global_skill_names() -> list[str]:
    """Filesystem walk of ~/.claude/skills/ — used by migration only."""
    global_dir = Path.home() / ".claude" / "skills"
    if not global_dir.exists():
        return []
    names: list[str] = []
    try:
        for entry in global_dir.iterdir():
            if entry.is_dir() and (entry / "SKILL.md").exists():
                names.append(entry.name)
    except OSError:
        return []
    return names


def get_skill_exclude(username: str) -> list[str]:
    """Return the skill_exclude denylist for ``username``.

    Lazily migrates legacy ``enable_global_skills`` into ``skill_exclude`` on
    first read:
    - ``'auto'`` / unset -> ``[]`` (nothing excluded)
    - ``['a', 'b']`` (allowlist) -> exclude every currently-discovered global
      skill not in the list
    - ``null`` / ``[]`` -> exclude every currently-discovered global skill
    """
    raw = _read_user_yaml(username)
    if "skill_exclude" in raw:
        value = raw.get("skill_exclude")
        return list(value) if isinstance(value, list) else []

    if "enable_global_skills" not in raw:
        return []

    legacy = raw.get("enable_global_skills")
    discovered = _list_global_skill_names()
    if legacy == "auto":
        migrated: list[str] = []
    elif legacy is None or legacy == [] or legacy == "":
        migrated = list(discovered)
    elif isinstance(legacy, list):
        allowed = set(legacy)
        migrated = [n for n in discovered if n not in allowed]
    else:
        migrated = []

    save_user_yaml_key(username, "skill_exclude", migrated)
    save_user_yaml_key(username, "enable_global_skills", None)
    return migrated


def save_skill_exclude(username: str, value: list[str]) -> None:
    """Write the explicit skill_exclude denylist."""
    save_user_yaml_key(username, "skill_exclude", list(value or []))
