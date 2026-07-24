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


class GatewayMetricsResponse(BaseModel):
    """Live agentgateway HTTP traffic snapshot.

    Cumulative counters scraped from the data-plane gateway pod's Prometheus
    endpoint (agentgateway_requests_total). The SPA derives req/s from the delta
    between successive polls — the server stays stateless. ``available=False`` when
    no gateway pod is reachable (the tile degrades to '—')."""
    available: bool = False
    total_requests: int = 0  # sum of agentgateway_requests_total across all label sets
    connections: int = 0  # sum of agentgateway_downstream_connections_total
    by_status_class: dict[str, int] = Field(default_factory=dict)  # "2xx","4xx","5xx",…
    by_backend: dict[str, int] = Field(default_factory=dict)  # "control-panel","agent-runner"
    scraped_at: float = 0.0  # server epoch seconds — the SPA's rate-delta time base


class HealthDep(BaseModel):
    """One downstream dependency a module self-reports from its ``/health``.

    ``ok=None`` means unknown / not probed (e.g. a module with no HTTP endpoint),
    distinct from ``ok=False`` (probed and failing). Drives the edge-level ✕ on
    the System Map when a real dependency (e.g. agent-runner→data-spine) is down."""
    name: str
    ok: bool | None = None
    detail: str | None = None


class ReplicaCount(BaseModel):
    """ready/desired replicas of a module's backing k8s Deployment — rendered as
    the corner chip on the System Map (green full, yellow partial, red none)."""
    ready: int = 0
    desired: int = 0


class SystemNode(BaseModel):
    """One module on the System Map topology (browser / agentgateway / …)."""
    id: str                          # "agentgateway","control-panel",...
    label: str
    sub: str | None = None           # ":8080 HTTP · :9000 EPP"
    plane: str                       # "edge"|"control"|"data"|"tenant"
    status: str                      # "up"|"degraded"|"down"|"idle"|"disabled"
    detail: str | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    deps: list[HealthDep] = Field(default_factory=list)
    replicas: ReplicaCount | None = None  # None => no chip (browser / fleet / planned)


class SystemEdge(BaseModel):
    """One connection between modules. ``bytepath`` edges animate a constant
    particle flow while ``healthy``; an unhealthy edge freezes and shows an ✕."""
    source: str
    target: str
    label: str | None = None
    kind: str = "control"            # "byte"|"decision"|"control"|"grpc"
    bytepath: bool = False           # True → animate particles when healthy
    healthy: bool = True             # False → render ✕ + freeze
    disabled: bool = False           # planned edge


class SystemHealthResponse(BaseModel):
    """Topology + live per-module health for the admin System Map.

    Read-only observability snapshot: k8s Deployment readiness for up/down,
    enriched by each module's ``/health`` self-reported downstream connectivity
    so edge-level failures (e.g. agent-runner→data-spine) surface as ✕."""
    nodes: list[SystemNode]
    edges: list[SystemEdge]
    scraped_at: float = 0.0


class ResourceUsageAccountEntry(BaseModel):
    """One account's agent-runner resource line: live usage vs allocated quota.

    ``*_used_*`` come from metrics-server (0 when the pod is asleep — nothing to
    measure); ``*_allocated_*`` come from the account's ``account_resource_spec``
    (the committed ceiling, independent of sleep state). Volume has no live-usage
    figure (metrics-server doesn't report PVC disk), so only ``volume_gb`` is shown."""
    account_id: str
    username: str | None = None
    runner_type: str = "auto_scale"
    awake: bool = False
    cpu_used_m: float = 0.0          # live millicores
    cpu_allocated_m: float = 0.0     # spec cpu_cores × 1000
    memory_used_mb: float = 0.0      # live MiB
    memory_allocated_mb: float = 0.0
    volume_gb: int = 1               # allocated quota (Gi)
    volume_used_gb: float | None = None  # backend-reported used (Gi); None if unavailable


class ResourceUsageResponse(BaseModel):
    """Agent-runtime resource consumption for the admin Resource Quota view.

    Fleet-wide used vs allocated totals + per-account rows. ``used`` is summed
    over awake pods (live metrics); ``allocated`` is summed over ALL accounts'
    resource specs (the committed quota). ``available=False`` when metrics-server
    is unreachable (the bars degrade to '—' rather than failing the view)."""
    available: bool = False
    cpu_used_m: float = 0.0
    cpu_allocated_m: float = 0.0
    memory_used_mb: float = 0.0
    memory_allocated_mb: float = 0.0
    volume_allocated_gb: int = 0
    volume_used_gb: float = 0.0      # fleet total of backend-reported usage (Gi)
    awake: int = 0
    sleeping: int = 0
    total_accounts: int = 0
    accounts: list[ResourceUsageAccountEntry] = Field(default_factory=list)
    scraped_at: float = 0.0


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


class RunnerDefaultsResponse(BaseModel):
    """Platform-wide GLOBAL defaults for per-account agent-runner pods (admin "Agent
    Runner Sandbox" panel). An account whose CR omits a field inherits the value here.
    CPU is in MILLICORES for the digit-only UI (250 = 0.25 cores; stored as cpu_cores)."""
    idle_grace_seconds: int = 1800
    min_alive_after_wake_seconds: int = 1800
    cpu_millicores: int = 1000
    memory_mb: int = 2048
    storage_gb: int = 1
    terminal_resource_percent: int = 0
    terminal_max_sessions: int = 2
    terminal_idle_timeout_seconds: int = 1800
    terminal_max_lifetime_seconds: int = 14400
    terminal_scale_down_grace_seconds: int = 120
    terminal_enabled: bool = False
    runner_cpu_millicores: int = 1000
    terminal_cpu_millicores: int = 0
    runner_memory_mb: int = 2048
    terminal_memory_mb: int = 0
    updated_at: str | None = None


