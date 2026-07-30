"""Kubernetes client + manifest builders for the per-account agent-runner objects.

Bodies are plain dicts (the client serializes them). Reconciliation is
idempotent; security-boundary resources deliberately propagate write conflicts
so a stale generation is never treated as successfully applied.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from types import SimpleNamespace

from kubernetes import client, config

from priva_common import drain_token, service_identity, service_token
from priva_common.logging import get_app_logger
from priva_common.network_isolation import (
    ISOLATION_INTENT_ANNOTATION,
    isolation_intent_digest,
    policy_may_widen_tenant_runtime,
)
from priva_operator import GROUP, PLURAL, VERSION, names
from priva_operator.storage_backend import MountInfo, get_backend

logger = get_app_logger(__name__)

_loaded = False

# The single, global managed-policy ConfigMap (hook_policy is global scope, so one
# object is shared by every ar-<account> pod). Mounted whole (no subPath) at the
# Claude Code managed-settings path so the CLI loads it natively.
MANAGED_POLICY_CM = "claude-managed-policy"
MANAGED_POLICY_MOUNT = "/etc/claude-code"
MANAGED_POLICY_VOLUME = "claude-policy"
_POLICY_DIGEST_ANNOTATION = "priva.io/policy-digest"
_TERMINAL_PERCENT_ANNOTATION = "priva.io/terminal-resource-percent"
_ALLOCATION_HASH_ANNOTATION = "priva.io/allocation-hash"
_TERMINAL_TEMPLATE_HASH_ANNOTATION = "priva.io/terminal-template-hash"
# Fingerprint of the injected egress-proxy env. Deliberately NOT folded into
# allocation_hash (that has eight call sites; one that forgot to thread `iso`
# would produce a permanent desired!=applied mismatch, i.e. a restart loop).
# But it still needs a trigger of its own: without one a PERSISTENT runner never
# picks the config up at all — it is never dormant, so ensure_runtime_objects
# always returns early and the timer has nothing to compare. Measured in-cluster.
_EGRESS_GENERATION_ANNOTATION = "priva.io/egress-generation"

# Bound Kubernetes scale calls so a reconcile cannot hang indefinitely after
# it has persisted Draining. The in-pod admission gate is permanent for that
# process; on API failure the CR stays fail-closed and a later tick retries from
# live Deployment/Pod state.
_KUBE_REQUEST_TIMEOUT = (3.0, 10.0)
_ACCOUNT_WORKLOAD_APPS = ("agent-runner", "terminal")
ISOLATION_SNAPSHOT_CONFIG_MAP = "priva-network-isolation-snapshot"
ISOLATION_SNAPSHOT_KEY = "snapshot.json"
ISOLATION_SNAPSHOT_VERSION = 1
_ISOLATION_SNAPSHOT_LABELS = {
    "app.kubernetes.io/managed-by": "priva-operator",
    "priva.io/config-kind": "network-isolation-snapshot",
}


class IsolationConflictError(RuntimeError):
    """An additional NetworkPolicy can widen a tenant security boundary."""

    def __init__(self, policy_names) -> None:
        self.policy_names = tuple(sorted(set(policy_names)))
        self.conflicting_policies = self.policy_names
        names = ", ".join(self.policy_names)
        super().__init__(
            "tenant isolation blocked by additional NetworkPolicy allow rules: "
            f"{names}"
        )


def _load() -> None:
    global _loaded
    if _loaded:
        return
    try:
        config.load_incluster_config()
    except Exception:
        config.load_kube_config()
    _loaded = True


def apps() -> "client.AppsV1Api":
    _load()
    return client.AppsV1Api()


def core() -> "client.CoreV1Api":
    _load()
    return client.CoreV1Api()


def custom() -> "client.CustomObjectsApi":
    _load()
    return client.CustomObjectsApi()


def networking() -> "client.NetworkingV1Api":
    _load()
    return client.NetworkingV1Api()


def _ignore_conflict(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except client.ApiException as exc:
        if exc.status == 409:  # AlreadyExists
            return None
        raise


# --- resource quantity helpers ----------------------------------------------
# Admin "MB"/"GB" are interpreted as Mi/Gi (matches the legacy inline "1Gi" PVC).

def cpu_quantity(cores: float) -> str:
    """cores -> k8s CPU quantity. 0.5 -> '500m', 2 -> '2' (integral stays whole)."""
    cores = float(cores)
    if cores == int(cores):
        return str(int(cores))
    return f"{int(round(cores * 1000))}m"


def mem_quantity(mb: int) -> str:
    return f"{int(mb)}Mi"


def resolve_terminal_percent(settings, defaults=None) -> int:
    value = (getattr(defaults, "terminal_resource_percent", None)
             if defaults is not None else None)
    if value is None:
        value = getattr(settings.kubernetes, "terminal_resource_percent", 0)
    value = int(value)
    if value < 0 or value > 50 or value % 5:
        logger.warning("invalid terminal_resource_percent=%s; disabling Terminal", value)
        return 0
    return value


def _resource_totals(spec: dict, settings, defaults=None) -> tuple[int, int]:
    """Return the tenant's committed total as integer millicores + MiB."""
    r = spec.get("resources") or {}
    cores = r.get("cpu")
    if cores is None:
        cores = defaults.cpu_cores if defaults else settings.kubernetes.runner_cpu_cores
    memory_mb = r.get("memoryMb")
    if memory_mb is None:
        memory_mb = defaults.memory_mb if defaults else settings.kubernetes.runner_memory_mb
    return int(round(float(cores) * 1000)), int(memory_mb)


