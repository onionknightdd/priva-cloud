from __future__ import annotations

import pytest
from fastapi import HTTPException

from priva_agent_runner.services import subagents


EXPECTED_BUILTINS = [
    "Read",
    "Write",
    "Edit",
    "Bash",
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskUpdate",
    "SendMessage",
]


def test_subagent_builtin_catalog_is_the_approved_set():
    assert subagents.BUILTIN_TOOL_CATALOG == EXPECTED_BUILTINS


@pytest.mark.parametrize(
    "tool",
    [
        "Agent",
        "Task",
        "DesignSync",
        "PushNotification",
        "ScheduleWakeup",
        "ReportFindings",
        "Grep",
        "Glob",
        "TodoWrite",
    ],
)
def test_removed_or_recursive_tools_cannot_be_saved(tool):
    with pytest.raises(HTTPException) as exc:
        subagents._validate_tool(tool)
    assert exc.value.status_code == 422


def test_legacy_agent_frontmatter_is_normalized_on_read(tmp_path):
    path = tmp_path / "legacy.md"
    path.write_text(
        """---
name: legacy
description: Old tool configuration
tools: [Read, Grep, Glob, TodoWrite, Agent, Task, PushNotification, SendMessage]
disallowedTools: [Grep, Glob, TodoWrite]
---

Do the work.
""",
        encoding="utf-8",
    )

    detail = subagents._parse_agent_md(path)

    assert detail.tools == ["Read", "SendMessage"]
    assert detail.disallowedTools == []
