from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AuditEntryResponse(BaseModel):
    id: str | None = None
    timestamp: datetime
    actor: str
    action: str
    target: str | None = None
    details: dict = Field(default_factory=dict)


class AuditLogResponse(BaseModel):
    entries: list[AuditEntryResponse]
    next_cursor: str | None = None
    prev_cursor: str | None = None
    total: int | None = None
    limit: int


class UserStatsEntry(BaseModel):
    username: str
    role: str
    session_count: int
    storage_bytes: int
    last_active: datetime | None = None


class AdminStatsResponse(BaseModel):
    total_users: int
    total_sessions: int
    total_storage_bytes: int
    users: list[UserStatsEntry]


class FleetAccountEntry(BaseModel):
    """One account's live agent-runner state, as seen by the control plane."""
    account_id: str
    username: str | None = None
    phase: str = "Zero"  # operator status: Running / Waking / Zero / Unknown
    awake: bool = False  # ready pod answering at status.podIP
    ready_replicas: int = 0
    # In-flight runs from the pod's /health (None = awake but probe failed/timed out).
    active_runs: int | None = None
    last_activity_ts: float | None = None  # epoch seconds, from the pod's /health
    pod_ip: str | None = None


class FleetResponse(BaseModel):
    """Live fleet snapshot: awake sandboxes + summed in-flight runs across pods."""
    total_accounts: int
    awake_sandboxes: int
    running_sessions: int
    accounts: list[FleetAccountEntry]


class PendingRegistrationResponse(BaseModel):
    """One pending self-registration request (admin Pending Approval tab).
    password_hash is NEVER included."""
    request_id: str
    username: str
    display_name: str | None = None
    runner_type: str = "auto_scale"
    cpu_cores: float = 1.0
    memory_mb: int = 2048
    volume_gb: int = 1
    note: str | None = None
    status: str = "pending"
    created_at: str | None = None


class PresetPromptResponse(BaseModel):
    enable: bool = False
    content: str | None = None


class PresetPromptUpdate(BaseModel):
    enable: bool
    content: str | None = None


class CliPathResponse(BaseModel):
    cli_path: str | None = None


class CliPathUpdate(BaseModel):
    cli_path: str | None = None


class HistoryRetentionResponse(BaseModel):
    history_retention_days: int = 7


class HistoryRetentionUpdate(BaseModel):
    history_retention_days: int = 7


class RetryableToolEntry(BaseModel):
    name: str
    max_retries: int = 3
    interval_seconds: int = 30


class RetryCallbackWeComConfig(BaseModel):
    api_url: str = ""
    key: str = ""
    service_name: str = ""


class RetryableToolsResponse(BaseModel):
    retryable_tools: list[RetryableToolEntry] = []
    retry_callback_type: str = "none"
    retry_callback_script: str | None = None
    retry_callback_wecom: RetryCallbackWeComConfig | None = None


class RetryableToolsUpdate(BaseModel):
    retryable_tools: list[RetryableToolEntry] = []
    retry_callback_type: str = "none"
    retry_callback_script: str | None = None
    retry_callback_wecom: RetryCallbackWeComConfig | None = None


class RiskyToolsResponse(BaseModel):
    risky_tool_list: list[str] = []


class RiskyToolsUpdate(BaseModel):
    risky_tool_list: list[str] = []


class SensitivePatternEntry(BaseModel):
    name: str
    pattern: str
    mask: str


class SensitivePatternsResponse(BaseModel):
    enable: bool = False
    patterns: list[SensitivePatternEntry] = []


class SensitivePatternsUpdate(BaseModel):
    enable: bool = False
    patterns: list[SensitivePatternEntry] = []
