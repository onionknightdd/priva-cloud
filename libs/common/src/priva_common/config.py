from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, Field, model_validator
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


class ConsoleLoggingSettings(BaseModel):
    # Mirror every channel's file sink to the process stdout so container logs
    # (`kubectl logs` / `docker logs`) carry the same lines as the log files.
    enabled: bool = True
    level: str | None = None  # None → each channel keeps its file-sink level


class LoggingSettings(BaseModel):
    console: ConsoleLoggingSettings = Field(default_factory=ConsoleLoggingSettings)
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
    # D15: pod boot prunes scheduler-origin session transcripts older than this
    # (run records persist forever in data-spine). 0 disables the prune.
    history_retention_days: int = 7

    # --- services/scheduler engine (Phase 4a, design §6) ---
    relist_seconds: int = 30              # D6: every replica re-lists ListActiveJobs
    sweep_seconds: int = 60               # reconcile cadence (stale runs + fire prune)
    # Stale-'running' age-out ceiling — above the D14 caps so the runner
    # always kills first; only pod-vanished runs reach dispatch_lost.
    running_ceiling_seconds: int = 7200
    misfire_grace_seconds: int = 60       # US-8: fire late-once within grace, else skip
    wake_retry_attempts: int = 5          # connection-level dispatch retries …
    wake_retry_base_seconds: float = 2.0  # … backing off base→max (+ jitter)
    wake_retry_max_seconds: float = 60.0
    jitter_window_seconds: float = 5.0    # per-fire wake jitter (spread the 09:00 storm)
    admission_retry_window_seconds: int = 120  # D16: re-admit on 429 up to this long
    fire_prune_hours: int = 24            # job_fire rows older than this are swept
    api_port: int = 8082                  # internal API (/internal/trigger, /healthz)
    # Where the runner reaches the scheduler's internal API (run-now proxy).
    internal_url: str = "http://scheduler:8082"


class ChannelsSettings(BaseModel):
    command_poll_interval: float = 1.0
    heartbeat_interval: float = 5.0
    shutdown_timeout: int = 30


class PtySettings(BaseModel):
    enabled: bool = True
    max_sessions_per_user: int = 3
    idle_timeout_seconds: int = 600
    absolute_timeout_seconds: int = 7200
    output_rate_limit_bytes_per_sec: int = 1_000_000
    max_cols: int = 500
    max_rows: int = 200
    rlimit_cpu_seconds: int = 600
    # 0 = don't cap address space. RLIMIT_AS counts virtual *reservations*, and the
    # claude CLI's bun/JSC binary reserves >3 GiB of PROT_NONE at startup — any
    # realistic cap SIGTRAPs it. Real memory is already bounded by the pod cgroup.
    rlimit_as_bytes: int = 0
    rlimit_fsize_bytes: int = 100 * 1024 * 1024
    rlimit_nofile: int = 1024
    shell: str = ""


class AgentSettings(BaseModel):
    permission_timeout_seconds: int = 600
    # Bound a Claude CLI stream which emits no events while no tool is running.
    # This turns an unavailable egress proxy/upstream into a visible error
    # instead of an endless SSE keepalive stream.
    network_silence_timeout_seconds: int = Field(
        default=120,
        ge=30,
        le=900,
    )


class ServiceIdentitySettings(BaseModel):
    """Asymmetric workload identity (see priva_common.service_identity).

    ``private_key`` is the control-plane signing key and MUST NOT be mounted
    into an agent-runner: the whole point of the split is that a pod running
    untrusted tenant code can verify tokens but cannot mint them. The runner
    gets ``public_key`` plus ``additional_public_keys`` (both harmless — they
    are public) and an operator-minted, account-scoped service token.

    ``additional_public_keys`` is the overlap set for a two-stage signer
    rotation. It may contain the future key before the signer changes, or the
    previous key while old pods/tokens drain. The current signer always remains
    ``private_key``; additional keys can verify only and never affect signing.

    Both unset => an ephemeral in-process keypair, which works for single-process
    dev/tests and fails closed across pods.
    """

    private_key: str | None = None  # PEM (PKCS#8). Control-plane pods only.
    public_key: str | None = None   # PEM (SubjectPublicKeyInfo). Every pod.
    # JSON env: PRIVA_SERVICE_IDENTITY__ADDITIONAL_PUBLIC_KEYS='["-----BEGIN ..."]'
    additional_public_keys: list[str] = Field(default_factory=list, max_length=8)
    # Who this pod claims to be when dialling data-spine / the scheduler API.
    # Must be one of service_token.CONTROL_PLANE_ROLES for a signing workload.
    #
    # Deliberately EMPTY, not "control-panel". data-spine keys its per-workload
    # method allowlist (CONTROL_PLANE_ACL) on this name, so a default naming a
    # real role makes an unconfigured pod silently inherit that role's surface
    # instead of failing. Measured on the dev cluster: scheduler and
    # channel-connector Deployments were missing the env, both presented as
    # "control-panel", and the ACL split was a no-op for every control-plane
    # workload. An empty name fails closed at boot (assert_configured) and again
    # at first use (service_token.current_token).
    service_name: str = ""
    service_token_ttl_seconds: int = 3600  # outbound identity refresh window
    runner_token_ttl_seconds: int = 60     # per-request control-plane → runner


