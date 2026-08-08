"""Single-pod ``skill_exclude`` denylist + generic ``.priva.user.yml`` accessors.

Extracted from ``api/services/channels/config_store.py`` (Phase 2) so the skill
execution path (agent-runner) and the skill-config face (control-panel) can read
and write the denylist without either service importing ``channels``.  A
single-account runner stores one file at ``priva_home()/.priva.user.yml``.
"""

from __future__ import annotations

import fcntl
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

import yaml

from .logging import get_app_logger
from .paths import priva_home

logger = get_app_logger(__name__)

_lock = threading.Lock()


def _get_user_config_path() -> Path:
    return priva_home() / ".priva.user.yml"


def _read_user_yaml() -> dict:
    """Read full .priva.user.yml as a dict."""
    path = _get_user_config_path()
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
        logger.warning("Failed to read .priva.user.yml")
        return {}


def get_user_yaml_key(key: str, default: Any = None) -> Any:
    """Read a top-level key from .priva.user.yml."""
    return _read_user_yaml().get(key, default)


def save_user_yaml_key(key: str, value: Any) -> None:
    """Atomically set (or delete, when ``value`` is ``None``) a top-level key in
    .priva.user.yml. Passing ``None`` pops the key entirely."""
    path = _get_user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    with _lock:
        existing = _read_user_yaml()
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


def get_skill_exclude() -> list[str]:
    """Return the single-pod skill_exclude denylist.
    """
    value = _read_user_yaml().get("skill_exclude", [])
    return list(value) if isinstance(value, list) else []


def save_skill_exclude(value: list[str]) -> None:
    """Write the explicit skill_exclude denylist."""
    save_user_yaml_key("skill_exclude", list(value or []))
