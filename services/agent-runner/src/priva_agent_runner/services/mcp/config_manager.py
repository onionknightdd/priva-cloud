from __future__ import annotations

import fcntl
import json
import threading
from pathlib import Path
from typing import Any

from priva_common.logging import get_app_logger
from priva_common.config import get_settings

logger = get_app_logger(__name__)

_lock = threading.Lock()

GLOBAL_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


def _get_work_dir() -> Path:
    settings = get_settings()
    return Path(settings.server.work_dir).expanduser()


def _default_workspace(username: str) -> str:
    return str(_get_work_dir() / username)


def list_user_workdirs(username: str) -> list[str]:
    """Distinct session cwds ∪ the user's default workspace, in stable order.

    Mirrors the skills service so the MCP panel groups project servers by the
    same set of known project directories the rest of the app uses.
    """
    cwds: list[str] = []
    seen: set[str] = set()
    default_ws = _default_workspace(username)
    cwds.append(default_ws)
    seen.add(default_ws)
    try:
        from claude_agent_sdk import list_sessions

        for s in list_sessions(directory=None):
            cwd = getattr(s, "cwd", None)
            if cwd and cwd not in seen:
                seen.add(cwd)
                cwds.append(cwd)
    except Exception:
        logger.warning("list_sessions failed during MCP workdir enumeration", exc_info=True)
    return cwds


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read {}: {}", path, e)
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        with open(path, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                json.dump(data, f, indent=2)
                f.write("\n")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


class McpConfigManager:
    """Manage MCP server configs at project (.mcp.json) and global (~/.claude/settings.json) levels."""

    def __init__(self, username: str) -> None:
        self.username = username
        self._default_project_path = _get_work_dir() / username / ".mcp.json"
        self._global_path = GLOBAL_SETTINGS_PATH

    # ── Project-level (per-workdir {cwd}/.mcp.json) ──

    def _project_path(self, cwd: str | None) -> Path:
        """Path to a workdir's ``.mcp.json``. ``cwd=None`` -> default workspace."""
        if cwd:
            return Path(cwd).expanduser() / ".mcp.json"
        return self._default_project_path

    def read_project_servers(self, cwd: str | None = None) -> dict[str, dict]:
        data = _read_json(self._project_path(cwd))
        servers = data.get("mcpServers", {})
        return servers if isinstance(servers, dict) else {}

    def write_project_servers(self, cwd: str | None, servers: dict[str, dict]) -> None:
        # Preserve existing non-mcpServers keys
        path = self._project_path(cwd)
        existing = _read_json(path)
        existing["mcpServers"] = servers
        _write_json(path, existing)

    def add_project_server(self, cwd: str | None, name: str, config: dict) -> None:
        servers = self.read_project_servers(cwd)
        servers[name] = config
        self.write_project_servers(cwd, servers)

    def update_project_server(self, cwd: str | None, name: str, updates: dict) -> dict | None:
        servers = self.read_project_servers(cwd)
        if name not in servers:
            return None
        servers[name].update({k: v for k, v in updates.items() if v is not None})
        self.write_project_servers(cwd, servers)
        return servers[name]

    def delete_project_server(self, cwd: str | None, name: str) -> bool:
        servers = self.read_project_servers(cwd)
        if name not in servers:
            return False
        del servers[name]
        self.write_project_servers(cwd, servers)
        return True

    def read_project_groups(self) -> list[tuple[str, dict[str, dict]]]:
        """[(cwd, {name: config}), ...] for each known workdir that has servers."""
        groups: list[tuple[str, dict[str, dict]]] = []
        for cwd in list_user_workdirs(self.username):
            servers = self.read_project_servers(cwd)
            if servers:
                groups.append((cwd, servers))
        return groups

    # ── Global-level (~/.claude/settings.json .mcpServers) ──

    def read_global_servers(self) -> dict[str, dict]:
        data = _read_json(self._global_path)
        servers = data.get("mcpServers", {})
        return servers if isinstance(servers, dict) else {}

    def _write_global_servers(self, servers: dict[str, dict]) -> None:
        existing = _read_json(self._global_path)
        existing["mcpServers"] = servers
        _write_json(self._global_path, existing)

    def add_global_server(self, name: str, config: dict) -> None:
        servers = self.read_global_servers()
        servers[name] = config
        self._write_global_servers(servers)

    def update_global_server(self, name: str, updates: dict) -> dict | None:
        servers = self.read_global_servers()
        if name not in servers:
            return None
        servers[name].update({k: v for k, v in updates.items() if v is not None})
        self._write_global_servers(servers)
        return servers[name]

    def delete_global_server(self, name: str) -> bool:
        servers = self.read_global_servers()
        if name not in servers:
            return False
        del servers[name]
        self._write_global_servers(servers)
        return True

    # ── Merged view ──

    def read_all_servers(self, cwd: str | None = None) -> list[tuple[str, dict, str]]:
        """Return [(name, config, level), ...] merging a workdir's project servers
        and global. ``cwd=None`` uses the default workspace.

        Project servers take precedence over global servers with the same name.
        """
        result: list[tuple[str, dict, str]] = []
        seen: set[str] = set()

        for name, config in self.read_project_servers(cwd).items():
            result.append((name, config, "project"))
            seen.add(name)

        for name, config in self.read_global_servers().items():
            if name not in seen:
                result.append((name, config, "global"))

        return result

    # ── For agent options ──

    def build_mcp_dict(
        self, cwd: str | None = None, filter_names: list[str] | None = None
    ) -> dict[str, Any]:
        """Build the dict suitable for ClaudeAgentOptions.mcp_servers.

        - cwd -> the session workdir whose project servers apply (None = default)
        - filter_names=None -> all servers
        - filter_names=[] -> empty dict (disable all)
        - filter_names=["A","B"] -> only those servers
        """
        if filter_names is not None and len(filter_names) == 0:
            return {}

        all_servers = self.read_all_servers(cwd)
        result: dict[str, Any] = {}

        for name, config, _level in all_servers:
            if filter_names is not None and name not in filter_names:
                continue

            server_type = config.get("type", "http")
            sdk_config: dict[str, Any] = {
                "type": server_type,
                "url": config["url"],
            }
            headers = config.get("headers")
            if headers:
                sdk_config["headers"] = headers

            result[name] = sdk_config

        return result