def _resource_block(cpu_m: int, memory_mb: int) -> dict:
    cpu = f"{int(cpu_m)}m" if int(cpu_m) % 1000 else str(int(cpu_m) // 1000)
    q = {"cpu": cpu, "memory": mem_quantity(memory_mb)}
    return {"requests": dict(q), "limits": dict(q)}


def _split_resources(spec: dict, settings, defaults=None) -> tuple[tuple[int, int], tuple[int, int]]:
    total_cpu_m, total_memory_mb = _resource_totals(spec, settings, defaults)
    percent = resolve_terminal_percent(settings, defaults)
    if percent <= 0:
        return (total_cpu_m, total_memory_mb), (0, 0)
    if total_cpu_m < 2 or total_memory_mb < 2:
        raise ValueError("terminal requires at least 2m CPU and 2Mi memory total")
    terminal_cpu_m = min(total_cpu_m - 1, max(1, total_cpu_m * percent // 100))
    terminal_memory_mb = min(total_memory_mb - 1, max(1, total_memory_mb * percent // 100))
    return ((total_cpu_m - terminal_cpu_m, total_memory_mb - terminal_memory_mb),
            (terminal_cpu_m, terminal_memory_mb))


# --- the inherit cascade: CR spec field (per-account override) > global runner_defaults
# (the admin "Agent Runner Sandbox" panel) > static env settings (the ultimate seed/
# fail-soft when data-spine is unreachable). `defaults` is a RunnerDefaultsRecord or None.

def resolve_resources(spec: dict, settings, defaults=None) -> dict:
    """CR spec.resources -> container `resources` (requests==limits = Guaranteed QoS),
    inheriting the global default then the env seed when a field is omitted."""
    runner, _ = _split_resources(spec, settings, defaults)
    return _resource_block(*runner)


def resolve_terminal_resources(spec: dict, settings, defaults=None) -> dict:
    """The fixed Terminal share. Runner + Terminal always equals the committed total."""
    _, terminal = _split_resources(spec, settings, defaults)
    if terminal == (0, 0):
        return _resource_block(0, 0)
    return _resource_block(*terminal)


def resolve_storage_gb(spec: dict, settings, defaults=None) -> int:
    sg = spec.get("storageGb")
    if sg is None:
        sg = defaults.storage_gb if defaults else settings.kubernetes.runner_storage_gb
    return int(sg)


def resolve_image(spec: dict, settings) -> str:
    """spec.image (per-account CR override) wins; otherwise the operator's own
    deployment settings decide — the runner image is a platform-release concern,
    not a runtime default."""
    img = spec.get("image")
    if img:
        return img
    return settings.kubernetes.runner_image


def allocation_hash(
    spec: dict,
    settings,
    defaults,
    username: str = "",
    *,
    image: str | None = None,
    pull_policy: str | None = None,
    verification_key: str | None = None,
    verification_key_ring: tuple[str, ...] | None = None,
) -> str:
    """Fingerprint every value that can change the shared runtime allocation.

    Percentage alone is not an allocation generation: total CPU/memory, image and
    identity can change while it remains constant. Both Deployments carry this hash so
    the operator never starts a mixed generation that can overcommit a tenant. Terminal-
    only session/output policy has a separate template hash and does not restart Runner.
    """
    k = settings.kubernetes
    if verification_key_ring is None:
        verification_key_ring = (
            (verification_key,)
            if verification_key is not None
            else service_identity.verification_keys()
        )
    # Order is security state: verification_keys() puts the current signer's
    # public key first. During staged rotation the set is unchanged when
    # [old, new] becomes [new, old], but the permanent account-scoped service
    # token injected below must be re-minted by the new signer before old trust
    # can be removed. A sorted set hid that transition and stranded old-signed
    # tokens in dormant Deployment templates.
    verification_key_digests = list(dict.fromkeys(
        hashlib.sha256(key.encode()).hexdigest()
        for key in verification_key_ring
    ))
    payload = {
        "version": 4,
        "username": username,
        "image": image or resolve_image(spec, settings),
        "pullPolicy": pull_policy or getattr(k, "runner_image_pull_policy", "IfNotPresent"),
        "runnerResources": resolve_resources(spec, settings, defaults),
        "terminalResources": resolve_terminal_resources(spec, settings, defaults),
        "terminalPercent": resolve_terminal_percent(settings, defaults),
        "security": {
            "uid": int(getattr(k, "runner_uid", 10001)),
            "gid": int(getattr(k, "runner_gid", 10001)),
            # A signer rotation invalidates every token verified by the current
            # processes. Treat the public half as executable template state so
            # both runtimes converge at their next safe zero boundary.
            "verificationKeyDigests": verification_key_digests,
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "v1:" + hashlib.sha256(canonical.encode()).hexdigest()


def terminal_template_hash(
    spec: dict, settings, defaults, username: str = "", *, image: str | None = None,
    pull_policy: str | None = None, verification_key: str | None = None,
    verification_key_ring: tuple[str, ...] | None = None,
) -> str:
    """Fingerprint Terminal-only policy without forcing a Runner allocation restart."""
    k = settings.kubernetes
    if verification_key_ring is None:
        verification_key_ring = (
            (verification_key,)
            if verification_key is not None
            else service_identity.verification_keys()
        )
    verification_key_digests = list(dict.fromkeys(
        hashlib.sha256(key.encode()).hexdigest()
        for key in verification_key_ring
    ))
    payload = {
        "allocation": allocation_hash(
            spec, settings, defaults, username, image=image, pull_policy=pull_policy,
            verification_key_ring=verification_key_ring),
        "maxSessions": int(getattr(defaults, "terminal_max_sessions",
                                   getattr(k, "terminal_max_sessions", 2))),
        "idleTimeoutSeconds": int(getattr(
            defaults, "terminal_idle_timeout_seconds",
            getattr(k, "terminal_idle_timeout_seconds", 1800))),
        "maxLifetimeSeconds": int(getattr(
            defaults, "terminal_max_lifetime_seconds",
            getattr(k, "terminal_max_lifetime_seconds", 14400))),
        "outputRate": int(getattr(k, "terminal_output_rate_limit_bytes_per_sec", 256 * 1024)),
        "outputBurst": int(getattr(k, "terminal_output_burst_bytes", 1024 * 1024)),
        "outputBuffer": int(getattr(k, "terminal_output_buffer_bytes", 1024 * 1024)),
        "tmpSizeLimit": str(getattr(k, "terminal_tmp_size_limit", "256Mi")),
        # The public key is safe to distribute, but it is still part of the
        # executable security boundary. Rotating the signing key must replace a
        # dormant Terminal template before the next pod starts.
        "verificationKeyDigests": verification_key_digests,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "v1:" + hashlib.sha256(canonical.encode()).hexdigest()


# --- manifest builders ------------------------------------------------------

def _runner_tmp_size_limit(settings) -> str:
    return getattr(settings.kubernetes, "runner_tmp_size_limit", "512Mi")


def _data_volume(mount_info: MountInfo) -> dict:
    """The shared-export volume source for the runner's /workspace mount."""
    return {"name": "data", "persistentVolumeClaim": {"claimName": mount_info.claim}}


def _data_volume_mount(mount_info: MountInfo) -> dict:
    """Mount ONLY the account's subdir at /workspace — the runner gets no handle to
    siblings (isolation is a property of the mount). The reader RO-mounts the whole tree."""
    vm = {"name": "data", "mountPath": "/workspace"}
    if mount_info.sub_path:
        vm["subPath"] = mount_info.sub_path
    return vm


def egress_generation(iso, settings) -> str:
    """Stable fingerprint of the proxy env a pod template would carry."""
    from priva_operator import netpol
    env = netpol.proxy_env(iso, settings) if iso is not None else []
    canonical = json.dumps(env, sort_keys=True, separators=(",", ":"))
    return "e1:" + hashlib.sha256(canonical.encode()).hexdigest()[:16]


def applied_egress_generation(namespace, account_id) -> str | None:
    d = _read_deployment(namespace, account_id)
    if d is None:
        return None
    return (d.metadata.annotations or {}).get(_EGRESS_GENERATION_ANNOTATION)


def applied_terminal_egress_generation(namespace, account_id) -> str | None:
    d = _read_terminal_deployment(namespace, account_id)
    if d is None:
        return None
    return (d.metadata.annotations or {}).get(_EGRESS_GENERATION_ANNOTATION)


def _proxy_template_bits(iso, settings, base_labels):
    """(pod-template labels, extra env) for the egress proxy.

    Deliberately NOT folded into allocation_hash: the proxy env is ordinary
    template state that lands on the pod's next restart, exactly like the
    managed-policy mount. Adding it to the hash would make an admin's toggle
    invalidate every dormant runner's generation at once, and a hash computed
    inconsistently across the eight allocation_hash call sites would restart-loop.
    The template reaches the pod through the existing full-template converge
    (ensure on create/resume, on_wake's cold path).
    """
    from priva_operator import netpol
    env = netpol.proxy_env(iso, settings) if iso is not None else []
    # NetworkPolicy deliberately selects the stable app label. A second
    # "proxied" marker used to be stamped here but no policy consumed it; keeping
    # an unused security-looking label only obscures what actually enforces the
    # boundary.
    return dict(base_labels), env


def _deployment_body(
    namespace,
    account_id,
    username,
    image,
    pull_policy,
    settings,
    owner,
    spec,
    mount_info: MountInfo,
    defaults=None,
    iso=None,
    *,
    internal_drain_token: str | None = None,
) -> dict:
    lbl = names.labels(account_id)
    # Copy template labels because `lbl` is also used by the immutable selector.
    tmpl_lbl, proxy_env = _proxy_template_bits(iso, settings, lbl)
    terminal_percent = resolve_terminal_percent(settings, defaults)
    verification_key_ring = service_identity.verification_keys()
    verification_key = verification_key_ring[0]
    generation = allocation_hash(
        spec, settings, defaults, username, image=image, pull_policy=pull_policy,
        verification_key_ring=verification_key_ring)
    internal_drain_token = internal_drain_token or secrets.token_urlsafe(32)
    uid = int(settings.kubernetes.runner_uid)
    gid = int(settings.kubernetes.runner_gid)
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": names.deploy_name(account_id), "namespace": namespace,
                     "labels": lbl, "ownerReferences": [owner],
                     "annotations": {
                         _TERMINAL_PERCENT_ANNOTATION: str(terminal_percent),
                         _ALLOCATION_HASH_ANNOTATION: generation,
                         _EGRESS_GENERATION_ANNOTATION: egress_generation(iso, settings),
                     }},
        "spec": {
            "replicas": 0,  # scale-to-zero from birth; the operator is the sole scaler
            "strategy": {"type": "Recreate"},  # one pod per subPath; clean cutover on restart
            "selector": {"matchLabels": lbl},
            "template": {
                "metadata": {"labels": tmpl_lbl, "annotations": {
                    _TERMINAL_PERCENT_ANNOTATION: str(terminal_percent),
                    _ALLOCATION_HASH_ANNOTATION: generation,
                    _EGRESS_GENERATION_ANNOTATION: egress_generation(iso, settings),
                }},
                "spec": {
                    **({"imagePullSecrets": [{"name": settings.kubernetes.runner_image_pull_secret}]}
                       if settings.kubernetes.runner_image_pull_secret else {}),
                    # Non-root: run as the sandbox uid that owns /export/<account_id>. fsGroup
                    # makes the mount group-writable; OnRootMismatch skips the recursive chown
                    # once the quota-manager has already chowned the subdir (NFS root_squash).
                    "securityContext": {
                        "runAsNonRoot": True, "runAsUser": uid, "runAsGroup": gid,
                        "fsGroup": gid, "fsGroupChangePolicy": "OnRootMismatch",
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "hostPID": False,
                    "hostIPC": False,
                    "hostNetwork": False,
                    "containers": [{
                        "name": "agent-runner",
                        "image": image,
                        "imagePullPolicy": pull_policy,
                        "resources": resolve_resources(spec, settings, defaults),
                        "ports": [{"containerPort": settings.kubernetes.runner_service_port}],
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
                            "privileged": False,
                            "readOnlyRootFilesystem": True,
                            "capabilities": {"drop": ["ALL"]},
                        },
                        "env": [
                            {"name": "ACCOUNT_ID", "value": account_id},
                            {"name": "USERNAME", "value": username},
                            {"name": "AGENT_RUNNER_HOST", "value": "0.0.0.0"},
                            {"name": "AGENT_RUNNER_PORT", "value": str(settings.kubernetes.runner_service_port)},
                            {"name": "PRIVA_SERVER__WORK_DIR", "value": "/workspace"},
                            {"name": "WORKSPACE_DIR", "value": "/workspace"},
                            {"name": "PRIVA_HOME", "value": "/workspace/.priva"},
                            {"name": "CLAUDE_CONFIG_DIR", "value": "/workspace/.claude"},
                            # Per-account writable dir for hook context. Passed through the
                            # fire-log wrapper so a global managed script CAN read per-account
                            # context from a fixed absolute path (no shipped seed uses it since
                            # risky-tools v3 embedded its patterns; kept for custom hooks).
                            {"name": "PRIVA_HOOK_DIR", "value": "/workspace/.priva/hook-context"},
                            # HOME must be writable on the volume (readOnlyRootFilesystem).
                            {"name": "HOME", "value": "/workspace/.home"},
                            # NOTE: no IS_SANDBOX — the claude CLI refuses
                            # --dangerously-skip-permissions only as root; running non-root
                            # (runAsUser above) satisfies it without the escape (byte-path.md).
                            # --- workload identity (replaces priva-shared-secret) ---
                            # PUBLIC key only: the runner verifies control-plane-minted
                            # runner tokens and can mint nothing. Public by definition, so
                            # inlining it is safe.
                            {"name": "PRIVA_SERVICE_IDENTITY__PUBLIC_KEY",
                             "value": verification_key},
                            {
                                "name": "PRIVA_SERVICE_IDENTITY__ADDITIONAL_PUBLIC_KEYS",
                                "value": json.dumps(
                                    list(verification_key_ring[1:]),
                                    separators=(",", ":"),
                                ),
                            },
                            # Per-Pod lifecycle capability. It deliberately does
                            # not derive from the service signing key, so the new
                            # Operator can drain a Pod which still trusts the old
                            # key during a controlled rotation.
                            {"name": drain_token.ENV, "value": internal_drain_token},
                            # Account-scoped capability, NOT a signing key. The tenant can
                            # read this out of their own env and gain only what this account
                            # already has: data-spine's ACL pins it to account_id and to the
                            # narrow method set the runner actually uses (data_spine/authz.py).
                            {"name": "PRIVA_DATASPINE__SERVICE_TOKEN",
                             "value": service_token.mint("agent-runner", account_id=account_id)},
                            # Stable egress-proxy environment in every mode. Platform
                            # data-spine/scheduler clients explicitly disable proxy
                            # handling, so NO_PROXY stays localhost-only and tenant tools
                            # cannot inherit a broad `.svc` escape from the public path.
                            *proxy_env,
                        ],
                        "envFrom": [
                            {"configMapRef": {"name": "priva-config"}},
                            # NO priva-shared-secret. It carries the platform JWT signing
                            # secret and the api-key HMAC secret; mounting them here put
                            # them in reach of the tenant's own agent (plain `env`, or
                            # /proc/self/environ via the file API), which yielded a forged
                            # `sub: "admin"` login JWT — full platform takeover. The
                            # terminal pod has always omitted this; the runner was the
                            # un-fixed twin.
                            # No per-account creds Secret: BYOK creds live in the pod's own
                            # /workspace/.claude/settings.json on the PVC, read by the CLI.
                        ],
                        "volumeMounts": [
                            _data_volume_mount(mount_info),
                            # readOnlyRootFilesystem → give the CLI/node a writable scratch.
                            {"name": "tmp", "mountPath": "/tmp"},
                            # Global managed policy (enforced admin hooks). Whole-dir,
                            # NO subPath → ConfigMap edits hot-sync into the running pod.
                            {"name": MANAGED_POLICY_VOLUME, "mountPath": MANAGED_POLICY_MOUNT,
                             "readOnly": True},
                        ],
                        "readinessProbe": {
                            "httpGet": {"path": "/health", "port": settings.kubernetes.runner_service_port},
                            "initialDelaySeconds": 2, "periodSeconds": 3, "failureThreshold": 30,
                        },
                    }],
                    "volumes": [
                        _data_volume(mount_info),
                        # Bounded: Starlette spools multipart uploads here before any
                        # route code runs, so an unbounded emptyDir lets a tenant fill
                        # the node's ephemeral storage and get the pod evicted. The
                        # terminal pod has always capped its own /tmp.
                        {"name": "tmp", "emptyDir": {"sizeLimit": _runner_tmp_size_limit(settings)}},
                        # optional:False → a pod will NOT start without the enforced
                        # admin hook policy. This mount IS the enforcement: with
                        # optional:True a data-spine blip during the operator's
                        # render left the ConfigMap absent, the pod started with an
                        # empty policy dir, and every enforced hook silently stopped
                        # firing while the runner still reported Ready — a security
                        # control that disappears on error is not a control.
                        # Availability trade-off is deliberate: no policy, no runner.
                        # The operator creates the CM at startup (reconcile.py
                        # bootstrap_managed_policy) before any pod mounts it.
                        {"name": MANAGED_POLICY_VOLUME,
                         "configMap": {"name": MANAGED_POLICY_CM, "optional": False}},
                    ],
                },
            },
        },
    }


def ensure_managed_policy_configmap(namespace, *, strict: bool = False) -> bool:
    """Render the global enforced-hook policy into the shared managed-policy CM.

    Reads enforced+enabled command policies from data-spine and renders the
    Claude Code managed-settings.json + hook scripts + fire-log wrapper (see
    priva_common.managed_policy_render). Idempotent: a digest annotation skips
    the write when the enforced set is unchanged, so calling it from every
    per-account handler costs one read and no write in steady state. Fail-soft:
    a data-spine blip logs and returns (pods fail-open on the optional mount).
    Returns True when a create/replace was performed.

    Fail-soft by default (periodic reconcile), fail-loud under ``strict`` (boot).
    NOTE: the mount is optional:False since the enforcement hardening — a missing
    ConfigMap blocks every runner pod, it does not fail open.
    """
    try:
        from priva_common.dataplane import get_client
        from priva_common import managed_policy_render as render

        rows = get_client().hook_policies.list(enabled_only=True)
        enforced = [p for p in rows if p.enforced and p.hook_type == "command"]
        data = render.render_config_map_data(enforced)
    except Exception:
        # strict=True is the startup gate: the runner mounts this ConfigMap with
        # optional:False, so "render skipped" is no longer a degraded mode — it
        # means no tenant pod can start. Swallowing it here made the caller's
        # raise unreachable for the very case its error message names.
        if strict:
            raise
        logger.warning("managed-policy render skipped (data-spine unreachable?)", exc_info=True)
        return False

    digest = render.content_digest(data)
    existing = None
    try:
        existing = core().read_namespaced_config_map(MANAGED_POLICY_CM, namespace)
    except client.ApiException as exc:
        if exc.status != 404:
            raise

    meta = {"name": MANAGED_POLICY_CM, "namespace": namespace,
            "annotations": {_POLICY_DIGEST_ANNOTATION: digest}}
    if existing is None:
        body = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": meta, "data": data}
        _ignore_conflict(core().create_namespaced_config_map, namespace, body)
        logger.info("created {} ({} enforced hooks)", MANAGED_POLICY_CM, len(enforced))
        return True

    if (existing.metadata.annotations or {}).get(_POLICY_DIGEST_ANNOTATION) == digest:
        return False  # enforced set unchanged
    # Retain the prior generation's scripts so an in-flight session whose
    # settings still point at the old hash keeps working until it ends.
    data = render.merge_generations(data, existing.data or {})
    meta["resourceVersion"] = existing.metadata.resource_version
    body = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": meta, "data": data}
    try:
        core().replace_namespaced_config_map(MANAGED_POLICY_CM, namespace, body)
        logger.info("updated {} ({} enforced hooks)", MANAGED_POLICY_CM, len(enforced))
        return True
    except client.ApiException as exc:
        if exc.status == 409:  # a sibling handler won the race with identical content
            return False
        raise


def _isolation_inputs(strict: bool, what: str):
    """(settings, isolation-record), or None when data-spine is unreachable and
    we're allowed to skip. Shared so one converge reads the record once."""
    try:
        from priva_common.config import get_settings
        from priva_common.dataplane import get_client
        return get_settings(), get_client().network_isolation.get()
    except Exception:
        if strict:
            raise
        logger.warning("{} render skipped (data-spine unreachable?)", what, exc_info=True)
        return None


def _isolation_snapshot_json(iso) -> str:
    """Serialize only the semantic isolation record needed after a restart."""
    payload = {
        "version": ISOLATION_SNAPSHOT_VERSION,
        "record": {
            "runner_deny_internal": bool(
                getattr(iso, "runner_deny_internal", False)
            ),
            "terminal_deny_internal": bool(
                getattr(iso, "terminal_deny_internal", False)
            ),
            "deny_tenant_peers": bool(
                getattr(iso, "deny_tenant_peers", False)
            ),
            "egress_mode": str(getattr(iso, "egress_mode", "deny_all")),
            "egress_allowlist": [
                {
                    "host": str(getattr(entry, "host", "")),
                    "port": int(getattr(entry, "port", 0) or 0),
                }
                for entry in (getattr(iso, "egress_allowlist", None) or ())
            ],
            "updated_at": getattr(iso, "updated_at", None),
        },
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def persist_isolation_snapshot(namespace: str, iso, settings) -> bool:
    """Persist a verified last-known-good record for Operator restart recovery.

    The intent annotation binds this record to all topology inputs from the
    current Operator configuration. A later CIDR/DNS/config change therefore
    invalidates the snapshot instead of replaying it under different semantics.
    """
    intent = isolation_intent_digest(iso, settings)
    body = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": ISOLATION_SNAPSHOT_CONFIG_MAP,
            "namespace": namespace,
            "labels": dict(_ISOLATION_SNAPSHOT_LABELS),
            "annotations": {ISOLATION_INTENT_ANNOTATION: intent},
        },
        "data": {ISOLATION_SNAPSHOT_KEY: _isolation_snapshot_json(iso)},
    }
    wrote = _apply_cm(namespace, body)
    # A create/replace conflict is never proof that the winner contains the same
    # security record. Re-read and validate the exact desired object before the
    # in-memory applied generation is advanced.
    live = core().read_namespaced_config_map(
        ISOLATION_SNAPSHOT_CONFIG_MAP,
        namespace,
        _request_timeout=_KUBE_REQUEST_TIMEOUT,
    )
    if (
        (live.data or {}) != body["data"]
        or any(
            (live.metadata.labels or {}).get(key) != value
            for key, value in _ISOLATION_SNAPSHOT_LABELS.items()
        )
        or (live.metadata.annotations or {}).get(
            ISOLATION_INTENT_ANNOTATION
        ) != intent
    ):
        raise RuntimeError(
            "network isolation snapshot changed during reconcile; refusing "
            "to advance the applied generation"
        )
    return wrote


def load_isolation_snapshot(namespace: str, settings):
    """Return a topology-bound persisted isolation record, or ``None``.

    This validates the snapshot itself only. The caller must additionally prove
    that the live CNI fact, NetworkPolicies and proxy still implement its intent
    before treating it as an applied last-known-good generation.
    """
    from priva_common.dataplane import NetworkIsolationRecord

    try:
        cm = core().read_namespaced_config_map(
            ISOLATION_SNAPSHOT_CONFIG_MAP,
            namespace,
            _request_timeout=_KUBE_REQUEST_TIMEOUT,
        )
    except client.ApiException as exc:
        if exc.status == 404:
            return None
        raise
    if any(
        (cm.metadata.labels or {}).get(key) != value
        for key, value in _ISOLATION_SNAPSHOT_LABELS.items()
    ):
        return None
    try:
        payload = json.loads(
            (cm.data or {}).get(ISOLATION_SNAPSHOT_KEY, "")
        )
        if not isinstance(payload, dict):
            return None
        if payload.get("version") != ISOLATION_SNAPSHOT_VERSION:
            return None
        record_data = payload["record"]
        required = {
            "runner_deny_internal",
            "terminal_deny_internal",
            "deny_tenant_peers",
            "egress_mode",
            "egress_allowlist",
        }
        if not isinstance(record_data, dict) or not required.issubset(record_data):
            return None
        record = NetworkIsolationRecord.model_validate(record_data)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    intent = (cm.metadata.annotations or {}).get(
        ISOLATION_INTENT_ANNOTATION
    )
    if not intent or isolation_intent_digest(record, settings) != intent:
        return None
    return record


def isolation_snapshot_resources_ready(namespace: str, iso, settings) -> bool:
    """Validate every live boundary object before accepting a persisted LKG."""
    from priva_operator import egress_proxy

    intent = isolation_intent_digest(iso, settings)
    config_sha = egress_proxy.config_sha256(
        egress_proxy.render_squid_conf(iso, settings)
    )
    return bool(
        network_policy_enforced(namespace, settings)
        and network_policies_ready(namespace, iso, settings)
        and egress_proxy_ready(
            namespace,
            expected_intent=intent,
            expected_config_sha=config_sha,
            settings=settings,
            require_all_replicas=False,
        )
    )


def ensure_isolation(
    namespace, *, strict: bool = False, iso=None, settings=None,
) -> bool:
    """Converge the egress proxy and the tenant NetworkPolicies together.

    A changed or drifted proxy is first scaled to zero and its old Pods are
    removed, then updated behind a temporary deny-public NetworkPolicy
    generation. Only after the exact ConfigMap/Deployment/Service binding is
    Ready do we restore the requested rules. Thus neither a partial policy write
    nor a ConfigMap write followed by a failed Deployment update can leave an
    older, wider Squid reachable.
    """
    from priva_operator import egress_proxy

    if iso is None or settings is None:
        inputs = _isolation_inputs(strict, "isolation")
        if inputs is None:
            return False
        settings, iso = inputs

    intent_digest = isolation_intent_digest(iso, settings)
    config_sha = egress_proxy.config_sha256(
        egress_proxy.render_squid_conf(iso, settings)
    )
    proxy_matches = egress_proxy_ready(
        namespace,
        expected_intent=intent_digest,
        expected_config_sha=config_sha,
        settings=settings,
        require_all_replicas=False,
    )
    wrote = False
    if not proxy_matches:
        # NetworkPolicy objects are updated one at a time and their allow rules
        # are unioned, so a half-written "deny_all" generation is not an atomic
        # quarantine. Remove every old proxy endpoint first and wait for its Pods
        # to disappear. From that point a partial policy/config write can cause
        # only an outage, never continued access through an older wider Squid.
        wrote = quiesce_egress_proxy(
            namespace,
            timeout=float(settings.kubernetes.wake_timeout_seconds),
        )
        quarantine = SimpleNamespace(
            runner_deny_internal=bool(
                getattr(iso, "runner_deny_internal", False)
            ),
            terminal_deny_internal=bool(
                getattr(iso, "terminal_deny_internal", False)
            ),
            deny_tenant_peers=bool(
                getattr(iso, "deny_tenant_peers", False)
            ),
            egress_mode="deny_all",
            egress_allowlist=[],
        )
        # This list/apply also detects unknown NetworkPolicy union conflicts
        # before any proxy configuration is changed.
        wrote = ensure_network_policies(
            namespace,
            strict=strict,
            iso=quarantine,
            settings=settings,
        ) or wrote
        wrote = ensure_egress_proxy(
            namespace, strict=strict, iso=iso, settings=settings
        ) or wrote
        if not wait_egress_proxy_ready(
            namespace,
            timeout=float(settings.kubernetes.wake_timeout_seconds),
            expected_intent=intent_digest,
            expected_config_sha=config_sha,
            settings=settings,
        ):
            # Deliberately leave the quarantine generation installed. A later
            # reconcile retries from this fail-closed state.
            raise RuntimeError(
                "egress proxy did not become Ready for the desired isolation generation"
            )
    else:
        # Readiness covers the ConfigMap/Deployment binding. Still converge the
        # Service and all security-critical workload fields for drift.
        wrote = ensure_egress_proxy(
            namespace, strict=strict, iso=iso, settings=settings
        )

    return ensure_network_policies(
        namespace, strict=strict, iso=iso, settings=settings) or wrote


def ensure_network_policies(namespace, *, strict: bool = False, iso=None, settings=None) -> bool:
    """Converge the tenant-isolation NetworkPolicy set to the admin settings.

    Namespace-scoped, not per-account: the policies select by pod class
    (app=agent-runner / app=terminal), so one set covers every tenant. Per-account
    egress differences live in the proxy's allowlist, not in NetworkPolicy — a
    CIDR cannot express a domain, which is the whole reason the proxy exists.

    Idempotent via a digest annotation, same as the managed-policy CM: N
    per-account handlers calling this every tick cost one list and no writes in
    steady state. Deletes policies it previously created but no longer wants, so
    turning a switch off actually re-opens the path.

    Data-spine reads are fail-soft by default (periodic reconcile) and fail-loud
    under ``strict`` (boot). A policy-union conflict is always fail-loud: silently
    proceeding would report a boundary applied while another object re-opens it.
    Returns True when anything was written or deleted.
    """
    from priva_operator import netpol
    if iso is None or settings is None:
        inputs = _isolation_inputs(strict, "network-policy")
        if inputs is None:
            return False
        settings, iso = inputs
    desired = {p["metadata"]["name"]: p
               for p in netpol.build_policies(iso, settings, namespace)}
    intent_digest = isolation_intent_digest(iso, settings)

    digest = hashlib.sha256(
        json.dumps([desired[k]["spec"] for k in sorted(desired)], sort_keys=True).encode()
    ).hexdigest()[:16]

    # List all objects, not just those still carrying our labels. If an
    # out-of-band edit strips the managed label from an identically named policy,
    # a selector-only lookup sees it as absent and create loops on 409 forever,
    # leaving the widened spec in force.
    all_existing = {
        p.metadata.name: p
        for p in networking().list_namespaced_network_policy(
            namespace,
            _request_timeout=_KUBE_REQUEST_TIMEOUT,
        ).items
    }
    # Known superseded objects are ours to remove. Several legacy rules were
    # permissive by design, but treating them as an unknown conflict would make
    # the upgrade impossible to self-heal. Unknown objects are never deleted.
    for name in set(netpol.LEGACY_POLICIES) & set(all_existing):
        _delete_network_policy(namespace, name)
        del all_existing[name]
        logger.info("pruned superseded NetworkPolicy {}", name)

    owned_names = {
        netpol.RUNNER_EGRESS,
        netpol.TERMINAL_EGRESS,
        netpol.RUNNER_INGRESS,
        netpol.TERMINAL_INGRESS,
        netpol.PROXY_POLICY,
    }

    def actual_spec(policy):
        spec = policy.spec
        if isinstance(spec, dict):
            return spec
        return client.ApiClient().sanitize_for_serialization(spec)

    conflicts = sorted(
        name
        for name, policy in all_existing.items()
        if name not in owned_names
        and name not in netpol.LEGACY_POLICIES
        and policy_may_widen_tenant_runtime(actual_spec(policy))
    )
    if conflicts:
        # NetworkPolicy allow rules are unioned. Repairing our own objects cannot
        # compensate for this, and deleting an object we do not own would be an
        # unsafe ownership violation. Leave it intact for an administrator to
        # inspect and resolve, and block the reconcile instead.
        raise IsolationConflictError(conflicts)

    existing = {
        name: policy
        for name, policy in all_existing.items()
        if name in desired
        if all(
            (policy.metadata.labels or {}).get(key) == value
            for key, value in netpol.MANAGED_LABELS.items()
        )
    }

    # Every managed object carries the same set digest, so one unchanged object
    # is only trustworthy if the NAME SET also matches — otherwise a policy that
    # should have been deleted would keep the digest green forever.
    # Do not trust our own digest annotation by itself. An out-of-band editor can
    # mutate spec while leaving metadata untouched; name+digest would then report
    # green forever. Compare the live API representation as well.
    actual_owned_names = set(all_existing) & owned_names
    if actual_owned_names == set(desired) and set(existing) == set(desired) and all(
            (p.metadata.annotations or {}).get(_POLICY_DIGEST_ANNOTATION) == digest
            and (p.metadata.annotations or {}).get(
                ISOLATION_INTENT_ANNOTATION
            ) == intent_digest
            and actual_spec(p) == desired[name]["spec"]
            for name, p in existing.items()):
        # Close the list→return race for an old manifest being re-applied by a
        # stale deploy job while this reconcile was in flight.
        _prune_legacy_policies(namespace)
        return False

    wrote = False
    for name, body in desired.items():
        annotations = body["metadata"].setdefault("annotations", {})
        annotations[_POLICY_DIGEST_ANNOTATION] = digest
        annotations[ISOLATION_INTENT_ANNOTATION] = intent_digest
        # Adopt/repair a same-named object even if its ownership labels drifted.
        prior = all_existing.get(name)
        if prior is None:
            # A 409 means something appeared after the authoritative list. Do
            # not call that success: the winner may carry an older/wider spec.
            # Propagate so Kopf retries from a fresh list before advancing the
            # cross-service isolation generation.
            networking().create_namespaced_network_policy(
                namespace,
                body,
                _request_timeout=_KUBE_REQUEST_TIMEOUT,
            )
            logger.info("created NetworkPolicy {}", name)
            wrote = True
            continue
        body["metadata"]["resourceVersion"] = prior.metadata.resource_version
        # Isolation converges under one process-wide lock and the chart enforces
        # one standalone operator replica. A conflict is therefore external
        # drift or a stale API snapshot, not a safe identical sibling write.
        networking().replace_namespaced_network_policy(
            name,
            namespace,
            body,
            _request_timeout=_KUBE_REQUEST_TIMEOUT,
        )
        logger.info("updated NetworkPolicy {}", name)
        wrote = True

    # Delete only reserved operator policy names which are no longer desired.
    # A caller can copy our ownership labels onto an unrelated name; labels alone
    # are not authority to delete that object.
    for name in (owned_names - set(desired)) & set(all_existing):
        _delete_network_policy(namespace, name)
        logger.info("deleted NetworkPolicy {} (no longer desired)", name)
        wrote = True

    _prune_legacy_policies(namespace)
    return wrote


def network_policies_ready(namespace: str, iso, settings) -> bool:
    """Prove the live policy union implements one persisted isolation snapshot."""
    from priva_operator import netpol

    desired = {
        policy["metadata"]["name"]: policy
        for policy in netpol.build_policies(iso, settings, namespace)
    }
    intent = isolation_intent_digest(iso, settings)
    digest = hashlib.sha256(
        json.dumps(
            [desired[name]["spec"] for name in sorted(desired)],
            sort_keys=True,
        ).encode()
    ).hexdigest()[:16]
    try:
        policies = networking().list_namespaced_network_policy(
            namespace,
            _request_timeout=_KUBE_REQUEST_TIMEOUT,
        ).items
    except client.ApiException:
        raise
    all_existing = {policy.metadata.name: policy for policy in policies}
    owned_names = {
        netpol.RUNNER_EGRESS,
        netpol.TERMINAL_EGRESS,
        netpol.RUNNER_INGRESS,
        netpol.TERMINAL_INGRESS,
        netpol.PROXY_POLICY,
    }
    if set(netpol.LEGACY_POLICIES) & set(all_existing):
        return False
    if (set(all_existing) & owned_names) != set(desired):
        return False

    def actual_spec(policy):
        spec = policy.spec
        if isinstance(spec, dict):
            return spec
        return client.ApiClient().sanitize_for_serialization(spec)

    if any(
        name not in owned_names
        and name not in netpol.LEGACY_POLICIES
        and policy_may_widen_tenant_runtime(actual_spec(policy))
        for name, policy in all_existing.items()
    ):
        return False
    for name, body in desired.items():
        policy = all_existing.get(name)
        if policy is None:
            return False
        if any(
            (policy.metadata.labels or {}).get(key) != value
            for key, value in netpol.MANAGED_LABELS.items()
        ):
            return False
        annotations = policy.metadata.annotations or {}
        if (
            annotations.get(_POLICY_DIGEST_ANNOTATION) != digest
            or annotations.get(ISOLATION_INTENT_ANNOTATION) != intent
            or actual_spec(policy) != body["spec"]
        ):
            return False
    return True


def ensure_egress_proxy(namespace, *, strict: bool = False, iso=None, settings=None) -> bool:
    """Converge the Squid egress proxy to the admin settings.

    It exists in every mode: unrestricted/allowlist/deny_all are Squid policy
    modes, not different pod topologies. A supplied ``iso``/``settings`` pair is
    one authoritative snapshot and must not be re-read halfway through render.
    """
    from priva_operator import egress_proxy
    if iso is None or settings is None:
        inputs = _isolation_inputs(strict, "egress-proxy")
        if inputs is None:
            return False
        settings, iso = inputs

    conf = egress_proxy.render_squid_conf(iso, settings)
    intent_digest = isolation_intent_digest(iso, settings)
    config_body = egress_proxy.config_map_body(
        namespace, conf, intent_digest=intent_digest
    )
    wrote = _apply_cm(namespace, config_body)
    # subPath mounts never receive ConfigMap projection updates. Bind the live
    # ConfigMap resourceVersion into the Pod template so repairing data drift
    # necessarily changes the Deployment template and rolls every Squid process.
    config_revision = _verified_config_map_revision(namespace, config_body)
    wrote = _apply_service(
        namespace, egress_proxy.service_body(namespace, settings)) or wrote

    body = egress_proxy.deployment_body(
        namespace,
        conf,
        settings,
        config_revision=config_revision,
        intent_digest=intent_digest,
    )
    digest = body["spec"]["template"]["metadata"]["annotations"][
        egress_proxy.CONFIG_DIGEST_ANNOTATION]
    try:
        existing = apps().read_namespaced_deployment(
            egress_proxy.NAME,
            namespace,
            _request_timeout=_KUBE_REQUEST_TIMEOUT,
        )
    except client.ApiException as exc:
        if exc.status != 404:
            raise
        # A conflicting winner is unverified and could be an old unrestricted
        # proxy. Let the reconcile fail and retry from a fresh read.
        apps().create_namespaced_deployment(namespace, body)
        logger.info("created egress proxy {}", egress_proxy.NAME)
        return True

    if _proxy_deployment_matches(existing, body, digest):
        return wrote
    # The digest lives on the POD TEMPLATE, so writing it replaces the pods —
    # squid re-reads neither a subPath ConfigMap update nor the admin record.
    # Recreate is intentional: preserving an old, wider Ready generation when a
    # tightened replacement cannot start would be fail-open.
    body["metadata"]["resourceVersion"] = existing.metadata.resource_version
    apps().replace_namespaced_deployment(egress_proxy.NAME, namespace, body)
    logger.info("rolled egress proxy for new policy/config ({})", digest)
    return True


def _desired_subset(actual, desired) -> bool:
    """Whether every desired field is present, with lists owned exactly."""
    if isinstance(desired, dict):
        return isinstance(actual, dict) and all(
            (
                key in actual and _desired_subset(actual[key], value)
            )
            or (key not in actual and value is False)
            for key, value in desired.items()
        )
    if isinstance(desired, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(desired)
            and all(
                _desired_subset(actual_value, desired_value)
                for actual_value, desired_value in zip(
                    actual, desired, strict=True
                )
            )
        )
    return actual == desired


def _proxy_deployment_matches(existing, desired: dict, digest: str) -> bool:
    """Check the live security-critical proxy workload, not its annotation alone."""
    actual = (
        existing
        if isinstance(existing, dict)
        else client.ApiClient().sanitize_for_serialization(existing)
    )
    annotations = (
        (((actual.get("spec") or {}).get("template") or {}).get("metadata") or {})
        .get("annotations")
        or {}
    )
    if annotations.get(
        "priva.io/egress-proxy-digest"
    ) != digest or not _desired_subset(actual.get("spec"), desired.get("spec")):
        return False

    pod = (((actual.get("spec") or {}).get("template") or {}).get("spec") or {})
    # Fields omitted from the desired manifest are defaults only. These ones
    # would add executable code, credentials or privileges and are never allowed.
    if (
        any(
            pod.get(key)
            for key in (
                "hostNetwork",
                "hostPID",
                "hostIPC",
                "shareProcessNamespace",
            )
        )
        or pod.get("initContainers")
        or (pod.get("securityContext") or {}).get("sysctls")
    ):
        return False
    containers = pod.get("containers") or []
    if len(containers) != 1:
        return False
    container = containers[0]
    if any(
        container.get(key)
        for key in ("command", "env", "envFrom", "lifecycle", "volumeDevices")
    ):
        return False
    capabilities = (container.get("securityContext") or {}).get("capabilities") or {}
    return not (capabilities.get("add") or [])


def _apply_service(namespace, body) -> bool:
    """Create or patch the fields owned by the operator.

    Create-only services silently retain an old proxy port/selector after a
    config change. A merge patch avoids replacing immutable/defaulted ClusterIP
    fields while still closing that drift.
    """
    name = body["metadata"]["name"]
    try:
        existing = core().read_namespaced_service(
            name,
            namespace,
            _request_timeout=_KUBE_REQUEST_TIMEOUT,
        )
    except client.ApiException as exc:
        if exc.status != 404:
            raise
        # Do not accept an unverified same-name Service (for example a
        # concurrently-created LoadBalancer exposing the forward proxy).
        core().create_namespaced_service(namespace, body)
        return True

    if _proxy_service_matches(existing, body):
        return False
    desired_meta = body.get("metadata") or {}
    desired_spec = body.get("spec") or {}
    # JSON merge patch replaces the ports list instead of strategic-merging it
    # by port number (which would retain an attacker-added NodePort/extra port).
    # Null the exposure-only fields while deliberately omitting clusterIP(s), so
    # immutable Service identity survives the repair.
    ports = [
        {**port, "nodePort": None}
        for port in (desired_spec.get("ports") or [])
    ]
    patch = {
        "metadata": {"labels": desired_meta.get("labels") or {}},
        "spec": {
            "type": "ClusterIP",
            "selector": desired_spec.get("selector"),
            "ports": ports,
            "externalIPs": [],
            "externalName": None,
            "loadBalancerIP": None,
            "loadBalancerClass": None,
            "loadBalancerSourceRanges": [],
            "externalTrafficPolicy": None,
            "healthCheckNodePort": None,
            "allocateLoadBalancerNodePorts": None,
        },
    }
    core().patch_namespaced_service(
        name,
        namespace,
        patch,
        _content_type="application/merge-patch+json",
    )
    return True


def _proxy_service_matches(existing, desired: dict) -> bool:
    """Check that the proxy Service has no external exposure or extra ports."""
    actual = (
        existing
        if isinstance(existing, dict)
        else client.ApiClient().sanitize_for_serialization(existing)
    )
    actual_meta = actual.get("metadata") or {}
    actual_spec = actual.get("spec") or {}
    desired_meta = desired.get("metadata") or {}
    desired_spec = desired.get("spec") or {}
    desired_ports = desired_spec.get("ports") or []
    actual_ports = actual_spec.get("ports") or []
    owned_port_keys = ("name", "port", "targetPort", "protocol")
    ports_match = len(actual_ports) == len(desired_ports) and all(
        {key: actual_port.get(key) for key in owned_port_keys}
        == {key: desired_port.get(key) for key in owned_port_keys}
        and not actual_port.get("nodePort")
        for actual_port, desired_port in zip(
            actual_ports, desired_ports, strict=True
        )
    )
    return bool(
        all(
            (actual_meta.get("labels") or {}).get(key) == value
            for key, value in (desired_meta.get("labels") or {}).items()
        )
        and actual_spec.get("selector") == desired_spec.get("selector")
        and actual_spec.get("type", "ClusterIP") == "ClusterIP"
        and not (actual_spec.get("externalIPs") or [])
        and not actual_spec.get("externalName")
        and not actual_spec.get("loadBalancerIP")
        and not actual_spec.get("loadBalancerClass")
        and not (actual_spec.get("loadBalancerSourceRanges") or [])
        and not actual_spec.get("healthCheckNodePort")
        and ports_match
    )


def quiesce_egress_proxy(namespace, *, timeout: float) -> bool:
    """Scale the proxy to zero and prove every old endpoint process is gone.

    This is the atomic cut in a security-policy rollout. NetworkPolicy has no
    deny rule that can override an existing allow, and multiple policy objects
    cannot be transacted together. A zero-endpoint proxy makes every subsequent
    partial failure fail closed.
    """
    from priva_operator import egress_proxy

    api = apps()
    wrote = False
    try:
        deployment = api.read_namespaced_deployment(
            egress_proxy.NAME,
            namespace,
            _request_timeout=_KUBE_REQUEST_TIMEOUT,
        )
    except client.ApiException as exc:
        if exc.status != 404:
            raise
        deployment = None
    if deployment is not None:
        replicas = int(getattr(deployment.spec, "replicas", 0) or 0)
        if replicas != 0:
            api.patch_namespaced_deployment(
                egress_proxy.NAME,
                namespace,
                {"spec": {"replicas": 0}},
                _request_timeout=_KUBE_REQUEST_TIMEOUT,
            )
            wrote = True

    deadline = time.monotonic() + max(0.0, timeout)
    selector = f"app={egress_proxy.APP_LABEL}"
    core_api = core()
    deletion_requested: set[str] = set()
    while True:
        pods = core_api.list_namespaced_pod(
            namespace,
            label_selector=selector,
            _request_timeout=_KUBE_REQUEST_TIMEOUT,
        ).items
        if not pods:
            return wrote
        # Scaling the Deployment is the durable gate; explicit deletion closes
        # terminating/orphan endpoints promptly and covers a missing Deployment
        # whose old labelled Pod would otherwise remain selected by the Service.
        for pod in pods:
            pod_name = str(getattr(pod.metadata, "name", "") or "")
            if not pod_name or pod_name in deletion_requested:
                continue
            try:
                core_api.delete_namespaced_pod(
                    pod_name,
                    namespace,
                    grace_period_seconds=0,
                    _request_timeout=_KUBE_REQUEST_TIMEOUT,
                )
            except client.ApiException as exc:
                if exc.status != 404:
                    raise
            deletion_requested.add(pod_name)
            wrote = True
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "old egress proxy Pods did not terminate; isolation rollout remains blocked"
            )
        time.sleep(0.25)


def egress_proxy_ready(
    namespace,
    *,
    expected_intent: str | None = None,
    expected_config_sha: str | None = None,
    settings=None,
    require_all_replicas: bool = True,
) -> bool:
    """Whether the expected proxy generation has enough Ready replicas.

    A security-policy rollout requires every desired replica before restoring
    public egress. Steady-state runtime checks require at least one verified
    endpoint: Kubernetes removes an unready replica from the Service, so
    quiescing every tenant when its redundant peer is briefly unavailable turns
    redundancy into a global outage without strengthening the boundary.
    """
    from priva_operator import egress_proxy

    try:
        deployment = apps().read_namespaced_deployment(
            egress_proxy.NAME,
            namespace,
            _request_timeout=_KUBE_REQUEST_TIMEOUT,
        )
    except client.ApiException as exc:
        if exc.status == 404:
            return False
        raise
    desired = int(getattr(deployment.spec, "replicas", 0) or 0)
    status = getattr(deployment, "status", None)
    if desired <= 0 or status is None:
        return False
    generation = int(getattr(deployment.metadata, "generation", 0) or 0)
    observed = int(getattr(status, "observed_generation", 0) or 0)
    required = desired if require_all_replicas else 1
    rollout_ready = (
        observed >= generation
        and int(getattr(status, "updated_replicas", 0) or 0) >= required
        and int(getattr(status, "ready_replicas", 0) or 0) >= required
        and int(getattr(status, "available_replicas", 0) or 0) >= required
    )
    if not rollout_ready:
        return False
    if expected_intent is None and expected_config_sha is None and settings is None:
        return True

    try:
        config_map = core().read_namespaced_config_map(
            egress_proxy.CONFIG_MAP,
            namespace,
            _request_timeout=_KUBE_REQUEST_TIMEOUT,
        )
    except client.ApiException as exc:
        if exc.status == 404:
            return False
        raise
    conf = (getattr(config_map, "data", None) or {}).get(
        egress_proxy.CONFIG_KEY
    ) or ""
    if not conf:
        return False
    actual_sha = egress_proxy.config_sha256(conf)
    if expected_config_sha is not None and actual_sha != expected_config_sha:
        return False
    cm_annotations = getattr(config_map.metadata, "annotations", None) or {}
    revision = str(
        getattr(config_map.metadata, "resource_version", None) or ""
    )
    template_annotations = (
        getattr(
            getattr(deployment.spec, "template", None),
            "metadata",
            None,
        )
    )
    template_annotations = (
        getattr(template_annotations, "annotations", None) or {}
    )
    binding_matches = bool(
        revision
        and cm_annotations.get(egress_proxy.PROXY_CONFIG_SHA256_ANNOTATION)
        == actual_sha
        and (
            expected_intent is None
            or cm_annotations.get(ISOLATION_INTENT_ANNOTATION)
            == expected_intent
        )
        and template_annotations.get(
            egress_proxy.PROXY_CONFIG_SHA256_ANNOTATION
        )
        == actual_sha
        and template_annotations.get(
            egress_proxy.PROXY_CONFIG_REVISION_ANNOTATION
        )
        == revision
        and (
            expected_intent is None
            or template_annotations.get(ISOLATION_INTENT_ANNOTATION)
            == expected_intent
        )
    )
    if not binding_matches or settings is None:
        return False

    desired_deployment = egress_proxy.deployment_body(
        namespace,
        conf,
        settings,
        config_revision=revision,
        intent_digest=expected_intent,
    )
    desired_digest = desired_deployment["spec"]["template"]["metadata"][
        "annotations"
    ][egress_proxy.CONFIG_DIGEST_ANNOTATION]
    if not _proxy_deployment_matches(
        deployment, desired_deployment, desired_digest
    ):
        return False
    try:
        service = core().read_namespaced_service(
            egress_proxy.NAME,
            namespace,
            _request_timeout=_KUBE_REQUEST_TIMEOUT,
        )
    except client.ApiException as exc:
        if exc.status == 404:
            return False
        raise
    return _proxy_service_matches(
        service, egress_proxy.service_body(namespace, settings)
    )


def wait_egress_proxy_ready(
    namespace,
    timeout: float = 60.0,
    *,
    expected_intent: str | None = None,
    expected_config_sha: str | None = None,
    settings=None,
    require_all_replicas: bool = True,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if egress_proxy_ready(
            namespace,
            expected_intent=expected_intent,
            expected_config_sha=expected_config_sha,
            settings=settings,
            require_all_replicas=require_all_replicas,
        ):
            return True
        time.sleep(0.5)
    return egress_proxy_ready(
        namespace,
        expected_intent=expected_intent,
        expected_config_sha=expected_config_sha,
        settings=settings,
        require_all_replicas=require_all_replicas,
    )


def network_policy_enforced(namespace, settings) -> bool:
    """Read the result of the functional ingress+egress CNI probe.

    The API accepting a NetworkPolicy proves only that the object is valid, not
    that the installed CNI drops packets. Production therefore gates tenant pod
    starts on the independently measured ConfigMap verdict.
    """
    if not bool(getattr(
            settings.kubernetes, "network_policy_probe_required", True)):
        return True
    from priva_common import network_isolation as isolation

    try:
        api = core()
        facts = api.read_namespaced_config_map(
            isolation.FACTS_CONFIG_MAP,
            namespace,
            _request_timeout=_KUBE_REQUEST_TIMEOUT,
        )
        cluster = api.read_namespace(
            "kube-system",
            _request_timeout=_KUBE_REQUEST_TIMEOUT,
        )
    except client.ApiException as exc:
        if exc.status == 404:
            return False
        raise
    data = facts.data or {}
    current_cluster_uid = str(
        getattr(getattr(cluster, "metadata", None), "uid", "") or ""
    )
    return (
        data.get(isolation.FACT_PROBE_VERSION) == isolation.PROBE_VERSION
        and data.get(isolation.FACT_ENFORCED) == "true"
        and data.get("networkPolicyIngressEnforced") == "true"
        and data.get("networkPolicyEgressEnforced") == "true"
        and data.get(isolation.FACT_ADDRESS_FAMILY) == "ipv4"
        and bool(current_cluster_uid)
        and data.get(isolation.FACT_CLUSTER_UID) == current_cluster_uid
        and isolation.probe_fact_is_fresh(
            data,
            int(settings.kubernetes.network_policy_probe_max_age_seconds),
        )
    )


def _apply_cm(namespace, body) -> bool:
    name = body["metadata"]["name"]
    try:
        existing = core().read_namespaced_config_map(
            name,
            namespace,
            _request_timeout=_KUBE_REQUEST_TIMEOUT,
        )
    except client.ApiException as exc:
        if exc.status != 404:
            raise
        _ignore_conflict(core().create_namespaced_config_map, namespace, body)
        return True
    existing_labels = existing.metadata.labels or {}
    existing_annotations = existing.metadata.annotations or {}
    desired_metadata = body.get("metadata") or {}
    if (
        (existing.data or {}) == body["data"]
        and all(
            existing_labels.get(key) == value
            for key, value in (desired_metadata.get("labels") or {}).items()
        )
        and all(
            existing_annotations.get(key) == value
            for key, value in (desired_metadata.get("annotations") or {}).items()
        )
    ):
        return False
    body["metadata"]["resourceVersion"] = existing.metadata.resource_version
    try:
        core().replace_namespaced_config_map(name, namespace, body)
        return True
    except client.ApiException as exc:
        if exc.status == 409:
            return False
        raise


def _verified_config_map_revision(namespace, body) -> str:
    """Return the live desired ConfigMap revision, or fail closed on a race."""
    existing = core().read_namespaced_config_map(
        body["metadata"]["name"],
        namespace,
        _request_timeout=_KUBE_REQUEST_TIMEOUT,
    )
    desired_metadata = body.get("metadata") or {}
    labels = existing.metadata.labels or {}
    annotations = existing.metadata.annotations or {}
    if (
        (existing.data or {}) != body["data"]
        or any(
            labels.get(key) != value
            for key, value in (desired_metadata.get("labels") or {}).items()
        )
        or any(
            annotations.get(key) != value
            for key, value in (desired_metadata.get("annotations") or {}).items()
        )
    ):
        raise RuntimeError(
            "egress proxy ConfigMap changed during reconcile; refusing rollout"
        )
    revision = existing.metadata.resource_version
    if not revision:
        raise RuntimeError("egress proxy ConfigMap has no resourceVersion")
    return str(revision)


def _delete_network_policy(namespace, name) -> None:
    try:
        networking().delete_namespaced_network_policy(
            name,
            namespace,
            _request_timeout=_KUBE_REQUEST_TIMEOUT,
        )
    except client.ApiException as exc:
        if exc.status != 404:
            raise


def _prune_legacy_policies(namespace) -> None:
    """Remove the hand-applied policies this set supersedes.

    Policies UNION their allow rules, so leaving the old permissive set in place
    alongside the new strict one would quietly widen it — the upgrade would look
    successful and change nothing.
    """
    from priva_operator import netpol
    for name in netpol.LEGACY_POLICIES:
        try:
            networking().read_namespaced_network_policy(
                name,
                namespace,
                _request_timeout=_KUBE_REQUEST_TIMEOUT,
            )
        except client.ApiException as exc:
            if exc.status == 404:
                continue
            raise
        _delete_network_policy(namespace, name)
        logger.info("pruned superseded NetworkPolicy {}", name)


def _service_body(namespace, account_id, port, owner) -> dict:
    lbl = names.labels(account_id)
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": names.svc_name(account_id), "namespace": namespace,
                     "labels": lbl, "ownerReferences": [owner]},
        "spec": {"selector": lbl, "ports": [{"port": port, "targetPort": port, "name": "http"}]},
    }


def _terminal_service_body(namespace, account_id, port, owner) -> dict:
    lbl = names.terminal_labels(account_id)
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": names.terminal_svc_name(account_id), "namespace": namespace,
                     "labels": lbl, "ownerReferences": [owner]},
        "spec": {"selector": lbl, "ports": [{"port": port, "targetPort": port, "name": "http"}]},
    }


def _terminal_deployment_body(
    namespace,
    account_id,
    username,
    image,
    pull_policy,
    settings,
    owner,
    spec,
    mount_info: MountInfo,
    defaults=None,
    iso=None,
    *,
    internal_drain_token: str | None = None,
) -> dict:
    """Independent terminal runtime: same image/files/uid as Runner, but no Runner
    secrets, config-map environment, process namespace, or cgroup."""
    lbl = names.terminal_labels(account_id)
    tmpl_lbl, proxy_env = _proxy_template_bits(iso, settings, lbl)
    k = settings.kubernetes
    uid = int(k.runner_uid)
    gid = int(k.runner_gid)
    port = int(k.terminal_service_port)
    max_sessions = int(getattr(defaults, "terminal_max_sessions",
                               getattr(k, "terminal_max_sessions", 2)))
    idle_timeout = int(getattr(defaults, "terminal_idle_timeout_seconds",
                               getattr(k, "terminal_idle_timeout_seconds", 1800)))
    max_lifetime = int(getattr(defaults, "terminal_max_lifetime_seconds",
                               getattr(k, "terminal_max_lifetime_seconds", 14400)))
    terminal_percent = resolve_terminal_percent(settings, defaults)
    verification_key_ring = service_identity.verification_keys()
    verification_key = verification_key_ring[0]
    generation = allocation_hash(
        spec, settings, defaults, username, image=image, pull_policy=pull_policy,
        verification_key_ring=verification_key_ring)
    template_generation = terminal_template_hash(
        spec, settings, defaults, username, image=image, pull_policy=pull_policy,
        verification_key_ring=verification_key_ring)
    internal_drain_token = internal_drain_token or secrets.token_urlsafe(32)
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": names.terminal_deploy_name(account_id), "namespace": namespace,
                     "labels": lbl, "ownerReferences": [owner],
                     "annotations": {
                         _TERMINAL_PERCENT_ANNOTATION: str(terminal_percent),
                         _ALLOCATION_HASH_ANNOTATION: generation,
                         _TERMINAL_TEMPLATE_HASH_ANNOTATION: template_generation,
                         _EGRESS_GENERATION_ANNOTATION: egress_generation(iso, settings),
                     }},
        "spec": {
            "replicas": 0,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": lbl},
            "template": {
                "metadata": {"labels": tmpl_lbl, "annotations": {
                    _TERMINAL_PERCENT_ANNOTATION: str(terminal_percent),
                    _ALLOCATION_HASH_ANNOTATION: generation,
                    _TERMINAL_TEMPLATE_HASH_ANNOTATION: template_generation,
                    _EGRESS_GENERATION_ANNOTATION: egress_generation(iso, settings),
                }},
                "spec": {
                    **({"imagePullSecrets": [{"name": k.runner_image_pull_secret}]}
                       if k.runner_image_pull_secret else {}),
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "hostPID": False,
                    "hostIPC": False,
                    "hostNetwork": False,
                    "shareProcessNamespace": False,
                    "terminationGracePeriodSeconds": 10,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": uid,
                        "runAsGroup": gid,
                        "fsGroup": gid,
                        "fsGroupChangePolicy": "OnRootMismatch",
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [{
                        "name": "terminal",
                        "image": image,
                        "imagePullPolicy": pull_policy,
                        # Apply the process limits to terminald itself as well as
                        # every PTY child. The child shell re-asserts the same limits.
                        "command": [
                            "/usr/bin/prlimit", "--nofile=4096:4096", "--nproc=256:256",
                            "--core=0:0", "--", "/usr/local/bin/priva-terminald",
                        ],
                        "resources": resolve_terminal_resources(spec, settings, defaults),
                        "ports": [{"containerPort": port, "name": "http"}],
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
                            "privileged": False,
                            "readOnlyRootFilesystem": True,
                            "capabilities": {"drop": ["ALL"]},
                        },
                        # Deliberately no envFrom: terminal users must never inherit
                        # data-spine/JWT/runtime secrets from the Runner control process.
                        "env": [
                            {"name": "PRIVA_TERMINAL_LISTEN", "value": f"0.0.0.0:{port}"},
                            {"name": "PRIVA_TERMINAL_MAX_SESSIONS", "value": str(max_sessions)},
                            {"name": "PRIVA_TERMINAL_IDLE_TIMEOUT_SECONDS", "value": str(idle_timeout)},
                            {"name": "PRIVA_TERMINAL_MAX_LIFETIME_SECONDS", "value": str(max_lifetime)},
                            {"name": "PRIVA_TERMINAL_OUTPUT_RATE", "value": str(getattr(k, "terminal_output_rate_limit_bytes_per_sec", 256 * 1024))},
                            {"name": "PRIVA_TERMINAL_OUTPUT_BURST", "value": str(getattr(k, "terminal_output_burst_bytes", 1024 * 1024))},
                            {"name": "PRIVA_TERMINAL_OUTPUT_BUFFER", "value": str(getattr(k, "terminal_output_buffer_bytes", 1024 * 1024))},
                            {"name": "PRIVA_TERMINAL_CWD", "value": "/workspace"},
                            {"name": "PRIVA_TERMINAL_SHELL", "value": "/bin/bash"},
                            {"name": "PRIVA_TERMINAL_ACCOUNT_ID", "value": account_id},
                            {
                                "name": "PRIVA_TERMINAL_POD",
                                "valueFrom": {
                                    "fieldRef": {
                                        "apiVersion": "v1",
                                        "fieldPath": "status.podIP",
                                    },
                                },
                            },
                            {
                                "name": "PRIVA_SERVICE_IDENTITY__PUBLIC_KEY",
                                "value": verification_key,
                            },
                            {
                                "name": "PRIVA_SERVICE_IDENTITY__ADDITIONAL_PUBLIC_KEYS",
                                "value": json.dumps(
                                    list(verification_key_ring[1:]),
                                    separators=(",", ":"),
                                ),
                            },
                            {
                                "name": drain_token.ENV,
                                "value": internal_drain_token,
                            },
                            {"name": "HOME", "value": "/workspace/.home"},
                            {"name": "WORKSPACE_DIR", "value": "/workspace"},
                            {"name": "PRIVA_HOME", "value": "/workspace/.priva"},
                            {"name": "CLAUDE_CONFIG_DIR", "value": "/workspace/.claude"},
                            {"name": "PRIVA_HOOK_DIR", "value": "/workspace/.priva/hook-context"},
                            {"name": "USER", "value": "app"},
                            {"name": "LOGNAME", "value": "app"},
                            {"name": "SHELL", "value": "/bin/bash"},
                            {"name": "LANG", "value": "C.UTF-8"},
                            # terminald builds the PTY's environment from a FIXED list
                            # (main.go shellEnv), so these have to be forwarded there
                            # explicitly — a container env var alone never reaches the
                            # user's shell, and the shell would just lose the internet.
                            *proxy_env,
                        ],
                        "volumeMounts": [
                            _data_volume_mount(mount_info),
                            {"name": "tmp", "mountPath": "/tmp"},
                            {"name": MANAGED_POLICY_VOLUME, "mountPath": MANAGED_POLICY_MOUNT,
                             "readOnly": True},
                        ],
                        "readinessProbe": {
                            "tcpSocket": {"port": port},
                            "initialDelaySeconds": 1,
                            "periodSeconds": 3,
                            "failureThreshold": 20,
                        },
                        "livenessProbe": {
                            "tcpSocket": {"port": port},
                            "initialDelaySeconds": 5,
                            "periodSeconds": 10,
                            "failureThreshold": 3,
                        },
                    }],
                    "volumes": [
                        _data_volume(mount_info),
                        {"name": "tmp", "emptyDir": {
                            "medium": "Memory",
                            "sizeLimit": getattr(k, "terminal_tmp_size_limit", "256Mi"),
                        }},
                        {"name": MANAGED_POLICY_VOLUME,
                         "configMap": {"name": MANAGED_POLICY_CM, "optional": True}},
                    ],
                },
            },
        },
    }


