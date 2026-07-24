"""Data-plane client interface (the transport seam) + boundary DTOs.

These Protocols define the gRPC-shaped contract that every service calls. The
in-process transport (Phase 1) backs them with `priva_data_spine` service impls;
the gRPC transport (deferred) backs them with generated stubs. Callers depend
only on this module — never on the service package.

DTOs reuse the existing pydantic models where they already exist
(`UserRecord`, `ScheduledJobDefinition`, `JobRunRecord`); `BindingRecord` and
`QuotaRecord` are defined here (greenfield).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, Field

from priva_common.models.auth import UserRecord
from priva_common.models.scheduler import JobRunRecord, ScheduledJobDefinition

__all__ = [
    "UNSET",
    "BindingRecord",
    "QuotaRecord",
    "RunPage",
    "ResourceSpecRecord",
    "RunnerDefaultsRecord",
    "PendingRegistrationRecord",
    "HookPolicyRecord",
    "FeishuChannelConfigRecord",
    "ChannelPlatformConfigRecord",
    "AccountClient",
    "BindingClient",
    "QuotaClient",
    "SchedulerClient",
    "AdminClient",
    "ResourceSpecClient",
    "RunnerDefaultsClient",
    "RegistrationClient",
    "HookPolicyClient",
    "FeishuChannelConfigClient",
    "ChannelPlatformConfigClient",
    "DataplaneClient",
]

# Sentinel for "field not provided" vs "field set to None" (mirrors the monolith's
# UserStore.update_user(api_key=...) convention).
UNSET: Any = ...


class BindingRecord(BaseModel):
    binding_id: str
    account_id: str
    session_uuid: str | None = None  # None = detached ("/new"); next DM starts fresh
    first_run_done: bool = False
    feishu_chat_id: str | None = None
    # Display metadata for the settings-page session list, stamped by the
    # connector from live chat context (p2p → peer name, group → group name).
    chat_type: str = ""   # "p2p" | "group" | "" (pre-feature rows)
    chat_name: str = ""
    bound_at: str | None = None
    rebound_at: str | None = None


class QuotaRecord(BaseModel):
    account_id: str
    tier: str = "default"
    max_concurrent_sessions: int = 3
    idle_grace_seconds: int = 1800
    updated_at: str | None = None


class RunPage(BaseModel):
    runs: list[JobRunRecord]
    next_cursor: str | None = None
    prev_cursor: str | None = None
    total: int | None = None  # None when a filter is active (total unknown)


class ResourceSpecRecord(BaseModel):
    account_id: str
    cpu_cores: float = 1.0
    memory_mb: int = 2048
    volume_gb: int = 1
    updated_at: str | None = None


class FeishuChannelConfigRecord(BaseModel):
    """Per-account Feishu bot config (`feishu_channel_config`). Model B: each user's
    own self-built app. app_secret is NEVER carried in cleartext — only `has_app_secret`
    (presence) crosses this boundary on read. effective_enabled is server-computed
    (user_enabled AND NOT admin_disabled AND credentials present)."""
    account_id: str
    app_id: str | None = None
    has_app_secret: bool = False
    app_secret_updated_at: str | None = None
    user_enabled: bool = False
    admin_disabled: bool = False
    effective_enabled: bool = False
    single_chat_access_mode: str = "owner_only"  # owner_only | allowlist | all
    allowed_union_ids: str = "[]"                 # JSON array as string
    # Group-chat participation (feat_feishu_DM.md §5): user opt-in AND NOT the
    # platform-wide kill switch. effective_group_enabled is server-computed and
    # part of desired_digest (a flip re-arms the worker's cfg snapshot).
    group_chat_enabled: bool = False
    effective_group_enabled: bool = False
    # Owner link-code binding (feat_feishu_DM.md §4) — ids in the BOT app's namespace.
    # Owner cols are part of desired_digest (bind/unbind re-arms the worker); the
    # pending link-code lives server-side only (hash+expiry) and is never read back.
    owner_union_id: str = ""
    owner_open_id: str = ""
    owner_bound_at: str | None = None
    welcome_message: str = ""
    reject_message: str = ""
    model: str | None = None
    max_queue_size: int = 3
    enable_permission_feedback: bool = True
    feedback_timeout_seconds: int = 180
    domain: str = "feishu"                        # feishu | lark
    conn_status: str = "disabled"
    last_error_code: int | None = None
    last_error_message: str | None = None
    last_connected_at: str | None = None
    status_updated_at: str | None = None
    desired_digest: str | None = None
    updated_by: str = ""
    updated_at: str | None = None


class FeishuSecretRecord(BaseModel):
    """Connector-only privileged read: the DECRYPTED plaintext app_secret needed to
    open the Feishu WS / mint tenant_access_token. `app_secret` is "" when unset OR
    when the stored ciphertext failed to decrypt (key rotation/corruption) — the
    connector treats "" as unusable. NEVER log this record."""
    account_id: str
    app_id: str | None = None
    app_secret: str = ""      # plaintext; "" = unset or undecryptable
    domain: str = "feishu"


class ChannelPlatformConfigRecord(BaseModel):
    """ADMIN-only platform-wide channel settings (`channel_platform_config`, single
    row). group_chat_disabled is the global group-chat kill switch: flipping it
    recomputes every feishu row's desired_digest server-side so the connector
    re-arms all affected workers on its next poll."""
    group_chat_disabled: bool = False
    updated_by: str = ""
    updated_at: str | None = None


class RunnerDefaultsRecord(BaseModel):
    """Platform-wide global defaults for per-account agent-runner pods. Always a
    complete set (seeded from settings) — an account's CR overrides win per-field.
    The runner image is NOT a runtime default: the operator's deployment settings
    decide it (AgentTenant spec.image = per-account override)."""
    idle_grace_seconds: int = 1800
    min_alive_after_wake_seconds: int = 1800
    cpu_cores: float = 1.0
    memory_mb: int = 2048
    storage_gb: int = 1
    # 0 disables Web Terminal platform-wide. Non-zero values are fixed 5% steps
    # and may reserve at most half of each tenant's CPU and memory allocation.
    terminal_resource_percent: int = 0
    terminal_max_sessions: int = 2
    terminal_idle_timeout_seconds: int = 1800
    terminal_max_lifetime_seconds: int = 14400
    terminal_scale_down_grace_seconds: int = 120
    updated_at: str | None = None


class HookPolicyRecord(BaseModel):
    """Admin-stored hook (the `hook_policy` table). hook_type uses Claude Code
    native strings; command payload is script_body (content_hash server-derived);
    predefined rows are the seeded legacy builtins (not deletable)."""
    id: str
    hook_type: str = "command"  # "command" | "http" | "mcp_tool"
    name: str = ""
    description: str = ""
    events: list[str] = Field(default_factory=list)
    matcher: str = ""
    timeout_seconds: int = 30
    interpreter: str = ""       # command: "bash" | "python3"
    script_body: str = ""
    content_hash: str = ""
    url: str = ""
    headers_json: str = ""
    allowed_env_vars: list[str] = Field(default_factory=list)
    mcp_server: str = ""
    mcp_tool: str = ""
    enabled: bool = False       # DERIVED: len(enforced_events) > 0
    enforced: bool = False      # DERIVED: len(enforced_events) > 0
    default_on: bool = False
    predefined: bool = False
    seed_version: int = 0
    target: str = ""
    updated_at: str | None = None
    updated_by: str = ""
    # Per-event activation — the subset of `events` the hook actually fires on
    # (admin panel shield is per event group). Invariant: subset of events.
    enforced_events: list[str] = Field(default_factory=list)


class PendingRegistrationRecord(BaseModel):
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
    updated_at: str | None = None
    # bcrypt hash — only populated on the internal get() the approval path reads;
    # never returned by list() (the server zeroes it there).
    password_hash: str | None = None


class AccountClient(Protocol):
    def get(self, account_id: str) -> UserRecord | None: ...
    def get_by_username(self, username: str) -> UserRecord | None: ...
    def list(self) -> list[UserRecord]: ...
    def create(
        self,
        username: str,
        password: str = "",
        role: str = "user",
        agent_runner_type: str = "auto_scale",
        password_hash: str | None = None,  # precomputed bcrypt (approval path)
    ) -> UserRecord: ...
    def update(
        self,
        account_id: str,
        *,
        password: str | None = None,
        role: str | None = None,
        api_key: Any = UNSET,  # UNSET=leave, None=clear, str=set
        status: str | None = None,
        agent_runner_type: str | None = None,
        feishu_user_id: Any = UNSET,
        feishu_display_name: Any = UNSET,
    ) -> UserRecord: ...
    def delete(self, account_id: str) -> None: ...
    def verify_password(self, username: str, password: str) -> bool: ...
    def find_by_api_key(self, api_key: str) -> UserRecord | None: ...
    def count_admins(self) -> int: ...
    def find_by_feishu_user_id(self, feishu_user_id: str) -> UserRecord | None: ...
    def has_users(self) -> bool: ...


class BindingClient(Protocol):
    def bind(self, account_id: str, session_uuid: str | None, feishu_chat_id: str | None = None) -> BindingRecord: ...
    def rebind(self, account_id: str, session_uuid: str | None, feishu_chat_id: str | None = None) -> BindingRecord: ...
    def set_display(
        self, account_id: str, feishu_chat_id: str | None, *, chat_type: str = "", chat_name: str = ""
    ) -> BindingRecord | None: ...
    def claim_first_run_im(self, binding_id: str) -> bool: ...
    def get_binding(self, binding_id: str) -> BindingRecord | None: ...
    def list_bindings(self, account_id: str) -> list[BindingRecord]: ...


class QuotaClient(Protocol):
    def get(self, account_id: str) -> QuotaRecord | None: ...
    def ensure(self, account_id: str) -> QuotaRecord: ...  # seed defaults if absent
    def set(
        self,
        account_id: str,
        *,
        tier: str | None = None,
        max_concurrent_sessions: int | None = None,
        idle_grace_seconds: int | None = None,
    ) -> QuotaRecord: ...


class SchedulerClient(Protocol):
    def create_job(self, account_id: str, defn: ScheduledJobDefinition) -> ScheduledJobDefinition: ...
    def get_job(self, job_id: str) -> ScheduledJobDefinition | None: ...
    def update_job(self, job_id: str, defn: ScheduledJobDefinition) -> ScheduledJobDefinition | None: ...
    def delete_job(self, job_id: str) -> bool: ...
    def list_jobs(self, account_id: str) -> list[ScheduledJobDefinition]: ...
    # (account_id, defn) pairs — the daemon needs the owner of each active job.
    def list_active_jobs(self) -> list[tuple[str, ScheduledJobDefinition]]: ...
    def set_job_status(self, job_id: str, status: str) -> ScheduledJobDefinition | None: ...
    def start_run(self, account_id: str, record: JobRunRecord) -> JobRunRecord: ...
    def finish_run(self, record: JobRunRecord) -> JobRunRecord: ...
    def record_run(self, account_id: str, record: JobRunRecord) -> JobRunRecord: ...  # full-snapshot upsert
    def get_run(self, account_id: str, run_id: str) -> JobRunRecord | None: ...
    def get_latest_run(self, account_id: str, job_id: str) -> JobRunRecord | None: ...
    def list_runs(
        self,
        account_id: str,
        *,
        limit: int = 50,
        before: str | None = None,
        after: str | None = None,
        job_id: str | None = None,
        status: str | None = None,
    ) -> RunPage: ...
    def delete_runs_before(self, account_id: str, cutoff_date: str) -> list[str]: ...  # returns deleted run_ids
    # The exactly-once fire claim: INSERT-wins on job_fire(job_id, fire_epoch).
    # fire_epoch = the trigger's SCHEDULED instant (epoch s). False = another
    # replica won, or the job no longer exists.
    def claim_fire(self, job_id: str, fire_epoch: int, claimed_by: str) -> bool: ...
    def prune_fires_before(self, cutoff: str) -> int: ...  # reconcile-sweep hygiene


class AdminClient(Protocol):
    def healthz(self) -> str: ...
    def readyz(self) -> tuple[bool, str]: ...
    def stats(self) -> dict[str, int]: ...


class ResourceSpecClient(Protocol):
    def get(self, account_id: str) -> ResourceSpecRecord | None: ...
    def set(
        self,
        account_id: str,
        *,
        cpu_cores: float | None = None,
        memory_mb: int | None = None,
        volume_gb: int | None = None,
    ) -> ResourceSpecRecord: ...
    def list(self) -> list[ResourceSpecRecord]: ...


class FeishuChannelConfigClient(Protocol):
    """Per-account Feishu bot config. Three role-scoped setters mirror the wire's
    separate Set RPCs so a caller can only write columns in its role: the USER
    route writes credentials + user_enabled + behaviour, the ADMIN route writes
    only admin_disabled, the CONNECTOR writes only observed status. `set_status`
    must not perturb updated_at / desired_digest (the connector poll diffs on
    desired_digest, so status write-back stays invisible to it)."""

    def get(self, account_id: str) -> FeishuChannelConfigRecord | None: ...
    def set_user(
        self,
        account_id: str,
        *,
        app_id: str | None = None,
        app_secret: Any = UNSET,  # UNSET=keep, ""=clear, str=set/rotate
        user_enabled: bool | None = None,
        single_chat_access_mode: str | None = None,
        allowed_union_ids: str | None = None,
        welcome_message: str | None = None,
        reject_message: str | None = None,
        model: str | None = None,
        max_queue_size: int | None = None,
        enable_permission_feedback: bool | None = None,
        feedback_timeout_seconds: int | None = None,
        domain: str | None = None,
        group_chat_enabled: bool | None = None,
        updated_by: str = "",
    ) -> FeishuChannelConfigRecord: ...
    def set_admin(
        self, account_id: str, *, admin_disabled: bool | None = None, updated_by: str = ""
    ) -> FeishuChannelConfigRecord: ...
    def set_status(
        self,
        account_id: str,
        *,
        conn_status: str | None = None,
        last_error_code: int | None = None,
        last_error_message: str | None = None,
        last_connected_at: str | None = None,
    ) -> FeishuChannelConfigRecord: ...
    def list(self) -> list[FeishuChannelConfigRecord]: ...
    def list_effective(self) -> list[FeishuChannelConfigRecord]: ...
    # Connector-only privileged read: decrypted plaintext app_secret. None when the
    # account has no config row at all.
    def get_secret(self, account_id: str) -> FeishuSecretRecord | None: ...
    # Owner link-code binding (feat_feishu_DM.md §4). create_link_code mints a
    # single-use code (returns plaintext + expires_at; only its SHA-256 is stored),
    # bind_owner_with_code atomically validates+binds+clears (constant-time compare,
    # CONNECTOR route), unbind_owner clears the owner (USER route via control-panel).
    def create_link_code(self, account_id: str) -> tuple[str, str]: ...
    def bind_owner_with_code(
        self, account_id: str, code: str, union_id: str, open_id: str
    ) -> bool: ...
    def unbind_owner(self, account_id: str, *, updated_by: str = "") -> FeishuChannelConfigRecord: ...


class ChannelPlatformConfigClient(Protocol):
    """ADMIN-only platform-wide channel settings singleton. `set` recomputes the
    desired_digest of every feishu row whose effective_group_enabled flips, so the
    connector's poll re-arms all affected workers (feat_feishu_DM.md §5.1)."""

    def get(self) -> ChannelPlatformConfigRecord: ...
    def set(
        self, *, group_chat_disabled: bool | None = None, updated_by: str = ""
    ) -> ChannelPlatformConfigRecord: ...


