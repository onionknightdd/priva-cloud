from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


class ServerSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8001
    debug: bool = False
    work_dir: str = "~/priva_workspace"


class AuthSettings(BaseModel):
    jwt_secret: str = "dev-insecure-change-me"  # override via config.yaml or PRIVA_API_KEY/env in production
    jwt_expire_hours: int = 24
    default_password: str = "changeme"
    admins: list[str] = Field(default_factory=lambda: ["admin"])
    global_api_key: str | None = None
    enable_anonymous: bool = False


class LoggingTargetSettings(BaseModel):
    path: str
    rotation_time: str
    rotation_size: str
    retention: str
    format: str
    level: str
    compression: str


class LoggingSettings(BaseModel):
    access: LoggingTargetSettings = Field(
        default_factory=lambda: LoggingTargetSettings(
            path="logs/access.log",
            rotation_time="00:00",
            rotation_size="50 MB",
            retention="7 days",
            format="{time:YYYY-MM-DD HH:mm:ss:SSS} | {level: <8} | {extra[client_ip]} | {extra[method]} | {extra[path]} | {extra[status_code]} | {extra[duration_ms]}ms | {extra[user_name]}",
            level="INFO",
            compression="gz",
        )
    )
    server: LoggingTargetSettings = Field(
        default_factory=lambda: LoggingTargetSettings(
            path="logs/server.log",
            rotation_time="00:00",
            rotation_size="50 MB",
            retention="7 days",
            format="{time:YYYY-MM-DD HH:mm:ss:SSS} | {level: <8} | PID:{process} | {name}:{function}:{line} | {message}",
            level="DEBUG",
            compression="gz",
        )
    )
    app: LoggingTargetSettings = Field(
        default_factory=lambda: LoggingTargetSettings(
            path="logs/app.log",
            rotation_time="00:00",
            rotation_size="50 MB",
            retention="30 days",
            format="{time:YYYY-MM-DD HH:mm:ss:SSS} | {message}",
            level="INFO",
            compression="gz",
        )
    )
    scheduler: LoggingTargetSettings = Field(
        default_factory=lambda: LoggingTargetSettings(
            path="logs/scheduler.log",
            rotation_time="00:00",
            rotation_size="50 MB",
            retention="30 days",
            format="{time:YYYY-MM-DD HH:mm:ss:SSS} | {level: <8} | {name}:{function}:{line} | {message}",
            level="INFO",
            compression="gz",
        )
    )
    channels: LoggingTargetSettings = Field(
        default_factory=lambda: LoggingTargetSettings(
            path="logs/channels.log",
            rotation_time="00:00",
            rotation_size="50 MB",
            retention="30 days",
            format="{time:YYYY-MM-DD HH:mm:ss:SSS} | {level: <8} | {name}:{function}:{line} | {message}",
            level="INFO",
            compression="gz",
        )
    )


class SchedulerSettings(BaseModel):
    shutdown_timeout: int = 60
    command_poll_interval: float = 1.0
    heartbeat_interval: float = 5.0


class ChannelsSettings(BaseModel):
    command_poll_interval: float = 1.0
    heartbeat_interval: float = 5.0
    shutdown_timeout: int = 30


class PtySettings(BaseModel):
    enabled: bool = False
    max_sessions_per_user: int = 3
    idle_timeout_seconds: int = 600
    absolute_timeout_seconds: int = 7200
    output_rate_limit_bytes_per_sec: int = 1_000_000
    max_cols: int = 500
    max_rows: int = 200
    rlimit_cpu_seconds: int = 600
    rlimit_as_bytes: int = 2 * 1024 * 1024 * 1024
    rlimit_fsize_bytes: int = 100 * 1024 * 1024
    rlimit_nofile: int = 1024
    shell: str = ""


class AgentSettings(BaseModel):
    permission_timeout_seconds: int = 600


class DataspineSettings(BaseModel):
    """data-spine (durable-state layer) seams. Phase-1 default = in-process + file SQLite.

    transport / backend are config flips; only the in_process + sqlite paths are
    implemented in Phase 1 (grpc + postgres are structured-but-deferred).
    """

    transport: Literal["in_process", "grpc"] = "in_process"
    backend: Literal["sqlite", "postgres"] = "sqlite"
    sqlite_path: str = "~/priva_workspace/.priva.dataspine.db"
    grpc_dsn: str | None = None  # gRPC target (host:port) when transport == "grpc"
    # HMAC key for the api_key_lookup index. Falls back to auth.jwt_secret when unset.
    api_key_hmac_secret: str | None = None


