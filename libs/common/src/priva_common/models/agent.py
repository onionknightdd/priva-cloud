from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

PermissionMode = Literal["default", "acceptEdits", "plan", "bypassPermissions"]
PermissionDecision = Literal["allow", "deny"]


class AttachmentItem(BaseModel):
    path: str
    name: str | None = None


class ImageItem(BaseModel):
    data: str
    media_type: str
    filename: str | None = None


class AgentRunRequest(BaseModel):
    message: str = Field(min_length=1)
    session_id: str | None = None
    cwd: str | None = Field(
        default=None,
        description=(
            "Working directory for the run. Honored for NEW sessions only; "
            "on resume the session's recorded cwd is used and this is ignored."
        ),
    )
    add_dirs: list[str] | None = Field(
        default=None,
        description=(
            "Additional directories the agent may access (SDK --add-dir). "
            "Omit (null) to recover the session's stored set; pass a list to override."
        ),
    )
    permission_mode: PermissionMode | None = None
    model: str | None = None
    attachments: list[AttachmentItem] | None = None
    images: list[ImageItem] | None = None
    mcp_servers: str | list[str] | None = Field(
        default="auto",
        description=(
            "'auto' or omit: use all configured MCP servers. "
            "'disable'/null/[]: disable all MCP. "
            "['srv-A','srv-B']: use specific servers only."
        ),
    )
    enable_file_checkpointing: bool = False
    fork_session: bool = False
    enable_permission_feedback: bool = Field(
        default=False,
        description=(
            "Honored by /api/agent/run/stream only. False (default): the "
            "AskUserQuestion tool is removed and risky/gated tools are "
            "auto-denied, so the run never blocks waiting on a human. "
            "True: synchronous AskUserQuestion / risky-tool prompts (the "
            "caller must read the stream and POST /api/agent/permission/respond)."
        ),
    )


class PermissionRespondRequest(BaseModel):
    session_id: str
    request_id: str
    decision: PermissionDecision
    message: str | None = None
    updated_input: dict[str, Any] | None = None


class TextContentBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ThinkingContentBlock(BaseModel):
    type: Literal["thinking"] = "thinking"
    thinking: str
    signature: str | None = None


class ToolUseContentBlock(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any]