# --- reconcile primitives ---------------------------------------------------

def _read_deployment(namespace, account_id):
    try:
        return apps().read_namespaced_deployment(names.deploy_name(account_id), namespace)
    except client.ApiException as exc:
        if exc.status == 404:
            return None
        raise


def _read_terminal_deployment(namespace, account_id):
    try:
        return apps().read_namespaced_deployment(names.terminal_deploy_name(account_id), namespace)
    except client.ApiException as exc:
        if exc.status == 404:
            return None
        raise


def _deployment_env_value(deployment, container_name: str, env_name: str) -> str | None:
    """Read one literal env value from a Kubernetes Deployment model."""
    if deployment is None:
        return None
    template = getattr(getattr(deployment, "spec", None), "template", None)
    pod_spec = getattr(template, "spec", None)
    for container in getattr(pod_spec, "containers", None) or []:
        if getattr(container, "name", None) != container_name:
            continue
        for item in getattr(container, "env", None) or []:
            if getattr(item, "name", None) == env_name:
                value = getattr(item, "value", None)
                return str(value) if value else None
    return None


def applied_runner_drain_token(namespace: str, account_id: str) -> str | None:
    return _deployment_env_value(
        _read_deployment(namespace, account_id),
        "agent-runner",
        drain_token.ENV,
    )