class DataspineSettings(BaseModel):
    """data-spine (durable-state layer) seams. Default = in-process + Postgres.

    Postgres is the product default (requires postgres_dsn); sqlite remains an
    explicit legacy opt-in (backend="sqlite") kept as the migrate-to-pg source
    and rollback target. The DB is only ever opened by the data-spine process
    itself (in_process transport) — everything else is a grpc client and MUST
    NOT be given backend/postgres_dsn (the DSN carries DB credentials).
    """

    transport: Literal["in_process", "grpc"] = "in_process"
    backend: Literal["sqlite", "postgres"] = "postgres"
    sqlite_path: str = "~/priva_workspace/.priva.dataspine.db"
    grpc_dsn: str | None = None  # gRPC target (host:port) when transport == "grpc"
    # The Operator's isolation snapshot fallback is useful only if a black-holed
    # data-spine read eventually returns control. Keep this security-critical RPC
    # deadline short and explicit rather than inheriting gRPC's infinite default.
    network_isolation_rpc_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        le=30,
    )
    # libpq DSN when backend == "postgres", e.g. postgresql://priva:pw@postgres:5432/priva
    postgres_dsn: str | None = None
    # HMAC key for the api_key_lookup index. data-spine only — it is NEVER
    # shipped to a runner. No fallback to auth.jwt_secret: collapsing the two
    # meant one leaked value forged platform logins AND resolved api-key
    # lookups. Unset => data-spine refuses to start (see service.py).
    api_key_hmac_secret: str | None = None
    # Pre-minted, account-scoped workload identity injected by the operator into
    # agent-runner pods (which hold no signing key). Control-plane pods leave
    # this unset and mint their own from service_identity.private_key.
    service_token: str | None = None


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
    # THE runner image authority (operator env, versioned with the platform release).
    # An AgentTenant carrying spec.image overrides it for that account only.
    runner_image: str = "priva/agent-runner:dev"
    runner_image_pull_policy: str = "IfNotPresent"  # so minikube uses locally-loaded images
    # Name of an existing kubernetes.io/dockerconfigjson Secret in the tenants namespace,
    # rendered as imagePullSecrets on every runner pod. "" = none (public/local images).
    runner_image_pull_secret: str = ""
    runner_service_port: int = 8091  # per-account Service / pod runtime port
    terminal_service_port: int = 8092  # independent per-account Web Terminal pod
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
    # cephfs backend (prod/UAT): one RWX PVC per account on a CephFS CSI StorageClass —
    # 1 PVC = 1 subvolume whose size IS the quota. The SC must be RWX-capable and set
    # allowVolumeExpansion for online quota grow. "" => cluster default StorageClass.
    cephfs_storage_class: str = ""
    runner_uid: int = 10001  # non-root sandbox uid the runner runs as / owns its subdir
    runner_gid: int = 10001
    # Terminal is disabled by default for an upgrade-safe rollout. Enabling it
    # reserves one fixed percentage of each tenant's total CPU and memory.
    terminal_resource_percent: int = 0
    terminal_max_sessions: int = 2
    terminal_idle_timeout_seconds: int = 1800
    terminal_max_lifetime_seconds: int = 14400
    terminal_scale_down_grace_seconds: int = 120
    terminal_output_rate_limit_bytes_per_sec: int = 256 * 1024
    terminal_output_burst_bytes: int = 1024 * 1024
    terminal_output_buffer_bytes: int = 1024 * 1024
    terminal_tmp_size_limit: str = "256Mi"
    # Runner /tmp cap. Starlette spools multipart uploads here before any route
    # code runs, so an unbounded emptyDir is a node-level ephemeral-storage DoS.
    # Must exceed the largest allowed upload (user_files: 100MB).
    runner_tmp_size_limit: str = "512Mi"
    # Data-plane gateway observability: the admin scrapes the agentgateway pod's
    # Prometheus endpoint for live HTTP request counts. The metrics port is NOT on
    # the Service, so the scrape targets the pod IP directly (label-selected).
    gateway_name: str = "priva-gateway"  # Gateway resource name => pod label selector
    gateway_metrics_port: int = 15020  # agentgateway data-plane Prometheus /metrics port
    # --- tenant network isolation (operator-rendered NetworkPolicy) -------------
    # Cluster ranges are explicit deployment inputs. They are kept separate from
    # the general blocked set so operators can audit a cluster/CNI migration
    # without reverse-engineering one opaque "internal" list.
    cluster_pod_cidrs: list[str] = ["10.244.0.0/16"]
    cluster_service_cidrs: list[str] = ["10.96.0.0/12"]
    cluster_node_cidrs: list[str] = ["192.168.49.0/24"]
    # Resolver Service/listener IPs. The default is kube-dns in the default
    # 10.96/12 service range; real clusters must supply their actual ClusterIP
    # and any NodeLocal DNSCache listener. CoreDNS is also selected by pod label
    # so this remains correct across CNI pre-/post-DNAT policy evaluation.
    dns_ip_cidrs: list[str] = ["10.96.0.10/32"]
    # Refuse to create/wake tenant workloads until the functional ingress+egress
    # probe has recorded a positive verdict in priva-cluster-facts. Setting this
    # false is suitable only for a single-tenant local developer cluster.
    network_policy_probe_required: bool = True
    # A historical success is not permanent proof: CNI upgrades/config changes
    # can silently remove enforcement while the ConfigMap remains. Re-run the
    # functional probe before this TTL expires and after every network change.
    network_policy_probe_max_age_seconds: int = Field(
        default=7 * 24 * 60 * 60,
        ge=300,
        le=30 * 24 * 60 * 60,
    )
    # The proxy and the public-internet ipBlock both reject non-public address
    # space. 100.64/10 includes Volcengine's 100.96.0.96 metadata endpoint;
    # 169.254/16 includes the conventional 169.254.169.254 endpoint. Cluster
    # pod/service/node CIDRs above are merged into this list by the renderer.
    egress_blocked_cidrs: list[str] = [
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "224.0.0.0/4",
        "240.0.0.0/4",
    ]
    # Deprecated compatibility input. Existing installations that already set
    # this environment variable remain protected; new deployments should use the
    # explicit cluster_*_cidrs and egress_blocked_cidrs fields above.
    egress_internal_cidrs: list[str] = []
    # The proxy is always present and is the only public-egress path in every
    # mode. A same-namespace short name keeps namespace_tenants configurable.
    egress_proxy_host: str = "priva-egress-proxy"
    egress_proxy_port: int = 3128
    # Pin + mirror this for prod. Squid was chosen over Envoy/tinyproxy because
    # its allowlist maps one-to-one onto the admin UI's list and its failure mode
    # is a visible 403 rather than a silently wide match.
    egress_proxy_image: str = "ubuntu/squid:latest"
    # Keep this deliberately narrow: platform-internal clients (data-spine gRPC
    # and scheduler HTTP) disable environment proxies explicitly, so they do not
    # need a broad `.svc` bypass that tenant-launched tools could inherit.
    egress_no_proxy: str = "localhost,127.0.0.1"

    @model_validator(mode="after")
    def _require_shared_control_and_tenant_namespace(self):
        # The current NetworkPolicy peer selectors and namespaced RBAC are
        # intentionally built for one namespace. Silently accepting a split
        # makes same-labelled tenant pods stand in for control-plane peers while
        # the real data-spine/scheduler no longer match. Reject it until every
        # peer and RoleBinding is namespace-qualified end to end.
        if self.namespace_system != self.namespace_tenants:
            raise ValueError(
                "separate system/tenant namespaces are not supported by the "
                "current isolation/RBAC model"
            )
        required_topology = (
            "cluster_pod_cidrs",
            "cluster_service_cidrs",
            "cluster_node_cidrs",
            "dns_ip_cidrs",
            "egress_blocked_cidrs",
        )
        missing = [name for name in required_topology if not getattr(self, name)]
        if missing:
            raise ValueError(
                "tenant isolation topology must not be empty: "
                + ", ".join(missing)
            )
        return self