class RunnerDefaultsUpdate(BaseModel):
    """Partial update — only the provided fields are applied (each is an independent
    Save in the panel). CPU in millicores."""
    idle_grace_seconds: int | None = None
    min_alive_after_wake_seconds: int | None = None
    cpu_millicores: int | None = None
    memory_mb: int | None = None
    storage_gb: int | None = None
    terminal_resource_percent: int | None = None
    terminal_max_sessions: int | None = None
    terminal_idle_timeout_seconds: int | None = None
    terminal_max_lifetime_seconds: int | None = None
    terminal_scale_down_grace_seconds: int | None = None


# --- Hook Policy (admin "Runtime" panel) -------------------------------------
# Admin-stored hooks in data-spine, delivered to every agent-runner at session
# build. hook_type uses Claude Code native strings ("command"|"http"|"mcp_tool";
# mcp_tool is schema-reserved and rejected in v1). script_body is included on
# the ADMIN surface only — the user-facing catalog (agent-runner /catalog)
# never exposes it.

class HookPolicyItem(BaseModel):
    id: str
    hook_type: str = "command"
    name: str = ""
    description: str = ""  # zh content, shown verbatim to users
    events: list[str] = Field(default_factory=list)
    matcher: str = ""
    timeout_seconds: int = 30
    interpreter: str = ""
    script_body: str = ""
    content_hash: str = ""
    url: str = ""
    headers_json: str = ""
    allowed_env_vars: list[str] = Field(default_factory=list)
    mcp_server: str = ""
    mcp_tool: str = ""
    enabled: bool = False
    enforced: bool = False       # derived: len(enforced_events) > 0
    # Per-event activation: subset of `events` the hook actually fires on.
    enforced_events: list[str] = Field(default_factory=list)
    default_on: bool = False
    predefined: bool = False
    seed_version: int = 0
    target: str = ""
    updated_at: str | None = None
    updated_by: str = ""
    # Computed vs the shipped seeds: None (custom row) | "current" | "edited"
    # (admin-modified, no newer seed) | "outdated" (admin-modified AND a newer
    # seed shipped — the UI shows the diff banner).
    seed_state: str | None = None
    latest_seed_version: int | None = None


class HookPolicyListResponse(BaseModel):
    items: list[HookPolicyItem] = Field(default_factory=list)
    supported_events: list[str] = Field(default_factory=list)


class HookPolicyCreate(BaseModel):
    id: str
    hook_type: str = "command"
    name: str
    description: str
    events: list[str]
    matcher: str = ""
    timeout_seconds: int | None = None  # default: command 30 · http 5
    interpreter: str = ""
    script_body: str = ""
    url: str = ""
    headers_json: str = ""
    allowed_env_vars: list[str] = Field(default_factory=list)
    mcp_server: str = ""
    mcp_tool: str = ""
    # NOTE: new rows always save enabled=false (arm explicitly) — an `enabled`
    # field here would be ignored, so there isn't one.
    enforced: bool = False
    # Per-event activation (subset of events). Empty + enforced=true means
    # "all events" (legacy-client behavior).
    enforced_events: list[str] = Field(default_factory=list)
    default_on: bool = False
    target: str = ""


class HookPolicyUpdate(BaseModel):
    """Partial update — only provided fields are written (update_mask from
    fields_set). Validation runs on the merged row."""
    hook_type: str | None = None
    name: str | None = None
    description: str | None = None
    events: list[str] | None = None
    matcher: str | None = None
    timeout_seconds: int | None = None
    interpreter: str | None = None
    script_body: str | None = None
    url: str | None = None
    headers_json: str | None = None
    allowed_env_vars: list[str] | None = None
    mcp_server: str | None = None
    mcp_tool: str | None = None
    enabled: bool | None = None
    enforced: bool | None = None
    # Per-event activation (subset of events); the server derives `enforced`
    # from its non-emptiness.
    enforced_events: list[str] | None = None
    default_on: bool | None = None
    target: str | None = None


class HookPolicyValidationError(BaseModel):
    field: str
    message: str
    line: int | None = None  # script syntax errors carry the line number


class HookPolicyValidateRequest(BaseModel):
    """Validate a draft without saving (the drawer's [Validate] button). Same
    shape as create; id optional so unsaved drafts can validate too."""
    id: str | None = None
    hook_type: str = "command"
    name: str = ""
    description: str = ""
    events: list[str] = Field(default_factory=list)
    matcher: str = ""
    timeout_seconds: int | None = None
    interpreter: str = ""
    script_body: str = ""
    url: str = ""
    headers_json: str = ""
    allowed_env_vars: list[str] = Field(default_factory=list)


class HookPolicyValidateResponse(BaseModel):
    valid: bool
    errors: list[HookPolicyValidationError] = Field(default_factory=list)


class HookPolicySeedResponse(BaseModel):
    """The shipped seed content for a predefined row (side-by-side diff view)."""
    id: str
    seed_version: int
    name: str
    description: str
    events: list[str]
    matcher: str
    interpreter: str
    script_body: str
    timeout_seconds: int
    default_on: bool


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