def applied_terminal_drain_token(namespace: str, account_id: str) -> str | None:
    return _deployment_env_value(
        _read_terminal_deployment(namespace, account_id),
        "terminal",
        drain_token.ENV,
    )


def ensure_runtime_objects(namespace, account_id, username, image, pull_policy, settings, owner, spec,
                           defaults=None, iso=None) -> None:
    # Provision the per-account subdir + quota on the shared export FIRST (idempotent:
    # mkdir + chown + set the backend quota), then render the Deployment to mount it.
    # No per-account PVC — the runner subPaths into the one shared RWX export claim.
    mount_info = get_backend(settings).provision(account_id, resolve_storage_gb(spec, settings, defaults))
    _ignore_conflict(core().create_namespaced_service,
                     namespace, _service_body(namespace, account_id, settings.kubernetes.runner_service_port, owner))
    existing = _read_deployment(namespace, account_id)
    # A random capability is stable for the lifetime of the applied Deployment.
    # Reusing it avoids replacing every dormant Deployment on every timer tick.
    existing_drain_token = _deployment_env_value(
        existing, "agent-runner", drain_token.ENV
    )
    body = _deployment_body(namespace, account_id, username, image, pull_policy,
                            settings, owner, spec, mount_info, defaults, iso,
                            internal_drain_token=existing_drain_token)
    if existing is None:
        # A live Terminal may still carry an older resource split. Creating a new
        # Runner template from the new split beside it could exceed the tenant's fixed
        # commitment. Wait for the Terminal zero boundary unless generations match.
        terminal = _read_terminal_deployment(namespace, account_id)
        if terminal is not None and (terminal.spec.replicas or 0) > 0:
            desired_generation = body["metadata"]["annotations"][_ALLOCATION_HASH_ANNOTATION]
            terminal_generation = _deployment_allocation_generation(terminal)
            if desired_generation != terminal_generation:
                logger.warning(
                    "runner create deferred for %s: live Terminal generation %s != %s",
                    account_id, terminal_generation, desired_generation)
                return
        # A 409 winner appeared after the authoritative read and has not been
        # verified against this identity/allocation/egress template. Propagate
        # the conflict so a wake cannot immediately scale that unknown object.
        apps().create_namespaced_deployment(namespace, body)
        return
    # Converge an existing Deployment to the current template. Create-only (the old
    # behavior) strands tenants born under an older operator: template additions —
    # e.g. the managed-policy mount — would never reach them. Only while scaled to
    # 0: strategy=Recreate restarts the pod on any template write, and the policy is
    # apply-on-next-restart (reconcile must never kill a running session).
    if (existing.spec.replicas or 0) > 0:
        return
    # A live Terminal must keep the Runner template from the same allocation
    # generation. Re-templating just the dormant Runner after a percentage/total
    # change could make old-Terminal + new-Runner exceed the tenant commitment.
    terminal = _read_terminal_deployment(namespace, account_id)
    if terminal is not None and (terminal.spec.replicas or 0) > 0:
        runner_generation = _deployment_allocation_generation(existing)
        terminal_generation = _deployment_allocation_generation(terminal)
        if runner_generation != terminal_generation:
            raise RuntimeError(
                f"runner/terminal allocation generation mismatch for {account_id}: "
                f"{runner_generation} != {terminal_generation}")
        return
    body["spec"]["replicas"] = existing.spec.replicas or 0
    body["metadata"]["resourceVersion"] = existing.metadata.resource_version
    # A 409 is not success: a concurrent scale/template writer may have left the
    # old image, proxy environment, or allocation in place. Propagate the
    # conflict so the caller cannot continue to scale that unverified template.
    apps().replace_namespaced_deployment(
        names.deploy_name(account_id), namespace, body)