class ToolResultContentBlock(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: Any
    is_error: bool = False


SerializedContentBlock = Annotated[
    TextContentBlock | ThinkingContentBlock | ToolUseContentBlock | ToolResultContentBlock,
    Field(discriminator="type"),
]


class AssistantMessagePayload(BaseModel):
    type: Literal["assistant"] = "assistant"
    model: str | None = None
    content: list[SerializedContentBlock] = Field(default_factory=list)
    parent_tool_use_id: str | None = None
    error: str | None = None
    is_synthetic: bool | None = None


class RetryAttemptEvent(BaseModel):
    type: Literal["retry_attempt"] = "retry_attempt"
    attempt: int
    max_attempts: int
    delay_seconds: float
    error_code: str | None = None
    message: str | None = None


class RetryExhaustedEvent(BaseModel):
    type: Literal["retry_exhausted"] = "retry_exhausted"
    attempts: int
    error_code: str | None = None
    message: str | None = None
    raw_detail: str | None = None
    api_error_status: int | None = None


class StreamErrorEvent(BaseModel):
    type: Literal["stream_error"] = "stream_error"
    code: str
    message: str
    fatal: bool = True
    api_error_status: int | None = None


class RateLimitStatusEvent(BaseModel):
    type: Literal["rate_limit_status"] = "rate_limit_status"
    status: str | None = None
    resets_at: str | None = None
    utilization: float | None = None
    rate_limit_type: str | None = None


class HookEventPayload(BaseModel):
    """Lifecycle event emitted by the CLI when ``include_hook_events`` is on.

    Only ``PreToolUse`` and ``PostToolUse`` events flow through the SSE stream;
    other hook events stay log-only (see hooks/log_store.py).
    """

    type: Literal["hook_event"] = "hook_event"
    subtype: str
    hook_event_name: str
    session_id: str | None = None
    uuid: str | None = None
    data: dict[str, Any] | None = None


class SessionInfoResponse(BaseModel):
    session_id: str
    summary: str
    last_modified: int
    file_size: int
    custom_title: str | None = None
    first_prompt: str | None = None
    git_branch: str | None = None
    cwd: str | None = None
    session_source: str | None = None
    tag: str | None = None
    pinned: bool = False
    archived: bool = False
    parent_session_id: str | None = None
    parent_message_uuid: str | None = None
    fork_count: int = 0
    # Scheduler-origin sessions (D3): the sidebar marks these ⏰. Filled from
    # the runner's session-meta scheduler index; None for interactive sessions.
    origin: str | None = None
    scheduler_job_name: str | None = None


class SessionMessageResponse(BaseModel):
    type: Literal["user", "assistant"]
    uuid: str
    session_id: str
    message: Any
    parent_tool_use_id: str | None = None
    metadata: dict[str, Any] | None = None


class SessionListResponse(BaseModel):
    sessions: list[SessionInfoResponse]
    total: int = 0
    limit: int = 20
    offset: int = 0


class SessionMessagesResponse(BaseModel):
    messages: list[SessionMessageResponse]
    # The session's stored additional directories (SDK --add-dir), recovered
    # from the server-side sidecar so the UI chip hydrates on open/resume.
    add_dirs: list[str] = Field(default_factory=list)


class SessionGroupResponse(BaseModel):
    """One cwd's slice of the grouped sessions list."""
    cwd: str
    total: int
    sessions: list[SessionInfoResponse]
    has_more: bool = False
    last_activity: int = 0
    pinned: bool = False  # workdir-level pin (floats the group toward the top)


class GroupedSessionListResponse(BaseModel):
    """Sessions grouped by cwd, groups sorted by recent activity.

    The ``active_cwd`` group (the user's current workspace) is pinned first.
    """
    groups: list[SessionGroupResponse] = Field(default_factory=list)
    active_cwd: str | None = None


class FlatSessionListResponse(BaseModel):
    """One cwd's paginated page — backs the per-group 'more in this dir' loader."""
    cwd: str
    sessions: list[SessionInfoResponse]
    total: int = 0
    limit: int = 20
    offset: int = 0


class AgentRunResponse(BaseModel):
    type: str = "result"
    messages: list[AssistantMessagePayload] = Field(default_factory=list)
    session_id: str | None = None
    is_error: bool = False
    num_turns: int = 0
    duration_ms: int = 0
    duration_api_ms: int = 0
    stop_reason: str | None = None
    total_cost_usd: float | None = None
    usage: dict[str, Any] | None = None
    result: str | None = None
    attempts: int = 1
    retried_due_to: str | None = None
    api_error_status: int | None = None


# WebSocket frame models

class WsInitFrame(BaseModel):
    """First message from client — typed as 'init' for discriminated union."""
    type: Literal["init"] = "init"
    token: str | None = None
    x_user_name: str | None = None
    message: str = Field(min_length=1)
    session_id: str | None = None
    cwd: str | None = None
    add_dirs: list[str] | None = None
    permission_mode: PermissionMode | None = None
    model: str | None = None
    attachments: list[AttachmentItem] | None = None
    images: list[ImageItem] | None = None
    mcp_servers: str | list[str] | None = Field(
        default="auto",
        description=(
            "'auto' or omit: use all configured MCP servers. "
            "'disable'/null/[]: disable all MCP. "
            "['srv-A','srv-B']: use specific servers only."
        ),
    )
    enable_file_checkpointing: bool = False
    fork_session: bool = False
    enable_permission_feedback: bool = False


class WsPermissionFrame(BaseModel):
    """Permission response from client."""
    type: Literal["permission_response"]
    request_id: str
    decision: PermissionDecision
    message: str | None = None
    updated_input: dict[str, Any] | None = None


class WsAbortFrame(BaseModel):
    """Abort signal from client."""
    type: Literal["abort"]


class WsQueueFrame(BaseModel):
    """Mid-stream user message queued for injection at the next tool-result boundary."""
    type: Literal["queue"]
    id: str
    text: str
    attachments: list[AttachmentItem] | None = None
    images: list[ImageItem] | None = None


class WsQueueCancelFrame(BaseModel):
    """Cancel a previously queued message by id before it is delivered to the model."""
    type: Literal["queue_cancel"]
    id: str


class WsAttachFrame(BaseModel):
    """Attach to a run already executing in the RunRegistry (page refresh /
    reconnect). The server replays buffered events with seq > since_seq, then
    follows live. Identified by session_id and/or run_id."""
    type: Literal["attach"]
    token: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    since_seq: int = 0
    client_tab_id: str | None = None


WsClientFrame = Annotated[
    WsInitFrame | WsAttachFrame | WsPermissionFrame | WsAbortFrame | WsQueueFrame | WsQueueCancelFrame,
    Field(discriminator="type"),
]


class RewindRequest(BaseModel):
    session_id: str
    checkpoint_uuid: str


class RewindResponse(BaseModel):
    status: Literal["ok", "error"]
    message: str | None = None


class ForkRequest(BaseModel):
    session_id: str
    up_to_message_uuid: str | None = None
    title: str | None = None


class ForkResponse(BaseModel):
    new_session_id: str
    parent_session_id: str
    title: str | None = None


class RenameRequest(BaseModel):
    title: str = Field(min_length=1)


class TagRequest(BaseModel):
    tag: str | None = None


class AddDirsRequest(BaseModel):
    add_dirs: list[str] = Field(default_factory=list)


class PinRequest(BaseModel):
    pinned: bool


class ArchiveRequest(BaseModel):
    archived: bool


class WorkdirPinRequest(BaseModel):
    cwd: str
    pinned: bool


class WorkdirArchiveRequest(BaseModel):
    cwd: str


class ArchivedSessionListResponse(BaseModel):
    """Flat list of every archived session (across all cwds) for the
    Settings → Archived panel; each carries its ``cwd`` for display."""
    sessions: list[SessionInfoResponse] = Field(default_factory=list)
