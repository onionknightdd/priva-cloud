from __future__ import annotations

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class HookHandler(BaseModel):
    """A single hook handler definition, matching Claude Code's native format.

    Types follow the hook-policy model: command | http | mcp_tool (mcp_tool is
    schema-reserved — no executor in v1). The legacy prompt/agent types were
    never executed by the Priva engine and are gone.
    """

    type: Literal["command", "http", "mcp_tool"]
    command: str | None = None
    url: str | None = None
    timeout: int = 30
    headers: dict[str, str] | None = None
    allowedEnvVars: list[str] | None = None


class HookEntry(BaseModel):
    """A hook entry binding a matcher pattern to one or more handlers."""

    matcher: str | None = None
    hooks: list[HookHandler]


class HookConfig(BaseModel):
    """Full hooks config matching Claude Code's .claude/settings.json format."""

    hooks: dict[str, list[HookEntry]]


class HookCatalogEntry(BaseModel):
    """An admin hook policy as shown to USERS (no script body — the old
    ``source_code`` exposure is gone deliberately)."""

    id: str
    name: str
    description: str  # zh content, shown verbatim
    hook_type: str  # "command" | "http" | "mcp_tool"
    events: list[str] = Field(default_factory=list)
    matcher: str = ""
    enforced: bool = False  # locked on — user cannot toggle
    default_on: bool = False
    enabled: bool = False  # effective state for THIS user
    predefined: bool = False


class HookTestRequest(BaseModel):
    """Request to dry-run a hook handler with sample input."""

    event_type: str
    handler: HookHandler
    input_json: dict = Field(default_factory=dict)


class HookTestResponse(BaseModel):
    """Result of a hook dry-run."""

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int


class HookLogEntry(BaseModel):
    """A single hook execution log record."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    timestamp: str
    event_type: str
    matcher: str | None = None
    handler_type: str
    hook_id: str | None = None  # policy id for admin hooks, "user" for user hooks
    exit_code: int
    duration_ms: int
    tool_name: str | None = None
    error: str | None = None


class HookLogsResponse(BaseModel):
    """Cursor-paginated response for hook execution logs."""

    entries: list[HookLogEntry]
    next_cursor: str | None = None
    prev_cursor: str | None = None
    total: int | None = None
    limit: int