def ensure_terminal_objects(namespace, account_id, username, image, pull_policy, settings, owner,
                            spec, defaults=None, iso=None) -> None:
    """Create/converge the independent Terminal Deployment+Service while dormant.

    Disabled policy (0%) creates nothing. Existing objects are retained at replicas=0
    so re-enabling is migration-free and the next wake is fast.
    """
    if resolve_terminal_percent(settings, defaults) <= 0:
        return
    mount_info = get_backend(settings).provision(
        account_id, resolve_storage_gb(spec, settings, defaults))
    port = settings.kubernetes.terminal_service_port
    _ignore_conflict(core().create_namespaced_service, namespace,
                     _terminal_service_body(namespace, account_id, port, owner))
    existing = _read_terminal_deployment(namespace, account_id)
    existing_drain_token = _deployment_env_value(
        existing, "terminal", drain_token.ENV
    )
    body = _terminal_deployment_body(namespace, account_id, username, image, pull_policy,
                                     settings, owner, spec, mount_info, defaults, iso,
                                     internal_drain_token=existing_drain_token)
    if existing is None:
        # Same rule as Runner: do not scale a concurrently-created, unverified
        # Terminal template after treating AlreadyExists as success.
        apps().create_namespaced_deployment(namespace, body)
        return
    if (existing.spec.replicas or 0) > 0:
        return
    body["spec"]["replicas"] = existing.spec.replicas or 0
    body["metadata"]["resourceVersion"] = existing.metadata.resource_version
    # Same safety rule as Runner: never scale a template whose replace lost a
    # resourceVersion race.
    apps().replace_namespaced_deployment(
        names.terminal_deploy_name(account_id), namespace, body)


