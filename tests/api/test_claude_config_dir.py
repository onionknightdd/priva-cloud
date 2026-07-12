"""claude_config_dir() + legacy ~/.claude relocation (config-source consistency, item A).

The CLI's user scope is $CLAUDE_CONFIG_DIR (k8s pods: /workspace/.claude), NOT
Path.home()/".claude" (pods remap HOME to /workspace/.home). These tests pin:
- the shared helper (env override + local-dev fallback)
- that subagents / skills / global-MCP paths resolve through it
- the locked read-modify-write on settings.json (env block survives, 0600)
- the one-time startup migration out of the legacy ~/.claude tree
- the build_agent_options cwd fallback helper (per-user workspace, not work_dir root)
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from priva_common.paths import claude_config_dir
from priva_common.user_env import settings_json_path, write_settings_env


@pytest.fixture()
def isolated_dirs(tmp_path, monkeypatch):
    """Fake HOME and CLAUDE_CONFIG_DIR pointing at distinct tmp dirs (pod-like)."""
    home = tmp_path / "home"
    cfg = tmp_path / "cfg"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    return home, cfg


# --- helper resolution --------------------------------------------------------


def test_claude_config_dir_honors_env(isolated_dirs):
    home, cfg = isolated_dirs
    assert claude_config_dir() == cfg
    assert settings_json_path() == cfg / "settings.json"


def test_claude_config_dir_falls_back_to_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert claude_config_dir() == tmp_path / ".claude"


def test_service_paths_resolve_through_config_dir(isolated_dirs):
    home, cfg = isolated_dirs
    from priva_agent_runner.services.subagents import _agents_dir
    from priva_agent_runner.services.skills import _personal_skills_dir, _get_skills_dir
    from priva_agent_runner.services.mcp.config_manager import McpConfigManager

    assert _agents_dir("alice", scope="user") == cfg / "agents"
    assert _personal_skills_dir() == cfg / "skills"
    assert _get_skills_dir("global") == cfg / "skills"
    assert McpConfigManager("alice")._global_path == cfg / ".claude.json"
    # Nothing may resolve into the legacy (CLI-invisible) home tree.
    for p in (_agents_dir("alice", scope="user"), _personal_skills_dir()):
        assert not str(p).startswith(str(home))


def test_skill_exclude_walk_uses_config_dir(isolated_dirs):
    home, cfg = isolated_dirs
    from priva_common.skill_exclude import _list_global_skill_names

    (cfg / "skills" / "real-skill").mkdir(parents=True)
    (cfg / "skills" / "real-skill" / "SKILL.md").write_text("# s")
    (home / ".claude" / "skills" / "ghost").mkdir(parents=True)
    (home / ".claude" / "skills" / "ghost" / "SKILL.md").write_text("# g")
    assert _list_global_skill_names() == ["real-skill"]


# --- global MCP store is the CLI-native .claude.json ----------------------------


def test_global_mcp_crud_targets_claude_json(isolated_dirs):
    _, cfg = isolated_dirs
    from priva_agent_runner.services.mcp.config_manager import McpConfigManager

    write_settings_env({"ANTHROPIC_BASE_URL": "https://gw", "ANTHROPIC_AUTH_TOKEN": "tok"})
    # .claude.json is the CLI's own state file — foreign keys must survive our writes
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / ".claude.json").write_text(json.dumps({"oauthAccount": {"uuid": "abc"}}))

    mgr = McpConfigManager("alice")
    mgr.add_global_server("srv1", {"type": "http", "url": "https://mcp.example"})

    state = json.loads((cfg / ".claude.json").read_text())
    assert state["mcpServers"]["srv1"]["url"] == "https://mcp.example"
    assert state["oauthAccount"] == {"uuid": "abc"}
    # settings.json is untouched by MCP writes (env block only, no mcpServers)
    settings = json.loads((cfg / "settings.json").read_text())
    assert settings["env"]["ANTHROPIC_AUTH_TOKEN"] == "tok"
    assert "mcpServers" not in settings

    assert mgr.update_global_server("srv1", {"url": "https://mcp2.example"})["url"] == "https://mcp2.example"
    assert mgr.update_global_server("missing", {"url": "x"}) is None
    assert mgr.delete_global_server("srv1") is True
    assert mgr.delete_global_server("srv1") is False
    assert json.loads((cfg / ".claude.json").read_text())["oauthAccount"] == {"uuid": "abc"}


def test_global_mcp_creates_claude_json_0600(isolated_dirs):
    _, cfg = isolated_dirs
    from priva_agent_runner.services.mcp.config_manager import McpConfigManager

    McpConfigManager("alice").add_global_server("s", {"type": "http", "url": "https://x"})
    assert stat.S_IMODE(os.stat(cfg / ".claude.json").st_mode) == 0o600


def test_merge_global_servers_existing_names_win(isolated_dirs):
    _, cfg = isolated_dirs
    from priva_agent_runner.services.mcp.config_manager import McpConfigManager, merge_global_servers

    mgr = McpConfigManager("alice")
    mgr.add_global_server("keep", {"type": "http", "url": "https://canonical"})
    added = merge_global_servers({
        "keep": {"type": "http", "url": "https://legacy-loses"},
        "new": {"type": "http", "url": "https://legacy-new"},
    })
    assert added == 1
    servers = mgr.read_global_servers()
    assert servers["keep"]["url"] == "https://canonical"
    assert servers["new"]["url"] == "https://legacy-new"


# --- legacy ~/.claude migration -------------------------------------------------


def _seed_legacy(home: Path) -> Path:
    legacy = home / ".claude"
    (legacy / "skills" / "sk1").mkdir(parents=True)
    (legacy / "skills" / "sk1" / "SKILL.md").write_text("# sk1")
    (legacy / "agents").mkdir(parents=True)
    (legacy / "agents" / "helper.md").write_text("---\nname: helper\n---\n")
    (legacy / "settings.json").write_text(json.dumps({
        "mcpServers": {"legacy-srv": {"type": "http", "url": "https://legacy"}},
        "somethingElse": True,
    }))
    return legacy


def test_migration_moves_everything(isolated_dirs):
    home, cfg = isolated_dirs
    from priva_agent_runner.services.legacy_claude_dir import migrate_legacy_home_claude

    legacy = _seed_legacy(home)
    # interim state: a stray mcpServers block in the REAL settings.json too
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "settings.json").write_text(json.dumps({
        "env": {"ANTHROPIC_AUTH_TOKEN": "tok"},
        "mcpServers": {"interim-srv": {"type": "http", "url": "https://interim"}},
    }))
    migrate_legacy_home_claude()

    assert (cfg / "skills" / "sk1" / "SKILL.md").is_file()
    assert (cfg / "agents" / "helper.md").is_file()
    assert not (legacy / "skills").exists()
    assert not (legacy / "agents").exists()
    # both server sets land in the CLI-native .claude.json
    servers = json.loads((cfg / ".claude.json").read_text())["mcpServers"]
    assert servers["legacy-srv"]["url"] == "https://legacy"
    assert servers["interim-srv"]["url"] == "https://interim"
    # settings.json keeps its env block and loses the stray key
    settings = json.loads((cfg / "settings.json").read_text())
    assert settings["env"]["ANTHROPIC_AUTH_TOKEN"] == "tok"
    assert "mcpServers" not in settings
    # unowned keys stay behind in the legacy file, mcpServers is gone from it
    leftover = json.loads((legacy / "settings.json").read_text())
    assert leftover == {"somethingElse": True}

    # idempotent second pass
    migrate_legacy_home_claude()
    servers = json.loads((cfg / ".claude.json").read_text())["mcpServers"]
    assert set(servers) == {"legacy-srv", "interim-srv"}


def test_migration_target_wins_on_conflict(isolated_dirs):
    home, cfg = isolated_dirs
    from priva_agent_runner.services.legacy_claude_dir import migrate_legacy_home_claude

    legacy = _seed_legacy(home)
    (cfg / "skills" / "sk1").mkdir(parents=True)
    (cfg / "skills" / "sk1" / "SKILL.md").write_text("# canonical")
    migrate_legacy_home_claude()

    assert (cfg / "skills" / "sk1" / "SKILL.md").read_text() == "# canonical"
    # conflicting legacy entry is kept in place for inspection
    assert (legacy / "skills" / "sk1" / "SKILL.md").read_text() == "# sk1"


def test_migration_noop_when_dirs_coincide(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    from priva_agent_runner.services.legacy_claude_dir import migrate_legacy_home_claude

    live = tmp_path / ".claude"
    (live / "agents").mkdir(parents=True)
    (live / "agents" / "keep.md").write_text("x")
    migrate_legacy_home_claude()
    assert (live / "agents" / "keep.md").is_file()


# --- settings.json platform defaults ---------------------------------------------


def test_ensure_settings_defaults_seeds_and_respects_user(isolated_dirs):
    _, cfg = isolated_dirs
    from priva_common.user_env import ensure_claude_settings_defaults

    write_settings_env({"ANTHROPIC_BASE_URL": "https://gw", "ANTHROPIC_AUTH_TOKEN": "tok"})
    ensure_claude_settings_defaults()
    data = json.loads((cfg / "settings.json").read_text())
    assert data["enableAllProjectMcpServers"] is True
    assert data["env"]["ANTHROPIC_AUTH_TOKEN"] == "tok"

    # an explicit user value is never overridden (setdefault semantics)
    data["enableAllProjectMcpServers"] = False
    (cfg / "settings.json").write_text(json.dumps(data))
    ensure_claude_settings_defaults()
    assert json.loads((cfg / "settings.json").read_text())["enableAllProjectMcpServers"] is False


# --- cwd fallback helper --------------------------------------------------------


def test_get_workspace_for_username(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVA_SERVER__WORK_DIR", str(tmp_path / "ws"))
    from priva_common.config import get_settings
    get_settings.cache_clear()
    try:
        from priva_common.workspace import get_workspace_for_username
        ws = get_workspace_for_username("alice")
        assert ws == str(tmp_path / "ws" / "alice")
        assert Path(ws).is_dir()
        assert get_workspace_for_username(None).endswith("anonymous")
    finally:
        get_settings.cache_clear()
