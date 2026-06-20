"""Runtime config store — the PVC-owned half of the old UserStore.

`runtime.*` settings (cli_path, append_systemprompt, history_retention_days,
retryable_tools, risky_tool_list, pii_masking, skill_exclude) live in the
``runtime`` section of ``.priva.settings.yml`` and stay file-backed (Phase-1
decision: runtime config is pod/PVC-owned, NOT in data-spine). UserStore
delegates its get/update_runtime_config here so account methods can move to the
data-plane client (U1) without disturbing runtime config.
"""

from __future__ import annotations

import fcntl
import threading
from pathlib import Path

import yaml

from .logging import get_app_logger
from .paths import priva_home

logger = get_app_logger(__name__)


def _settings_path() -> Path:
    return priva_home() / ".priva.settings.yml"


class RuntimeConfigStore:
    def __init__(self, path: Path | None = None):
        self._path = path if path is not None else _settings_path()
        self._lock = threading.Lock()

    def _load(self) -> dict:
        if not self._path.exists():
            return {}
        with open(self._path, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                return yaml.safe_load(f) or {}
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def _save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def get_runtime_config(self) -> dict:
        data = self._load()
        runtime = data.get("runtime", {})
        # Lazy migration: sensitive_data_patterns -> pii_masking.{enable,patterns}.
        # Default enable=False so existing users keep the SSE-only mask behavior
        # they already see (patterns still feed the outbound mask path).
        if (
            isinstance(runtime, dict)
            and "sensitive_data_patterns" in runtime
            and "pii_masking" not in runtime
        ):
            patterns = runtime.get("sensitive_data_patterns") or []
            with self._lock:
                disk = self._load()
                disk_runtime = disk.setdefault("runtime", {})
                if (
                    "sensitive_data_patterns" in disk_runtime
                    and "pii_masking" not in disk_runtime
                ):
                    disk_runtime["pii_masking"] = {
                        "enable": False,
                        "patterns": patterns,
                    }
                    disk_runtime.pop("sensitive_data_patterns", None)
                    self._save(disk)
                runtime = disk.get("runtime", {})
        return runtime

    def update_runtime_config(self, key: str, value: dict | None) -> dict:
        # Known runtime keys (schemaless dict; each feature owns its own schema):
        #   cli_path               : str | None
        #   append_systemprompt    : dict {enable, content}
        #   history_retention_days : int
        #   retryable_tools        : list[dict]
        #   risky_tool_list        : list[str] -- Claude Code permission DSL
        #                            patterns forcing user approval in
        #                            bypassPermissions mode.
        #   pii_masking            : dict {enable, patterns}
        # Passing value=None removes the key.
        with self._lock:
            data = self._load()
            runtime = data.setdefault("runtime", {})
            if value is None:
                runtime.pop(key, None)
            else:
                runtime[key] = value
            self._save(data)
            return data["runtime"]


_store: RuntimeConfigStore | None = None


def get_runtime_config_store() -> RuntimeConfigStore:
    global _store
    if _store is None:
        _store = RuntimeConfigStore()
    return _store