def patch_deployment_resources(namespace, account_id, resources: dict) -> None:
    """Strategic-merge patch the container resources by name. With strategy=Recreate
    this restarts a running pod with the new requests/limits (dormant at replicas 0)."""
    body = {"spec": {"template": {"spec": {"containers": [
        {"name": "agent-runner", "resources": resources}]}}}}
    apps().patch_namespaced_deployment(names.deploy_name(account_id), namespace, body)


def patch_terminal_resources(namespace, account_id, resources: dict) -> None:
    body = {"spec": {"template": {"spec": {"containers": [
        {"name": "terminal", "resources": resources}]}}}}
    apps().patch_namespaced_deployment(names.terminal_deploy_name(account_id), namespace, body)


def get_replicas(namespace, account_id) -> int:
    try:
        d = apps().read_namespaced_deployment(names.deploy_name(account_id), namespace)
        return d.spec.replicas or 0
    except client.ApiException as exc:
        if exc.status == 404:
            return -1
        raise


def get_terminal_replicas(namespace, account_id) -> int:
    try:
        d = apps().read_namespaced_deployment(names.terminal_deploy_name(account_id), namespace)
        return d.spec.replicas or 0
    except client.ApiException as exc:
        if exc.status == 404:
            return -1
        raise


def _deployment_terminal_percent(deployment) -> int:
    annotations = getattr(deployment.metadata, "annotations", None) or {}
    try:
        return int(annotations.get(_TERMINAL_PERCENT_ANNOTATION, "0"))
    except (TypeError, ValueError):
        return 0


