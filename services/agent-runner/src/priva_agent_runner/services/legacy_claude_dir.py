"""One-time relocation of resources stranded in the legacy ``~/.claude`` tree.

Before the ``claude_config_dir()`` fix, three services wrote CLI user-scope
resources to ``Path.home()/".claude"`` — a directory the claude CLI never reads
once CLAUDE_CONFIG_DIR is set (k8s pods: HOME=/workspace/.home,
CLAUDE_CONFIG_DIR=/workspace/.claude):

- subagents  -> ~/.claude/agents/            (never discovered by the CLI)
- personal skills -> ~/.claude/skills/       (never discovered by the CLI)
- global MCP servers -> ~/.claude/settings.json ``mcpServers``

Global MCP servers land in the canonical $CLAUDE_CONFIG_DIR/.claude.json
(``claude mcp add --scope user`` territory), and a second step also relocates
any ``mcpServers`` block sitting in $CLAUDE_CONFIG_DIR/settings.json — the
interim home between the path fix and the files-canonical cutover.

Runs once per pod start from the app lifespan. Idempotent: after a successful
pass the legacy locations are empty/absent, and in local dev — where HOME and
CLAUDE_CONFIG_DIR resolve to the same ``~/.claude`` — the home-tree part is a
no-op. On name conflicts the target (canonical) copy wins and the legacy entry
is left in place for manual inspection.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from priva_common.logging import get_app_logger
from priva_common.paths import claude_config_dir

logger = get_app_logger(__name__)

_MOVE_SUBDIRS = ("skills", "agents")


def _move_children(src_dir: Path, dest_dir: Path) -> tuple[int, int]:
    """Move src_dir's children into dest_dir; existing dest names win. -> (moved, kept)."""
    moved = kept = 0
    dest_dir.mkdir(parents=True, exist_ok=True)
    for entry in sorted(src_dir.iterdir()):
        dest = dest_dir / entry.name
        if dest.exists():
            kept += 1
            continue
        shutil.move(str(entry), str(dest))
        moved += 1
    return moved, kept


def _rmdir_if_empty(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass  # not empty (conflict leftovers) or already gone


def _migrate_mcp_servers(legacy_settings: Path) -> int:
    """Move the ``mcpServers`` block from the legacy settings.json into the
    canonical global store (existing names win). Returns servers added."""
    try:
        data = json.loads(legacy_settings.read_text())
    except (ValueError, OSError):
        return 0
    if not isinstance(data, dict):
        return 0
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        return 0

    from .mcp.config_manager import merge_global_servers

    added = merge_global_servers(servers)
    # Drop the migrated key from the legacy file; delete the file when that
    # leaves it empty, otherwise keep the remaining (unowned) keys around.
    del data["mcpServers"]
    if data:
        legacy_settings.write_text(json.dumps(data, indent=2) + "\n")
    else:
        legacy_settings.unlink()
    return added


def _migrate_settings_json_mcp(target: Path) -> int:
    """Move a stray ``mcpServers`` block out of $CLAUDE_CONFIG_DIR/settings.json
    into the canonical .claude.json store (existing names win). settings.json
    briefly carried global MCP servers between the claude_config_dir() fix and
    the files-canonical cutover; the CLI never read the key from there."""
    settings_file = target / "settings.json"
    try:
        data = json.loads(settings_file.read_text())
    except (FileNotFoundError, ValueError, OSError):
        return 0
    if not isinstance(data, dict):
        return 0
    servers = data.pop("mcpServers", None)
    if not isinstance(servers, dict) or not servers:
        return 0

    from .mcp.config_manager import merge_global_servers

    added = merge_global_servers(servers)
    settings_file.write_text(json.dumps(data, indent=2) + "\n")
    return added


def migrate_legacy_home_claude() -> None:
    target = claude_config_dir()
    try:
        moved = _migrate_settings_json_mcp(target)
        if moved:
            logger.info("moved {} mcpServers from settings.json into .claude.json", moved)
    except OSError:
        logger.warning("settings.json mcpServers relocation failed", exc_info=True)

    legacy = Path.home() / ".claude"
    if not legacy.is_dir() or legacy.resolve() == target.resolve():
        return

    summary: dict[str, int] = {}
    for sub in _MOVE_SUBDIRS:
        src_dir = legacy / sub
        if not src_dir.is_dir():
            continue
        try:
            moved, kept = _move_children(src_dir, target / sub)
        except OSError:
            logger.warning("legacy ~/.claude/{} migration failed", sub, exc_info=True)
            continue
        if moved:
            summary[sub] = moved
        if kept:
            logger.warning(
                "legacy ~/.claude/{}: {} entries kept (name exists at target {})",
                sub, kept, target / sub,
            )
        _rmdir_if_empty(src_dir)

    legacy_settings = legacy / "settings.json"
    if legacy_settings.is_file():
        try:
            added = _migrate_mcp_servers(legacy_settings)
        except OSError:
            logger.warning("legacy mcpServers migration failed", exc_info=True)
            added = 0
        if added:
            summary["mcpServers"] = added

    _rmdir_if_empty(legacy)
    if summary:
        logger.info("migrated legacy {} -> {}: {}", legacy, target, summary)


def migrate_local_hooks_to_settings() -> None:
    """Relocate user hooks from the CLI-invisible ``settings.local.json`` into the
    CLI-loaded project ``settings.json`` (D5), for every workdir of every account
    user.

    The runner passes ``setting_sources=["project","user"]`` — the "local" source
    is never loaded, so pre-D5 user hooks in ``settings.local.json`` only ever fired
    via the now-removed programmatic path. Moving them to ``settings.json`` lets the
    CLI run them natively (in SDK runs AND terminal sessions). Idempotent and
    fail-soft per user. See ``hooks.config_manager.migrate_local_hooks``.
    """
    import os

    from priva_common.user_store import get_user_store

    from .hooks.config_manager import migrate_local_hooks

    # A per-account pod migrates only its own account: enumerating every account
    # from the pod that runs untrusted tenant code is exactly the cross-tenant
    # read data-spine now denies (AccountService/List is control-plane only).
    pinned = os.environ.get("USERNAME")
    if pinned:
        usernames = [pinned]
    else:
        try:  # monolith / single-process dev: no pinned account
            usernames = [u.username for u in get_user_store().list_users()]
        except Exception:
            logger.warning("user enumeration failed; settings.local hooks migration skipped",
                           exc_info=True)
            return

    total = 0
    for username in usernames:
        try:
            total += migrate_local_hooks(username)
        except OSError:
            logger.warning("settings.local hooks migration failed for {}", username, exc_info=True)
    if total:
        logger.info(
            "migrated user hooks settings.local.json -> settings.json across {} workdir(s)", total
        )
