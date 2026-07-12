"""Hook Policy feature tests (docs/hooks-policy-design.md §8).

Covers the full backend slice on the in-process transport:
- data-spine seeding + seed_version upgrade (edited vs unedited rows)
- constructed hook env (secrets absent, deny-list wins, env refs)
- materializer (hash rewrite, tamper recovery, orphan cleanup)
- precedence (enforced > user pref > default_on) + native scoped user hooks (D5)
- executor protocol parity (exit 2, event gating, stdout JSON, timeout)
- control-panel REST semantics (create-disarmed, 409/404/422, seed diff)
- runner catalog routes (403 on enforced disable)

The gRPC wire for HookPolicyService is exercised by test_dataplane_grpc-style
roundtrips implicitly; transport parity is the dataplane suite's job.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

import pytest

# --- module-scoped isolated store + home -------------------------------------


@pytest.fixture(scope="module")
def spine(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("hook-policy")
    os.environ["PRIVA_DATASPINE__BACKEND"] = "sqlite"
    os.environ["PRIVA_DATASPINE__SQLITE_PATH"] = str(tmp / "spine.db")
    os.environ["PRIVA_DATASPINE__TRANSPORT"] = "in_process"
    os.environ["PRIVA_HOME"] = str(tmp / "home")

    from priva_common.config import get_settings

    get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None
    from priva_data_spine.service import compose

    client = compose()
    yield client


@pytest.fixture()
def workspace(tmp_path):
    ws = tmp_path / "ws" / "alice"
    ws.mkdir(parents=True)
    return str(ws)


# --- seeds & upgrade ----------------------------------------------------------


def test_seeds_present_and_shaped(spine):
    rows = {p.id: p for p in spine.hook_policies.list()}
    assert set(rows) >= {"block-dangerous-bash", "audit-tool-use",
                         "lint-on-write", "require-permission-risky-tools"}
    b = rows["block-dangerous-bash"]
    # rev-5: the three safety seeds ship enforced-by-default (native delivery).
    assert b.predefined and b.enabled and b.default_on and b.enforced
    assert b.hook_type == "command" and b.interpreter == "python3"
    assert b.events == ["PreToolUse"] and b.matcher == "Bash"
    assert len(b.content_hash) == 64
    assert rows["audit-tool-use"].enforced and rows["require-permission-risky-tools"].enforced
    # lint-on-write stays non-enforced (dev convenience, excluded from the CM)
    assert rows["lint-on-write"].default_on is False and not rows["lint-on-write"].enforced


def test_seed_upgrade_unedited_refreshes_edited_survives(spine):
    import priva_common.hook_seeds as hs
    import priva_data_spine.service as svc_mod
    from priva_common.dataplane import HookPolicyRecord
    from priva_data_spine.service import HookPolicyService

    hp = spine.hook_policies
    # mark audit-tool-use as admin-edited
    hp.upsert(HookPolicyRecord(id="audit-tool-use", script_body="# custom\n"),
              update_mask=["script_body"], expect="update")
    old_lint = hs.seed_by_id("lint-on-write")
    old_audit = hs.seed_by_id("audit-tool-use")
    # Bump past the shipped versions (audit already ships v2) so a real upgrade runs.
    new_seeds = (
        dataclasses.replace(old_lint, script_body=old_lint.script_body + "# v3\n",
                            seed_version=3, previous_hashes=(old_lint.hash,)),
        dataclasses.replace(old_audit, script_body="# v3 audit\n",
                            seed_version=3, previous_hashes=(old_audit.hash,)),
    )
    original = svc_mod.HOOK_SEEDS
    svc_mod.HOOK_SEEDS = new_seeds
    try:
        HookPolicyService(_repo_of(spine))  # re-run startup seeding
    finally:
        svc_mod.HOOK_SEEDS = original

    lint = hp.get("lint-on-write")
    audit = hp.get("audit-tool-use")
    assert lint.seed_version == 3 and lint.script_body.endswith("# v3\n")
    assert lint.updated_by == "seed-upgrade"
    # edited row (content_hash diverged) is untouched — keeps its content + version
    assert audit.script_body == "# custom\n" and audit.seed_version == 2

    # restore for the rest of the module
    HookPolicyService(_repo_of(spine))
    hp.upsert(HookPolicyRecord(id="audit-tool-use", script_body=old_audit.script_body),
              update_mask=["script_body"], expect="update")


def _repo_of(client):
    return client.hook_policies.repo


# --- constructed env ------------------------------------------------------------


def test_build_hook_env_denies_secrets(monkeypatch):
    from priva_agent_runner.services.hooks.env import build_hook_env, resolve_env_refs

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-leak")
    monkeypatch.setenv("PRIVA_AUTH__JWT_SECRET", "leak")
    monkeypatch.setenv("SOME_HMAC_KEY", "leak")
    monkeypatch.setenv("PG_DSN", "leak")
    monkeypatch.setenv("WEBHOOK_TOKEN", "tok")
    env = build_hook_env(
        ["WEBHOOK_TOKEN", "ANTHROPIC_API_KEY", "PRIVA_AUTH__JWT_SECRET", "SOME_HMAC_KEY", "PG_DSN"],
        extra={"CLAUDE_HOOK_EVENT_NAME": "PreToolUse"},
    )
    for denied in ("ANTHROPIC_API_KEY", "PRIVA_AUTH__JWT_SECRET", "SOME_HMAC_KEY", "PG_DSN"):
        assert denied not in env
    assert env["WEBHOOK_TOKEN"] == "tok" and "PATH" in env
    assert resolve_env_refs("Bearer $WEBHOOK_TOKEN/${WEBHOOK_TOKEN}", env) == "Bearer tok/tok"
    assert resolve_env_refs("$NOPE!", env) == "!"


# --- build_hooks: admin AND user hooks are native (D6) ---------------------------


def test_build_hooks_has_no_admin_or_user_hooks(spine, workspace):
    """After D6 the programmatic admin path is gone — admin hooks fire ONLY via
    the managed ConfigMap (native), user hooks ONLY via settings.json (native).
    build_hooks therefore emits just the system logger (+ PII when configured):
    NO PreToolUse admin callbacks, and a settings.json user hook is not injected."""
    from priva_agent_runner.services.hooks import policy as policy_mod
    from priva_agent_runner.services.hooks.builder import build_hooks

    policy_mod.invalidate_snapshot()

    hooks = build_hooks("alice", workspace)
    assert "PreToolUse" not in hooks  # no programmatic admin hooks anymore
    assert len(hooks["PostToolUse"]) == 1  # system execution logger only

    # A user hook in settings.json is native (CLI-run) — never injected here.
    claude_dir = Path(workspace) / ".claude"
    claude_dir.mkdir(exist_ok=True)
    (claude_dir / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]},
    ]}}))
    hooks2 = build_hooks("alice", workspace)
    assert "PreToolUse" not in hooks2


def test_legacy_enforced_purge(workspace):
    from priva_agent_runner.services.hooks.config_manager import HookConfigManager

    claude_dir = Path(workspace) / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings = claude_dir / "settings.json"
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "old"}],
         "__priva_enforced": True},
        {"matcher": "Write", "hooks": [{"type": "command", "command": "keep"}]},
    ]}, "env": {"X": "1"}}))
    HookConfigManager("alice").purge_legacy_enforced("project", workspace)
    data = json.loads(settings.read_text())
    assert data["env"] == {"X": "1"}  # non-hooks keys preserved
    assert [e["matcher"] for e in data["hooks"]["PreToolUse"]] == ["Write"]


# --- D5: native scoped user hooks (settings.json, no settings.local.json) -----


def test_write_scope_hooks_user_preserves_env(tmp_path, monkeypatch):
    """User-scope write is surgical — only the hooks key is touched, the BYOK
    env block survives, and the file stays 0600 (it holds the auth token)."""
    from priva_agent_runner.services.hooks.config_manager import HookConfigManager

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_dir))
    settings = cfg_dir / "settings.json"
    settings.write_text(json.dumps({"env": {"ANTHROPIC_AUTH_TOKEN": "sk-secret"}}))

    HookConfigManager("alice").write_scope_hooks("user", None, {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]},
    ]})
    data = json.loads(settings.read_text())
    assert data["env"] == {"ANTHROPIC_AUTH_TOKEN": "sk-secret"}  # preserved
    assert data["hooks"]["PreToolUse"][0]["matcher"] == "Bash"
    assert (settings.stat().st_mode & 0o777) == 0o600


def test_write_scope_hooks_project(tmp_path):
    from priva_agent_runner.services.hooks.config_manager import HookConfigManager

    cwd = tmp_path / "proj"
    cwd.mkdir()
    HookConfigManager("alice").write_scope_hooks("project", str(cwd), {"PostToolUse": [
        {"matcher": "Write", "hooks": [{"type": "command", "command": "fmt"}]},
    ]})
    data = json.loads((cwd / ".claude" / "settings.json").read_text())
    assert data["hooks"]["PostToolUse"][0]["hooks"][0]["command"] == "fmt"


def test_read_all_scopes(tmp_path, monkeypatch):
    """read_all -> user scope first (always), then each project workdir WITH
    hooks; hook-less workdirs are omitted."""
    from priva_agent_runner.services.hooks import config_manager as cm
    import priva_agent_runner.services.mcp.config_manager as mcpcm

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_dir))
    (cfg_dir / "settings.json").write_text(json.dumps({"hooks": {"SessionStart": [
        {"matcher": None, "hooks": [{"type": "command", "command": "user-hook"}]}]}}))

    proj = tmp_path / "proj"
    (proj / ".claude").mkdir(parents=True)
    (proj / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "proj-hook"}]}]}}))
    empty = tmp_path / "empty"
    (empty / ".claude").mkdir(parents=True)  # no hooks -> omitted

    monkeypatch.setattr(mcpcm, "list_user_workdirs", lambda u: [str(proj), str(empty)])

    scopes = cm.HookConfigManager("alice").read_all()
    assert scopes[0][0] == "user" and "SessionStart" in scopes[0][2]
    project = [(s, c) for s, c, _ in scopes if s == "project"]
    assert ("project", str(proj)) in project
    assert all(c != str(empty) for _, c in project)  # hook-less workdir omitted


def test_migrate_local_hooks(tmp_path, monkeypatch):
    """settings.local.json hooks move into project settings.json (project entries
    first), the local hooks key is cleared, and other keys are preserved on both
    sides. Idempotent."""
    from priva_agent_runner.services.hooks import config_manager as cm
    import priva_agent_runner.services.mcp.config_manager as mcpcm

    cwd = tmp_path / "proj"
    (cwd / ".claude").mkdir(parents=True)
    local = cwd / ".claude" / "settings.local.json"
    local.write_text(json.dumps({
        "hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "local-hook"}]}]},
        "permissions": {"defaultMode": "acceptEdits"},  # non-hooks key preserved
    }))
    settings = cwd / ".claude" / "settings.json"
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Write", "hooks": [{"type": "command", "command": "proj-hook"}]}]},
        "env": {"K": "v"}}))

    monkeypatch.setattr(mcpcm, "list_user_workdirs", lambda u: [str(cwd)])

    assert cm.migrate_local_hooks("alice") == 1
    merged = json.loads(settings.read_text())
    cmds = [h["hooks"][0]["command"] for h in merged["hooks"]["PreToolUse"]]
    assert cmds == ["proj-hook", "local-hook"]  # project first, migrated local after
    assert merged["env"] == {"K": "v"}  # preserved

    ldata = json.loads(local.read_text())
    assert "hooks" not in ldata  # cleared
    assert ldata["permissions"] == {"defaultMode": "acceptEdits"}  # preserved

    assert cm.migrate_local_hooks("alice") == 0  # idempotent


# --- control-panel REST -----------------------------------------------------------


@pytest.fixture()
def cp_client(spine):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from priva_common.user_store import UserRecord
    from priva_control_panel.routers.hook_policy import router
    from priva_control_panel.services.auth import require_admin

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_admin] = lambda: UserRecord(
        username="admin", password_hash="", role="admin")
    return TestClient(app)


def test_cp_rest_semantics(cp_client, spine):
    r = cp_client.get("/api/admin/hook-policy")
    assert r.status_code == 200
    items = {i["id"]: i for i in r.json()["items"]}
    assert items["block-dangerous-bash"]["seed_state"] == "current"

    # create is disarmed; duplicate 409; mcp_tool 422
    r = cp_client.post("/api/admin/hook-policy", json={
        "id": "cp-hook", "hook_type": "http", "name": "n", "description": "描述",
        "events": ["Stop"], "url": "https://x.example/h"})
    assert r.status_code == 201 and r.json()["enabled"] is False
    assert r.json()["timeout_seconds"] == 5
    assert cp_client.post("/api/admin/hook-policy", json={
        "id": "cp-hook", "hook_type": "http", "name": "n", "description": "d",
        "events": ["Stop"], "url": "https://x.example/h"}).status_code == 409
    assert cp_client.post("/api/admin/hook-policy", json={
        "id": "m", "hook_type": "mcp_tool", "name": "n", "description": "d",
        "events": ["Stop"]}).status_code == 422

    # validation matrix
    v = cp_client.post("/api/admin/hook-policy/validate", json={
        "hook_type": "command", "interpreter": "python3",
        "script_body": "def broken(:\n  pass", "events": ["BadEvent"],
        "name": "", "description": "", "timeout_seconds": 9999}).json()
    fields = {e["field"] for e in v["errors"]}
    assert {"script_body", "events", "name", "description", "timeout_seconds"} <= fields
    assert next(e["line"] for e in v["errors"] if e["field"] == "script_body") == 1

    # masked PUT + merged validation + 404
    assert cp_client.put("/api/admin/hook-policy/cp-hook",
                         json={"enforced": True}).json()["enforced"] is True
    assert cp_client.put("/api/admin/hook-policy/cp-hook",
                         json={"url": "ftp://bad"}).status_code == 422
    assert cp_client.put("/api/admin/hook-policy/ghost",
                         json={"enabled": True}).status_code == 404

    # delete semantics
    assert cp_client.delete("/api/admin/hook-policy/audit-tool-use").status_code == 409
    assert cp_client.delete("/api/admin/hook-policy/cp-hook").status_code == 204
    assert cp_client.delete("/api/admin/hook-policy/cp-hook").status_code == 404

    # shipped-seed diff endpoint
    r = cp_client.get("/api/admin/hook-policy/lint-on-write/seed")
    assert r.status_code == 200 and "ruff" in r.json()["script_body"]


# --- runner catalog routes ----------------------------------------------------------


@pytest.fixture()
def runner_client(spine, workspace):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import priva_agent_runner.routers.hooks as hooks_router_mod
    from priva_agent_runner.deps import require_user
    from priva_common.user_store import UserRecord

    app = FastAPI()
    app.include_router(hooks_router_mod.router)
    app.dependency_overrides[require_user] = lambda: UserRecord(
        username="alice", password_hash="", role="user")
    return TestClient(app)


def test_runner_catalog_readonly(runner_client, spine):
    from priva_common.dataplane import HookPolicyRecord
    from priva_agent_runner.services.hooks import policy as policy_mod

    hp = spine.hook_policies
    hp.upsert(HookPolicyRecord(id="block-dangerous-bash", enforced=True),
              update_mask=["enforced"], expect="update")
    policy_mod.invalidate_snapshot()

    r = runner_client.get("/api/sandbox/hooks/catalog")
    assert r.status_code == 200
    rows = {e["id"]: e for e in r.json()}
    assert rows["block-dangerous-bash"]["enforced"] is True
    assert rows["block-dangerous-bash"]["enabled"] is True  # enabled mirrors enforced
    assert "script_body" not in rows["block-dangerous-bash"]  # no body exposure

    # The per-user enable/disable endpoints are GONE (D6) — admin hooks are
    # enforced-only and natively delivered, so the catalog is read-only.
    assert runner_client.post(
        "/api/sandbox/hooks/catalog/block-dangerous-bash/disable").status_code == 404
    assert runner_client.post(
        "/api/sandbox/hooks/catalog/lint-on-write/enable").status_code == 404

    hp.upsert(HookPolicyRecord(id="block-dangerous-bash", enforced=False),
              update_mask=["enforced"], expect="update")
    policy_mod.invalidate_snapshot()
