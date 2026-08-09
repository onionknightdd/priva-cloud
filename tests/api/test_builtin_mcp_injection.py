from __future__ import annotations

import asyncio

import pytest
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

from priva_agent_runner.services.claude_sdk import options as options_module


def _build_options(monkeypatch, tmp_path, **overrides):
    from priva_agent_runner.services import sandbox_venv, skills
    from priva_agent_runner.services.hooks import builder as hooks_builder
    from priva_common import user_store

    class RuntimeStore:
        def get_runtime_config(self):
            return {}

    runtime_settings = overrides.pop(
        "_runtime_settings",
        {
            "extra_env_enabled": False,
            "extra_env": {},
            "prompt_suggestion_enabled": False,
        },
    )
    monkeypatch.setattr(
        options_module,
        "read_runtime_settings",
        lambda: runtime_settings,
    )
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


def _cli_command(options):
    transport = SubprocessCLITransport("test", options)
    transport._cli_path = "/usr/bin/claude"
    return transport._build_command()


def test_agent_mode_passes_explicit_empty_system_prompt(monkeypatch, tmp_path):
    options = _build_options(monkeypatch, tmp_path, run_mode="agent")

    command = _cli_command(options)

    index = command.index("--system-prompt")
    assert command[index + 1] == ""
    assert "--append-system-prompt" not in command


def test_code_mode_uses_unmodified_claude_code_preset(monkeypatch, tmp_path):
    options = _build_options(monkeypatch, tmp_path, run_mode="code")

    command = _cli_command(options)

    assert options.system_prompt == {"type": "preset", "preset": "claude_code"}
    assert "--system-prompt" not in command
    assert "--append-system-prompt" not in command


def test_dead_tools_and_request_denylist_are_combined(monkeypatch, tmp_path):
    options = _build_options(
        monkeypatch,
        tmp_path,
        extra_disallowed_tools=["Bash", "PushNotification"],
    )

    for tool in (
        "PushNotification",
        "DesignSync",
        "ScheduleWakeup",
        "ReportFindings",
        "Bash",
    ):
        assert tool in options.disallowed_tools
    assert options.disallowed_tools.count("PushNotification") == 1


def test_streaming_prompt_suggestion_requires_server_toggle(monkeypatch, tmp_path):
    enabled = _build_options(
        monkeypatch,
        tmp_path,
        enable_prompt_suggestions=True,
        _runtime_settings={
            "extra_env_enabled": True,
            "extra_env": {"MY_RUNTIME_VALUE": "enabled"},
            "prompt_suggestion_enabled": True,
        },
    )

    assert enabled.env["MY_RUNTIME_VALUE"] == "enabled"
    assert enabled.env["CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION"] == "true"
    assert "ANTHROPIC_AUTH_TOKEN" not in enabled.env
    assert "ANTHROPIC_BASE_URL" not in enabled.env
    assert enabled.extra_args["prompt-suggestions"] == "true"
    assert enabled._priva_prompt_suggestion_enabled is True

    disabled = _build_options(
        monkeypatch,
        tmp_path,
        enable_prompt_suggestions=True,
        _runtime_settings={
            "extra_env_enabled": True,
            "extra_env": {"MY_RUNTIME_VALUE": "enabled"},
            "prompt_suggestion_enabled": False,
        },
    )

    assert "CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION" not in disabled.env
    assert "prompt-suggestions" not in disabled.extra_args
    assert disabled._priva_prompt_suggestion_enabled is False
