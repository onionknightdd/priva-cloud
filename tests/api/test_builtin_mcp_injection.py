from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from priva_agent_runner.services.claude_sdk import options as options_module


def _build_options(monkeypatch, tmp_path, **overrides):
    from priva_agent_runner.services import priva_plugin, sandbox_venv, skills
    from priva_agent_runner.services.hooks import builder as hooks_builder
    from priva_common import user_store

    class RuntimeStore:
        def get_runtime_config(self):
            return {}

    class PluginManager:
        async def execute_all(self, username, runtime):
            return SimpleNamespace(system_prompt_append=None)

    monkeypatch.setattr(options_module, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(
        options_module,
        "read_settings_env",
        lambda: {
            "ANTHROPIC_BASE_URL": "https://example.invalid",
            "ANTHROPIC_AUTH_TOKEN": "test-token",
            "ANTHROPIC_MODEL": "test-model",
        },
    )
    monkeypatch.setattr(sandbox_venv, "venv_env_overlay", lambda _env: {})
    monkeypatch.setattr(skills, "compute_enabled_skill_names", lambda _username: [])
    monkeypatch.setattr(user_store, "get_user_store", lambda: RuntimeStore())
    monkeypatch.setattr(priva_plugin, "get_plugin_manager", lambda: PluginManager())
    monkeypatch.setattr(hooks_builder, "build_hooks", lambda *_args, **_kwargs: {})

    kwargs = {
        "username": "alice",
        "cwd": str(tmp_path),
        "auth_method": "jwt",
        "mcp_servers": "auto",
    }
    kwargs.update(overrides)
    return asyncio.run(options_module.build_agent_options(**kwargs))


def test_injects_current_builtin_mcp_names(monkeypatch, tmp_path):
    options = _build_options(
        monkeypatch,
        tmp_path,
        inject_scheduler_tools=True,
    )

    assert set(options.mcp_servers) == {"FileCanvas", "Scheduler"}
    assert options.mcp_servers["FileCanvas"]["name"] == "FileCanvas"
    assert options.mcp_servers["Scheduler"]["name"] == "Scheduler"
    assert options.allowed_tools == ["mcp__Scheduler__*", "mcp__FileCanvas__*"]


def test_injects_vision_only_for_run_scoped_image_paths(monkeypatch, tmp_path):
    from priva_agent_runner.services.llm_profiles import ResolvedProfile
    from priva_common.models.llm_profiles import LlmProfile

    profile = LlmProfile(
        id="profile-a",
        label="Profile A",
        base_url="https://example.invalid",
        auth_token="secret",
        default_model="text-model",
        vision_model="vision-model",
    )
    monkeypatch.setattr(
        options_module,
        "resolve_model",
        lambda _reference: ResolvedProfile(profile=profile, model="text-model"),
    )

    options = _build_options(
        monkeypatch,
        tmp_path,
        vision_image_paths=[str(tmp_path / "image.png")],
    )

    assert set(options.mcp_servers) == {"FileCanvas", "Vision"}
    assert options.mcp_servers["Vision"]["name"] == "Vision"
    assert "mcp__Vision__*" in options.allowed_tools


@pytest.mark.parametrize(
    "blocked_name",
    ["mcp__FileCanvas__*", "mcp__FileCanvas__register_file"],
)
def test_current_file_canvas_denylist_prevents_injection(
    monkeypatch,
    tmp_path,
    blocked_name,
):
    options = _build_options(
        monkeypatch,
        tmp_path,
        extra_disallowed_tools=[blocked_name],
    )

    assert "FileCanvas" not in (options.mcp_servers or {})
    assert blocked_name in options.disallowed_tools
    assert "mcp__FileCanvas__*" not in options.allowed_tools


@pytest.mark.parametrize(
    "tool_name",
    [
        "mcp__FileCanvas__register_file",
        "mcp__Scheduler__scheduler_list_jobs",
        "mcp__Vision__image_read",
    ],
)
def test_current_builtin_names_pass_subagent_tool_validation(tool_name):
    from priva_agent_runner.services.subagents import _validate_tool

    _validate_tool(tool_name)
