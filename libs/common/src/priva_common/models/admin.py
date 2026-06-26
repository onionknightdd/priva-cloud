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
    runner_image: str = "priva/agent-runner:dev"
    updated_at: str | None = None


class RunnerDefaultsUpdate(BaseModel):
    """Partial update — only the provided fields are applied (each is an independent
    Save in the panel). CPU in millicores."""
    idle_grace_seconds: int | None = None
    min_alive_after_wake_seconds: int | None = None
    cpu_millicores: int | None = None
    memory_mb: int | None = None
    storage_gb: int | None = None
    runner_image: str | None = None


class RunnerImagesResponse(BaseModel):
    """Agent-runner image tags discoverable in the cluster (kubelet node images),
    unioned with the current default. ``source`` records how they were enumerated
    ('nodes', or 'fallback' when node listing is unavailable)."""
    images: list[str] = Field(default_factory=list)
    source: str = "nodes"


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