class EdgeSettings(BaseModel):
    """agentgateway edge knobs. The platform JWT the edge verifies; the control-panel
    mints it and the ext_proc brain reads the already-verified claims.
    """

    jwt_issuer: str = "priva-cp"
    jwt_audience: str | None = None
    jwks_url: str | None = None  # remote JWKS for the agentgateway provider (prod)
    extproc_port: int = 9000  # control-panel gRPC ext_proc (EPP) listener agentgateway calls
    # Browser origins allowed to open a WebSocket. Empty => same-origin only is
    # not enforceable here, so any Origin is accepted (dev). Set it in prod:
    # e.g. ["https://priva.example.com"]. A cross-site page still cannot read the
    # JWT (it rides the subprotocol, not a cookie), so this is defence in depth.
    allowed_ws_origins: list[str] = Field(default_factory=list)
    # Hosts this API answers to (Host-header allowlist). Empty => no check (dev).
    allowed_hosts: list[str] = Field(default_factory=list)
    # Emit HSTS. Off by default because it is only meaningful (and safe) once TLS
    # terminates in front of the gateway.
    hsts_enabled: bool = False
    # Enforce the CSP instead of shipping it Report-Only. Off by default: the SPA
    # frames itself, renders agent HTML via srcdoc and spawns data:/blob: workers,
    # so enforcing an untested policy can white-screen a working app. Watch the
    # report-only violations, then turn this on.
    csp_enforce: bool = False


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
    service_identity: ServiceIdentitySettings = Field(default_factory=ServiceIdentitySettings)
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
