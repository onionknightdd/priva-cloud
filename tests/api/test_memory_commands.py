"""Item E backend: Memory (CLAUDE.md) + Commands (slash-commands) services.

Covers path resolution across User/Project scopes, frontmatter round-tripping,
CRUD semantics (409/404/422, rename, traversal) and the list grouping.
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException


# --- Memory (CLAUDE.md) ------------------------------------------------------


def test_memory_user_roundtrip(tmp_path, monkeypatch):
    from priva_agent_runner.services import memory

    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))

    # Absent -> empty content, exists False.
    got = memory.read_memory("alice", "user", None)
    assert got.content == "" and got.exists is False
    assert got.path == str(cfg / "CLAUDE.md")

    saved = memory.write_memory("alice", "user", None, "# Rules\n- be terse\n")
    assert saved.exists is True
    reread = memory.read_memory("alice", "user", None)
    assert reread.content == "# Rules\n- be terse\n"
    assert (cfg / "CLAUDE.md").read_text() == "# Rules\n- be terse\n"


def test_memory_project_path_is_cwd_root(tmp_path):
    from priva_agent_runner.services import memory

    cwd = tmp_path / "proj"
    cwd.mkdir()
    saved = memory.write_memory("alice", "project", str(cwd), "project memory")
    # Project memory lives at the project ROOT, not under .claude/.
    assert saved.path == str(cwd / "CLAUDE.md")
    assert (cwd / "CLAUDE.md").read_text() == "project memory"


def test_memory_list_flags_existence(tmp_path, monkeypatch):
    from priva_agent_runner.services import memory
    import priva_agent_runner.services.mcp.config_manager as mcpcm

    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    (cfg / "CLAUDE.md").write_text("user mem")

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setattr(mcpcm, "list_user_workdirs", lambda u: [str(proj)])

    resp = memory.list_memory("alice")
    by_scope = {(s.scope, s.cwd): s for s in resp.scopes}
    assert by_scope[("user", None)].exists is True
    assert by_scope[("user", None)].size == len("user mem")
    assert by_scope[("project", str(proj))].exists is False  # no CLAUDE.md yet


def test_memory_invalid_scope_and_size(tmp_path, monkeypatch):
    from priva_agent_runner.services import memory

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    with pytest.raises(HTTPException) as e:
        memory.read_memory("alice", "local", None)
    assert e.value.status_code == 422

    with pytest.raises(HTTPException) as e2:
        memory.write_memory("alice", "user", None, "x" * (memory.MAX_MEMORY_BYTES + 1))
    assert e2.value.status_code == 413


# --- Commands (slash-commands) ----------------------------------------------


def test_command_create_get_roundtrip(tmp_path, monkeypatch):
    from priva_agent_runner.services import commands
    from priva_common.models.commands import CommandCreateRequest

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))

    detail = commands.create_command("alice", CommandCreateRequest(
        name="deploy",
        scope="user",
        description="Ship to prod",
        argument_hint="[env]",
        allowed_tools=["Bash(git push:*)", "Read"],
        prompt="Deploy to $1 now.",
    ))
    assert detail.name == "deploy"

    # File carries hyphenated frontmatter keys the CLI expects.
    path = tmp_path / "cfg" / "commands" / "deploy.md"
    raw = path.read_text()
    assert "argument-hint: '[env]'" in raw or "argument-hint: \"[env]\"" in raw or "argument-hint: [env]" in raw
    assert "allowed-tools: Bash(git push:*), Read" in raw

    got = commands.get_command("alice", "user", None, "deploy")
    assert got.description == "Ship to prod"
    assert got.argument_hint == "[env]"
    assert got.allowed_tools == ["Bash(git push:*)", "Read"]
    assert got.prompt.strip() == "Deploy to $1 now."


def test_command_allowed_tools_parses_list_or_string(tmp_path, monkeypatch):
    from priva_agent_runner.services import commands

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    cmd_dir = tmp_path / "cfg" / "commands"
    cmd_dir.mkdir(parents=True)
    # String form.
    (cmd_dir / "a.md").write_text("---\nallowed-tools: Bash, Read\n---\nbody\n")
    assert commands.get_command("alice", "user", None, "a").allowed_tools == ["Bash", "Read"]
    # YAML list form.
    (cmd_dir / "b.md").write_text("---\nallowed-tools:\n  - Write\n  - Edit\n---\nbody\n")
    assert commands.get_command("alice", "user", None, "b").allowed_tools == ["Write", "Edit"]


def test_command_list_groups_by_scope(tmp_path, monkeypatch):
    from priva_agent_runner.services import commands
    from priva_common.models.commands import CommandCreateRequest
    import priva_agent_runner.services.mcp.config_manager as mcpcm

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setattr(mcpcm, "list_user_workdirs", lambda u: [str(proj)])

    commands.create_command("alice", CommandCreateRequest(name="u1", scope="user", prompt="x"))
    commands.create_command("alice", CommandCreateRequest(name="p1", scope="project", cwd=str(proj), prompt="y"))

    resp = commands.list_commands("alice")
    by_name = {c.name: c for c in resp.commands}
    assert by_name["u1"].scope == "user" and by_name["u1"].cwd is None
    assert by_name["p1"].scope == "project" and by_name["p1"].cwd == str(proj)


def test_command_update_rename_and_delete(tmp_path, monkeypatch):
    from priva_agent_runner.services import commands
    from priva_common.models.commands import CommandCreateRequest, CommandUpdateRequest

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    commands.create_command("alice", CommandCreateRequest(name="old", scope="user", prompt="v1"))

    updated = commands.update_command("alice", "user", None, "old",
                                      CommandUpdateRequest(new_name="new", prompt="v2"))
    assert updated.name == "new" and updated.prompt.strip() == "v2"
    assert not (tmp_path / "cfg" / "commands" / "old.md").exists()
    assert (tmp_path / "cfg" / "commands" / "new.md").exists()

    commands.delete_command("alice", "user", None, "new")
    with pytest.raises(HTTPException) as e:
        commands.get_command("alice", "user", None, "new")
    assert e.value.status_code == 404


def test_command_validation_and_conflicts(tmp_path, monkeypatch):
    from priva_agent_runner.services import commands
    from priva_common.models.commands import CommandCreateRequest

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))

    # Bad name.
    with pytest.raises(HTTPException) as e:
        commands.create_command("alice", CommandCreateRequest(name="Bad Name!", scope="user"))
    assert e.value.status_code == 422

    # Duplicate.
    commands.create_command("alice", CommandCreateRequest(name="dup", scope="user", prompt="x"))
    with pytest.raises(HTTPException) as e2:
        commands.create_command("alice", CommandCreateRequest(name="dup", scope="user", prompt="y"))
    assert e2.value.status_code == 409

    # Path traversal in a get.
    with pytest.raises(HTTPException):
        commands.get_command("alice", "user", None, "../escape")


# --- Auto memory (Claude-written) -------------------------------------------


def _auto_dir(cwd):
    """Mirror the service's derivation for test assertions."""
    from claude_agent_sdk._internal.sessions import _canonicalize_path, _get_project_dir

    return _get_project_dir(_canonicalize_path(str(cwd))) / "memory"