class RunnerDefaultsClient(Protocol):
    def get(self) -> RunnerDefaultsRecord: ...  # seeded from settings when never set
    def set(
        self,
        *,
        idle_grace_seconds: int | None = None,
        min_alive_after_wake_seconds: int | None = None,
        cpu_cores: float | None = None,
        memory_mb: int | None = None,
        storage_gb: int | None = None,
        terminal_resource_percent: int | None = None,
        terminal_max_sessions: int | None = None,
        terminal_idle_timeout_seconds: int | None = None,
        terminal_max_lifetime_seconds: int | None = None,
        terminal_scale_down_grace_seconds: int | None = None,
    ) -> RunnerDefaultsRecord: ...


class HookPolicyClient(Protocol):
    """Errors (mapped to gRPC codes by the server, back to these by the client):
    upsert(expect="create") raises ValueError on id collision; upsert(expect=
    "update") / delete raise LookupError on a missing row; delete raises
    PermissionError on a predefined row."""

    def list(self, enabled_only: bool = False) -> list[HookPolicyRecord]: ...
    def get(self, policy_id: str) -> HookPolicyRecord | None: ...
    def upsert(
        self,
        policy: HookPolicyRecord,
        *,
        update_mask: list[str] | None = None,  # None/[] = all writable fields
        expect: str = "",  # "" | "create" | "update"
    ) -> HookPolicyRecord: ...
    def delete(self, policy_id: str) -> None: ...


class RegistrationClient(Protocol):
    def create(
        self,
        *,
        username: str,
        password_hash: str,
        display_name: str | None = None,
        runner_type: str = "auto_scale",
        cpu_cores: float = 1.0,
        memory_mb: int = 2048,
        volume_gb: int = 1,
        note: str | None = None,
    ) -> PendingRegistrationRecord: ...
    def get_open_by_username(self, username: str) -> PendingRegistrationRecord | None: ...
    def list(self, status: str | None = None) -> list[PendingRegistrationRecord]: ...
    def get(self, request_id: str) -> PendingRegistrationRecord | None: ...  # includes password_hash
    def set_status(self, request_id: str, status: str) -> PendingRegistrationRecord | None: ...


@dataclass
class DataplaneClient:
    """Aggregate handle — one per process. `get_client()` returns this."""

    accounts: AccountClient
    bindings: BindingClient
    quota: QuotaClient
    scheduler: SchedulerClient
    admin: AdminClient
    resource_specs: ResourceSpecClient
    runner_defaults: RunnerDefaultsClient
    registrations: RegistrationClient
    hook_policies: HookPolicyClient
    feishu_configs: FeishuChannelConfigClient
    channel_platform: ChannelPlatformConfigClient
