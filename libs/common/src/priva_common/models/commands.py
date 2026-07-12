"""Slash-command (custom command) models.

Custom commands are Markdown files the CLI discovers under ``.claude/commands/``:

- ``user``    -> ``$CLAUDE_CONFIG_DIR/commands/<name>.md``  (every project)
- ``project`` -> ``{cwd}/.claude/commands/<name>.md``       (that workdir)

The file's frontmatter carries ``description``, ``argument-hint``,
``allowed-tools`` and optional ``model``; the body is the prompt template
(supports ``$1``/``$ARGUMENTS``, ``!`` bash, ``@`` file refs). Invoked as
``/<name>`` in a session. Mirrors the sub-agent scope model.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CommandScope = Literal["user", "project"]


class CommandSummary(BaseModel):
    name: str
    description: str = ""
    scope: CommandScope = "project"
    cwd: str | None = None


class CommandListResponse(BaseModel):
    commands: list[CommandSummary]


class CommandDetail(BaseModel):
    name: str
    description: str = ""
    scope: CommandScope = "project"
    cwd: str | None = None
    argument_hint: str = ""
    allowed_tools: list[str] = Field(default_factory=list)
    model: str | None = None
    prompt: str = ""  # the command body (prompt template)


class CommandCreateRequest(BaseModel):
    name: str
    scope: CommandScope = "project"
    cwd: str | None = None
    description: str = ""
    argument_hint: str = ""
    allowed_tools: list[str] = Field(default_factory=list)
    model: str | None = None
    prompt: str = ""


class CommandUpdateRequest(BaseModel):
    new_name: str | None = None
    description: str | None = None
    argument_hint: str | None = None
    allowed_tools: list[str] | None = None
    model: str | None = None
    prompt: str | None = None