def _pin_workdirs(monkeypatch, *cwds):
    import priva_agent_runner.services.mcp.config_manager as mcpcm

    monkeypatch.setattr(mcpcm, "list_user_workdirs", lambda u: [str(c) for c in cwds])


def test_auto_memory_list_and_read(tmp_path, monkeypatch):
    from priva_agent_runner.services import memory

    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    proj = tmp_path / "proj"
    proj.mkdir()
    _pin_workdirs(monkeypatch, proj)

    # Claude has written a memory dir with an index + a topic file (in reverse
    # order, to prove MEMORY.md is surfaced first).
    mem = _auto_dir(proj)
    mem.mkdir(parents=True)
    (mem / "debugging.md").write_text("flaky test notes")
    (mem / "MEMORY.md").write_text("# index\n- build: make\n")

    resp = memory.list_auto_memory("alice")
    assert len(resp.projects) == 1
    p = resp.projects[0]
    assert p.cwd == str(proj) and p.exists is True and p.enabled is True
    assert p.memory_dir == str(mem)
    # MEMORY.md (the index) sorts first and is flagged; topic files follow.
    assert [f.name for f in p.files] == ["MEMORY.md", "debugging.md"]
    assert p.files[0].is_index is True and p.files[1].is_index is False

    got = memory.read_auto_memory("alice", str(proj), "MEMORY.md")
    assert got.exists is True and "build: make" in got.content