def _deployment_allocation_generation(deployment) -> str:
    annotations = getattr(deployment.metadata, "annotations", None) or {}
    value = annotations.get(_ALLOCATION_HASH_ANNOTATION)
    if value:
        return str(value)
    return f"legacy-percent:{_deployment_terminal_percent(deployment)}"


def applied_terminal_percent(namespace, account_id) -> int | None:
    """Percent baked into the current Runner Deployment template."""
    deployment = _read_deployment(namespace, account_id)
    return None if deployment is None else _deployment_terminal_percent(deployment)


def applied_allocation_hash(namespace, account_id) -> str | None:
    """Allocation generation baked into the current Runner Deployment template."""
    deployment = _read_deployment(namespace, account_id)
    return None if deployment is None else _deployment_allocation_generation(deployment)


def applied_terminal_template_hash(namespace, account_id) -> str | None:
    """Terminal-only template generation baked into its Deployment."""
    deployment = _read_terminal_deployment(namespace, account_id)
    if deployment is None:
        return None
    annotations = getattr(deployment.metadata, "annotations", None) or {}
    value = annotations.get(_TERMINAL_TEMPLATE_HASH_ANNOTATION)
    return str(value) if value else None


def scale(namespace, account_id, replicas: int) -> None:
    apps().patch_namespaced_deployment_scale(
        names.deploy_name(account_id),
        namespace,
        {"spec": {"replicas": replicas}},
        _request_timeout=_KUBE_REQUEST_TIMEOUT,
    )


