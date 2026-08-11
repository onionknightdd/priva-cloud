from __future__ import annotations

from claude_agent_sdk.types import SystemPromptPreset

from priva_common.models.agent import RunMode
from priva_common.paths import claude_config_dir


def wrap_system_reminder(content: str) -> str:
    """Wrap one platform-owned prompt injection as a system reminder."""
    normalized = content.strip()
    if not normalized:
        raise ValueError("system reminder content must not be empty")
    return f"<system-reminder>\n{normalized}\n</system-reminder>"


def _home_directory_reminder() -> str:
    config_dir = str(claude_config_dir())
    return (
        "Important: Due to the system's filesystem layout, the path referenced "
        "by the `$HOME` environment variable is not suitable or recommended for "
        "handling any request involving a user's home directory. For every such "
        "request, use the path referenced by `$CLAUDE_CONFIG_DIR` instead. In this "
        f"runtime, `$CLAUDE_CONFIG_DIR` is set to `{config_dir}`. Claude Code CLI "
        "global configuration and all related harness-managed user resources are "
        "rooted under `$CLAUDE_CONFIG_DIR`. Do not use `$HOME`, `~`, or paths "
        "derived from them as substitutes for the user's home directory."
    )


def build_injected_system_prompt() -> str:
    """Render every platform-owned injection with an explicit reminder boundary."""
    reminders = (_home_directory_reminder(),)
    return "\n\n".join(wrap_system_reminder(content) for content in reminders)


def build_run_system_prompt(run_mode: RunMode) -> str | SystemPromptPreset:
    """Build the shared injection without changing either run mode's base semantics."""
    prompt = build_injected_system_prompt()
    if run_mode == "agent":
        return prompt
    if run_mode == "code":
        return {
            "type": "preset",
            "preset": "claude_code",
            "append": prompt,
        }
    raise ValueError(f"Invalid run mode: {run_mode!r}")