def test_auto_memory_list_empty_and_missing_dir(tmp_path, monkeypatch):
    from priva_agent_runner.services import memory

    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    proj = tmp_path / "proj"
    proj.mkdir()
    _pin_workdirs(monkeypatch, proj)

    # No memory dir yet -> exists False, no files, still on by default.
    p = memory.list_auto_memory("alice").projects[0]
    assert p.exists is False and p.files == [] and p.enabled is True

    # Reading a not-yet-written file -> empty content, exists False (not an error).
    got = memory.read_auto_memory("alice", str(proj), "MEMORY.md")
    assert got.exists is False and got.content == ""


def test_auto_memory_edit_only_no_create(tmp_path, monkeypatch):
    from priva_agent_runner.services import memory

    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    proj = tmp_path / "proj"
    proj.mkdir()
    _pin_workdirs(monkeypatch, proj)
    mem = _auto_dir(proj)
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text("v1")

    saved = memory.write_auto_memory("alice", str(proj), "MEMORY.md", "v2")
    assert saved.content == "v2"
    assert (mem / "MEMORY.md").read_text() == "v2"

    # Writing a NON-existent file is a 404, not a silent create.
    with pytest.raises(HTTPException) as e:
        memory.write_auto_memory("alice", str(proj), "new-topic.md", "x")
    assert e.value.status_code == 404
    assert not (mem / "new-topic.md").exists()


def test_auto_memory_delete_and_validation(tmp_path, monkeypatch):
    from priva_agent_runner.services import memory

    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    proj = tmp_path / "proj"
    proj.mkdir()
    _pin_workdirs(monkeypatch, proj)
    mem = _auto_dir(proj)
    mem.mkdir(parents=True)
    (mem / "debugging.md").write_text("notes")

    memory.delete_auto_memory("alice", str(proj), "debugging.md")
    assert not (mem / "debugging.md").exists()

    # Traversal and non-.md names -> 422.
    for bad in ("../escape.md", "notmd.txt", "a/b.md"):
        with pytest.raises(HTTPException) as e:
            memory.read_auto_memory("alice", str(proj), bad)
        assert e.value.status_code == 422

    # A cwd the user does not own -> 404 (before any name check).
    with pytest.raises(HTTPException) as e2:
        memory.read_auto_memory("alice", str(tmp_path / "other"), "MEMORY.md")
    assert e2.value.status_code == 404


def test_auto_memory_toggle_per_project(tmp_path, monkeypatch):
    from priva_agent_runner.services import memory

    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    proj = tmp_path / "proj"
    proj.mkdir()
    _pin_workdirs(monkeypatch, proj)

    # Default on (no settings.json).
    assert memory.list_auto_memory("alice").projects[0].enabled is True

    memory.set_auto_memory_enabled("alice", str(proj), False)
    settings = proj / ".claude" / "settings.json"
    assert json.loads(settings.read_text())["autoMemoryEnabled"] is False
    assert memory.list_auto_memory("alice").projects[0].enabled is False

    # Toggling preserves every other key (e.g. project hooks).
    settings.write_text(json.dumps({"autoMemoryEnabled": False, "hooks": {"x": 1}}))
    memory.set_auto_memory_enabled("alice", str(proj), True)
    data = json.loads(settings.read_text())
    assert data["autoMemoryEnabled"] is True and data["hooks"] == {"x": 1}