def scale_terminal(namespace, account_id, replicas: int) -> None:
    apps().patch_namespaced_deployment_scale(
        names.terminal_deploy_name(account_id),
        namespace,
        {"spec": {"replicas": replicas}},
        _request_timeout=_KUBE_REQUEST_TIMEOUT,
    )


def delete_export_claim(namespace, account_id) -> bool:
    """Delete the per-account export claim; ``False`` when it was already absent.

    The claim is created with no ownerReference (``storage_backend.CephFsBackend``), so
    owner-ref garbage collection never reclaims it when the AgentTenant goes away.
    """
    try:
        core().delete_namespaced_persistent_volume_claim(
            names.export_claim(account_id), namespace)
    except client.ApiException as exc:
        if exc.status != 404:  # already gone == deleted
            raise
        return False
    return True


def workload_pods_gone(namespace: str, account_id: str, app: str) -> bool:
    """Whether every Pod for one account workload has disappeared.

    Unlike ``current_ready_pod_ip`` this intentionally counts NotReady and
    Terminating Pods. Desired replicas reaching zero is only the start of
    teardown; storage cannot be reclaimed, and a persisted Draining route gate
    cannot be cleared, until the old process is actually gone.
    """
    selector = f"app={app},priva.io/account-id={account_id}"
    return not core().list_namespaced_pod(
        namespace,
        label_selector=selector,
        _request_timeout=_KUBE_REQUEST_TIMEOUT,
    ).items


def account_workload_pods_gone(namespace: str, account_id: str) -> bool:
    """True only after both Runner and Terminal Pod sets are empty."""
    return all(
        workload_pods_gone(namespace, account_id, app)
        for app in _ACCOUNT_WORKLOAD_APPS
    )


def wait_account_workload_pods_gone(
    namespace: str,
    account_id: str,
    *,
    timeout: float = 60.0,
    poll_interval: float = 0.5,
) -> bool:
    """Bounded wait for account teardown, including Terminating Pods."""
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if account_workload_pods_gone(namespace, account_id):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(max(0.01, poll_interval), remaining))


def _current_ready_pod_ip(namespace, account_id, app: str) -> str | None:
    """IP of the *one* pod for this account that is Ready **and** not terminating, else None.

    Pure pod query — the single source of truth for "is there a live pod and where".
    Deliberately does NOT consult ``status.phase``: phase is *derived from* this, so
    coupling them would be circular. The ``deletion_timestamp is None`` filter drops a
    pod that is mid-termination (its IP is about to disappear) so callers never hand out
    or probe a doomed endpoint.
    """
    selector = f"app={app},priva.io/account-id={account_id}"
    pods = core().list_namespaced_pod(
        namespace,
        label_selector=selector,
        _request_timeout=_KUBE_REQUEST_TIMEOUT,
    ).items
    for p in pods:
        if p.metadata.deletion_timestamp is not None:
            continue  # terminating — skip its soon-to-vanish IP
        ready = any(c.type == "Ready" and c.status == "True" for c in (p.status.conditions or []))
        if ready and p.status.pod_ip:
            return p.status.pod_ip
    return None


def current_ready_pod_ip(namespace, account_id) -> str | None:
    return _current_ready_pod_ip(namespace, account_id, "agent-runner")


def current_ready_terminal_pod_ip(namespace, account_id) -> str | None:
    return _current_ready_pod_ip(namespace, account_id, "terminal")


def wait_pod_ready(namespace, account_id, timeout: float = 60.0) -> str | None:
    """Poll until a Ready, non-terminating pod for this account appears; return its podIP."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ip = current_ready_pod_ip(namespace, account_id)
        if ip:
            return ip
        time.sleep(1.5)
    return None


def wait_terminal_pod_ready(namespace, account_id, timeout: float = 60.0) -> str | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ip = current_ready_terminal_pod_ip(namespace, account_id)
        if ip:
            return ip
        time.sleep(1.5)
    return None


def set_cr_status(namespace, account_id, **fields) -> None:
    """Patch the AgentTenant *status* subresource directly via the API.

    kopf's ``patch.status[...]`` is buffered and applied as a single PATCH only when
    the handler *returns*. Cases that must flip status **before** doing something else
    (e.g. mark not-routable before tearing a pod down) can't use it — they go through
    here. Idempotent: re-asserting the same fields is a no-op PATCH.
    """
    custom().patch_namespaced_custom_object_status(
        GROUP, VERSION, namespace, PLURAL, account_id, {"status": fields})


def current_cr_phase(namespace, account_id) -> str | None:
    """Read the live phase when a stale handler snapshot would be unsafe."""
    try:
        obj = custom().get_namespaced_custom_object(
            GROUP, VERSION, namespace, PLURAL, account_id
        )
    except client.ApiException as exc:
        if exc.status == 404:
            return None
        raise
    return (obj.get("status") or {}).get("phase")


def current_cr_terminal_phase(namespace, account_id) -> str | None:
    """Read the nested live Terminal phase for wake/drain race checks."""
    try:
        obj = custom().get_namespaced_custom_object(
            GROUP, VERSION, namespace, PLURAL, account_id
        )
    except client.ApiException as exc:
        if exc.status == 404:
            return None
        raise
    terminal = ((obj.get("status") or {}).get("terminal") or {})
    return terminal.get("phase")


def agenttenant_teardown_started(
    namespace: str,
    account_id: str,
    expected_uid: str | None,
) -> bool:
    """Live guard for handlers which are about to create or scale workloads.

    A handler's ``spec``/``meta`` snapshot can predate a lifecycle transition,
    and Kopf cannot cancel a synchronous handler already running in its worker
    pool. Only the same, live, explicitly-active CR authorizes workload
    creation/scale; missing, deleting, recreated, offboarding, and purge objects
    all fail closed.
    """
    try:
        obj = custom().get_namespaced_custom_object(
            GROUP, VERSION, namespace, PLURAL, account_id
        )
    except client.ApiException as exc:
        if exc.status == 404:
            return True
        raise
    metadata = obj.get("metadata") or {}
    spec = obj.get("spec") or {}
    live_uid = metadata.get("uid")
    return bool(
        metadata.get("deletionTimestamp")
        or spec.get("desiredState", "active") != "active"
        or (
            expected_uid is not None
            and str(live_uid) != str(expected_uid)
        )
    )
