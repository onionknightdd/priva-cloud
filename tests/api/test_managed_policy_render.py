"""Render enforced hook policies -> claude-managed-policy ConfigMap data (rev-5 D2)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from priva_common import managed_policy_render as R


def _policy(**kw):
    base = dict(
        id="block-dangerous-bash",
        hook_type="command",
        events=["PreToolUse"],
        matcher="Bash",
        interpreter="python3",
        script_body="import sys; sys.exit(0)\n",
        content_hash="a1b2c3d4e5f6",
        timeout_seconds=30,
        allowed_env_vars=[],
        url="",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_renders_settings_scripts_and_wrapper():
    data = R.render_config_map_data([_policy()])
    assert R.WRAPPER_KEY in data and "build_scrubbed_env" in data[R.WRAPPER_KEY]

    key = "mh-command-block-dangerous-bash-a1b2c3d4.py"
    assert key in data and data[key].startswith("import sys")

    settings = json.loads(data[R.SETTINGS_KEY])
    entry = settings["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == "Bash"
    cmd = entry["hooks"][0]["command"]
    assert cmd == f"python3 /etc/claude-code/_wrapper.py block-dangerous-bash python3 /etc/claude-code/{key}"
    assert entry["hooks"][0]["timeout"] == 30


def test_bash_ext_and_allowed_env_and_multi_event():
    p = _policy(
        id="audit-tool-use",
        interpreter="bash",
        events=["PreToolUse", "PostToolUse"],
        matcher="",
        allowed_env_vars=["FOO", "BAR"],
        content_hash="deadbeef99",
    )
    data = R.render_config_map_data([p])
    key = "mh-command-audit-tool-use-deadbeef.sh"
    assert key in data
    settings = json.loads(data[R.SETTINGS_KEY])
    # appears under both events
    assert "PreToolUse" in settings["hooks"] and "PostToolUse" in settings["hooks"]
    cmd = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert cmd.endswith(f"/etc/claude-code/{key} FOO BAR")
    # no matcher key when matcher empty
    assert "matcher" not in settings["hooks"]["PreToolUse"][0]


def test_http_and_empty_body_skipped():
    http = _policy(id="notify", hook_type="http", url="https://x", script_body="")
    empty = _policy(id="blank", script_body="")
    data = R.render_config_map_data([http, empty])
    settings = json.loads(data[R.SETTINGS_KEY])
    assert settings.get("hooks", {}) == {}  # nothing renderable
    assert not any(k.startswith("mh-") for k in data)


def test_baseline_merges_but_hooks_win():
    data = R.render_config_map_data(
        [_policy()], baseline={"cleanupPeriodDays": 30, "permissions": {"deny": ["Bash(rm -rf /*)"]}}
    )
    settings = json.loads(data[R.SETTINGS_KEY])
    assert settings["cleanupPeriodDays"] == 30
    assert settings["permissions"]["deny"] == ["Bash(rm -rf /*)"]
    assert "PreToolUse" in settings["hooks"]


def test_command_is_shell_safe():
    p = _policy(id="weird", allowed_env_vars=["A B", "C;D"])
    cmd = R.render_command(p, R.script_key(p))
    # dangerous env names are quoted, not interpolated raw
    assert "'A B'" in cmd and "'C;D'" in cmd


def test_content_digest_stable_and_sensitive():
    d1 = R.render_config_map_data([_policy()])
    d2 = R.render_config_map_data([_policy()])
    assert R.content_digest(d1) == R.content_digest(d2)
    d3 = R.render_config_map_data([_policy(script_body="import sys; sys.exit(1)\n", content_hash="ffff0000")])
    assert R.content_digest(d1) != R.content_digest(d3)


def test_merge_generations_keeps_prior_script():
    new = R.render_config_map_data([_policy(content_hash="bbbb1111")])
    old = R.render_config_map_data([_policy(content_hash="aaaa0000")])
    merged = R.merge_generations(new, old, keep=2)
    assert "mh-command-block-dangerous-bash-bbbb1111.py" in merged  # new gen
    assert "mh-command-block-dangerous-bash-aaaa0000.py" in merged  # prior gen retained
    # settings + wrapper are always the new generation
    assert merged[R.SETTINGS_KEY] == new[R.SETTINGS_KEY]


def test_merge_generations_drops_removed_policy_scripts():
    new = R.render_config_map_data([_policy(id="keep", content_hash="1111")])
    old = R.render_config_map_data([
        _policy(id="keep", content_hash="0000"),
        _policy(id="gone", content_hash="9999"),
    ])
    merged = R.merge_generations(new, old, keep=2)
    assert not any("gone" in k for k in merged)  # fully-removed policy pruned