class KubernetesSettings(BaseModel):
    """control-panel (provisioner/EPP) + operator: how to reach the cluster and the
    cluster-wide defaults the control-panel stamps into each ``AgentTenant`` CR.

    The per-tenant authoritative values live on the CR; the operator reads the CR and
    falls back to these defaults. Only consulted when running in the K8s deployment.
    """

    provisioner: Literal["kubernetes"] = "kubernetes"
    in_cluster: bool = True  # build the kube client from the in-cluster ServiceAccount
    kubeconfig: str | None = None  # path when running off-cluster (e.g. minikube from host)
    # Alpha: a single namespace holds the control plane AND the per-account pods/CRs
    # (locked 2026-06-21). Split into system/tenants namespaces later via env override.
    namespace_system: str = "priva-cloud"  # control-panel / data-spine / operator
    namespace_tenants: str = "priva-cloud"  # per-account agent-runner pods + CRs
    runner_image: str = "priva/agent-runner:dev"  # stamped into AgentTenant spec.image
    runner_image_pull_policy: str = "IfNotPresent"  # so minikube uses locally-loaded images
    runner_service_port: int = 8091  # per-account Service / pod runtime port
    idle_grace_seconds: int = 1800  # default spec.idle.graceSeconds (scale-to-zero)
    min_alive_after_wake_seconds: int = 1800  # anti-thrash floor
    max_concurrent_sessions: int = 3  # default spec.concurrency.maxConcurrentSessions
    wake_timeout_seconds: int = 60  # operator wait_pod_ready bound (how long it drives a wake)
    # EPP fast-503 hold: how long the ext_proc waits before returning "waking, retry" so
    # the SPA retries warm. MUST be < agentgateway's ext_proc stream timeout (the operator
    # keeps driving the wake past this). Distinct from wake_timeout_seconds above.
    wake_hold_seconds: int = 5
    # Per-account runner pod sizing — fallback when the CR omits resources/storage.
    # The admin can override per-account (live-editable). Admin "MB"/"GB" are
    # interpreted as Mi/Gi by the operator (matches the legacy inline "1Gi" PVC).
    runner_cpu_cores: float = 1.0
    runner_memory_mb: int = 2048
    runner_storage_gb: int = 1  # default per-account volume quota in Gi (backend-enforced)
    # DEPRECATED: only the abandoned legacy per-account PVCs used this. The shared-export
    # model provisions a per-account quota'd subdir via the storage backend, not a PVC.
    runner_storage_class: str = "csi-hostpath-sc"
    # --- shared-RWX-export storage model (supersedes per-account PVCs) ---------------
    # The runner mounts only its own subdir (subPath=<account_id>) of one shared RWX
    # export; a read-only reader can mount the whole tree (wake-free aggregation). The
    # per-account volume quota is enforced by the storage backend, set at provision time.
    storage_backend: Literal["nfs_xfs", "cephfs"] = "nfs_xfs"  # dev=nfs_xfs, prod=cephfs
    export_claim_name: str = "priva-export"  # the one shared RWX PVC all runners subPath into
    # The quota-manager sidecar (on the dev NFS server) that creates per-account subdirs,
    # sets the XFS project quota, and reports usage (wake-free). Prod uses the Ceph API.
    quota_manager_url: str = "http://priva-quota.priva-cloud.svc:8099"
    runner_uid: int = 10001  # non-root sandbox uid the runner runs as / owns its subdir
    runner_gid: int = 10001
    # Data-plane gateway observability: the admin scrapes the agentgateway pod's
    # Prometheus endpoint for live HTTP request counts. The metrics port is NOT on
    # the Service, so the scrape targets the pod IP directly (label-selected).
    gateway_name: str = "priva-gateway"  # Gateway resource name => pod label selector
    gateway_metrics_port: int = 15020  # agentgateway data-plane Prometheus /metrics port


class EdgeSettings(BaseModel):
    """agentgateway edge knobs. The platform JWT the edge verifies; the control-panel
    mints it and the ext_proc brain reads the already-verified claims.
    """

    jwt_issuer: str = "priva-cp"
    jwt_audience: str | None = None
    jwks_url: str | None = None  # remote JWKS for the agentgateway provider (prod)
    extproc_port: int = 9000  # control-panel gRPC ext_proc (EPP) listener agentgateway calls


class Settings(BaseSettings):
    # Env override for every (nested) key: ``PRIVA_DATASPINE__GRPC_DSN``,
    # ``PRIVA_AUTH__JWT_SECRET``, ``PRIVA_SERVER__PORT`` … This is what lets a
    # containerized service run from ConfigMap/Secret env with no config.yaml.
    # Bespoke ``os.environ`` vars (ACCOUNT_ID, PRIVA_CONFIG_FILE, …) are not
    # pydantic fields, so the prefix does not touch them.
    model_config = SettingsConfigDict(env_prefix="PRIVA_", env_nested_delimiter="__", extra="ignore")

    app_name: str = "Priva API Server"
    app_version: str = "1.0.0"
    server: ServerSettings = Field(default_factory=ServerSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)
    channels: ChannelsSettings = Field(default_factory=ChannelsSettings)
    pty: PtySettings = Field(default_factory=PtySettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    dataspine: DataspineSettings = Field(default_factory=DataspineSettings)
    kubernetes: KubernetesSettings = Field(default_factory=KubernetesSettings)
    edge: EdgeSettings = Field(default_factory=EdgeSettings)

    # Source of the YAML overlay. This module no longer lives next to the file,
    # so the path comes from PRIVA_CONFIG_FILE (server.sh exports it as an
    # absolute path); the CWD-based fallback resolves to the monolith's
    # api/config.yaml when server.sh runs from PROJECT_ROOT. Kept a Path
    # ClassVar because logging.py resolves relative log paths via
    # ``Settings.yaml_file.parent.parent / path``. A missing file is tolerated
    # (pydantic defaults apply) — config.yaml is absent by default.
    yaml_file: ClassVar[Path] = Path(
        os.environ.get("PRIVA_CONFIG_FILE") or (Path.cwd() / "api" / "config.yaml")
    ).expanduser()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls, yaml_file=cls.yaml_file),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
