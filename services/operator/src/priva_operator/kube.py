"""Kubernetes client + manifest builders for the per-account agent-runner objects.

Bodies are plain dicts (the client serializes them). All create_* are idempotent
(409 AlreadyExists is swallowed) so reconcile can run repeatedly.
"""

from __future__ import annotations

import hashlib
import json
import time

from kubernetes import client, config

from priva_common.logging import get_app_logger
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


def resolve_image(spec: dict, settings, defaults=None) -> str:
    img = spec.get("image")
    if img:
        return img
    return defaults.runner_image if defaults else settings.kubernetes.runner_image


def allocation_hash(
    spec: dict,
    settings,
    defaults,
    username: str = "",
    *,
    image: str | None = None,
    pull_policy: str | None = None,
) -> str:
    """Fingerprint every value that can change the shared runtime allocation.

    Percentage alone is not an allocation generation: total CPU/memory, image and
    identity can change while it remains constant. Both Deployments carry this hash so
    the operator never starts a mixed generation that can overcommit a tenant. Terminal-
    only session/output policy has a separate template hash and does not restart Runner.
    """
    k = settings.kubernetes
    payload = {
        "version": 1,
        "username": username,
        "image": image or resolve_image(spec, settings, defaults),
        "pullPolicy": pull_policy or getattr(k, "runner_image_pull_policy", "IfNotPresent"),
        "runnerResources": resolve_resources(spec, settings, defaults),
        "terminalResources": resolve_terminal_resources(spec, settings, defaults),
        "terminalPercent": resolve_terminal_percent(settings, defaults),
        "security": {
            "uid": int(getattr(k, "runner_uid", 10001)),
            "gid": int(getattr(k, "runner_gid", 10001)),
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "v1:" + hashlib.sha256(canonical.encode()).hexdigest()


def terminal_template_hash(
    spec: dict, settings, defaults, username: str = "", *, image: str | None = None,
    pull_policy: str | None = None,
) -> str:
    """Fingerprint Terminal-only policy without forcing a Runner allocation restart."""
    k = settings.kubernetes
    payload = {
        "allocation": allocation_hash(
            spec, settings, defaults, username, image=image, pull_policy=pull_policy),
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
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "v1:" + hashlib.sha256(canonical.encode()).hexdigest()


# --- manifest builders ------------------------------------------------------

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


def _deployment_body(namespace, account_id, username, image, pull_policy, settings, owner, spec,
                     mount_info: MountInfo, defaults=None) -> dict:
    lbl = names.labels(account_id)
    terminal_percent = resolve_terminal_percent(settings, defaults)
    generation = allocation_hash(
        spec, settings, defaults, username, image=image, pull_policy=pull_policy)
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
                     }},
        "spec": {
            "replicas": 0,  # scale-to-zero from birth; the operator is the sole scaler
            "strategy": {"type": "Recreate"},  # one pod per subPath; clean cutover on restart
            "selector": {"matchLabels": lbl},
            "template": {
                "metadata": {"labels": lbl, "annotations": {
                    _TERMINAL_PERCENT_ANNOTATION: str(terminal_percent),
                    _ALLOCATION_HASH_ANNOTATION: generation,
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
                        ],
                        "envFrom": [
                            {"configMapRef": {"name": "priva-config"}},
                            {"secretRef": {"name": "priva-shared-secret"}},
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
                        {"name": "tmp", "emptyDir": {}},
                        # optional:True → a pod still starts if the CM isn't created
                        # yet (fail-open, consistent with the hooks-snapshot policy).
                        {"name": MANAGED_POLICY_VOLUME,
                         "configMap": {"name": MANAGED_POLICY_CM, "optional": True}},
                    ],
                },
            },
        },
    }


def ensure_managed_policy_configmap(namespace) -> bool:
    """Render the global enforced-hook policy into the shared managed-policy CM.

    Reads enforced+enabled command policies from data-spine and renders the
    Claude Code managed-settings.json + hook scripts + fire-log wrapper (see
    priva_common.managed_policy_render). Idempotent: a digest annotation skips
    the write when the enforced set is unchanged, so calling it from every
    per-account handler costs one read and no write in steady state. Fail-soft:
    a data-spine blip logs and returns (pods fail-open on the optional mount).
    Returns True when a create/replace was performed.
    """
    try:
        from priva_common.dataplane import get_client
        from priva_common import managed_policy_render as render

        rows = get_client().hook_policies.list(enabled_only=True)
        enforced = [p for p in rows if p.enforced and p.hook_type == "command"]
        data = render.render_config_map_data(enforced)
    except Exception:
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


def _terminal_deployment_body(namespace, account_id, username, image, pull_policy, settings,
                              owner, spec, mount_info: MountInfo, defaults=None) -> dict:
    """Independent terminal runtime: same image/files/uid as Runner, but no Runner
    secrets, config-map environment, process namespace, or cgroup."""
    lbl = names.terminal_labels(account_id)
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
    generation = allocation_hash(
        spec, settings, defaults, username, image=image, pull_policy=pull_policy)
    template_generation = terminal_template_hash(
        spec, settings, defaults, username, image=image, pull_policy=pull_policy)
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": names.terminal_deploy_name(account_id), "namespace": namespace,
                     "labels": lbl, "ownerReferences": [owner],
                     "annotations": {
                         _TERMINAL_PERCENT_ANNOTATION: str(terminal_percent),
                         _ALLOCATION_HASH_ANNOTATION: generation,
                         _TERMINAL_TEMPLATE_HASH_ANNOTATION: template_generation,
                     }},
        "spec": {
            "replicas": 0,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": lbl},
            "template": {
                "metadata": {"labels": lbl, "annotations": {
                    _TERMINAL_PERCENT_ANNOTATION: str(terminal_percent),
                    _ALLOCATION_HASH_ANNOTATION: generation,
                    _TERMINAL_TEMPLATE_HASH_ANNOTATION: template_generation,
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
                            {"name": "HOME", "value": "/workspace/.home"},
                            {"name": "WORKSPACE_DIR", "value": "/workspace"},
                            {"name": "PRIVA_HOME", "value": "/workspace/.priva"},
                            {"name": "CLAUDE_CONFIG_DIR", "value": "/workspace/.claude"},
                            {"name": "PRIVA_HOOK_DIR", "value": "/workspace/.priva/hook-context"},
                            {"name": "USER", "value": "app"},
                            {"name": "LOGNAME", "value": "app"},
                            {"name": "SHELL", "value": "/bin/bash"},
                            {"name": "LANG", "value": "C.UTF-8"},
                        ],
                        "volumeMounts": [
                            _data_volume_mount(mount_info),
                            {"name": "tmp", "mountPath": "/tmp"},
                            {"name": MANAGED_POLICY_VOLUME, "mountPath": MANAGED_POLICY_MOUNT,
                             "readOnly": True},
                        ],
                        "readinessProbe": {
                            "httpGet": {"path": "/health", "port": port},
                            "initialDelaySeconds": 1,
                            "periodSeconds": 3,
                            "failureThreshold": 20,
                        },
                        "livenessProbe": {
                            "httpGet": {"path": "/health", "port": port},
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


def ensure_runtime_objects(namespace, account_id, username, image, pull_policy, settings, owner, spec,
                           defaults=None) -> None:
    # Provision the per-account subdir + quota on the shared export FIRST (idempotent:
    # mkdir + chown + set the backend quota), then render the Deployment to mount it.
    # No per-account PVC — the runner subPaths into the one shared RWX export claim.
    mount_info = get_backend(settings).provision(account_id, resolve_storage_gb(spec, settings, defaults))
    _ignore_conflict(core().create_namespaced_service,
                     namespace, _service_body(namespace, account_id, settings.kubernetes.runner_service_port, owner))
    body = _deployment_body(namespace, account_id, username, image, pull_policy,
                            settings, owner, spec, mount_info, defaults)
    existing = _read_deployment(namespace, account_id)
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
        _ignore_conflict(apps().create_namespaced_deployment, namespace, body)
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
    # 409 = a concurrent writer (wake/scale) won the race; the next ensure converges.
    _ignore_conflict(apps().replace_namespaced_deployment,
                     names.deploy_name(account_id), namespace, body)


def ensure_terminal_objects(namespace, account_id, username, image, pull_policy, settings, owner,
                            spec, defaults=None) -> None:
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
    body = _terminal_deployment_body(namespace, account_id, username, image, pull_policy,
                                     settings, owner, spec, mount_info, defaults)
    existing = _read_terminal_deployment(namespace, account_id)
    if existing is None:
        _ignore_conflict(apps().create_namespaced_deployment, namespace, body)
        return
    if (existing.spec.replicas or 0) > 0:
        return
    body["spec"]["replicas"] = existing.spec.replicas or 0
    body["metadata"]["resourceVersion"] = existing.metadata.resource_version
    _ignore_conflict(apps().replace_namespaced_deployment,
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


def scale(namespace, account_id, replicas: int) -> None:
    apps().patch_namespaced_deployment_scale(
        names.deploy_name(account_id), namespace, {"spec": {"replicas": replicas}})


def scale_terminal(namespace, account_id, replicas: int) -> None:
    apps().patch_namespaced_deployment_scale(
        names.terminal_deploy_name(account_id), namespace, {"spec": {"replicas": replicas}})


def _current_ready_pod_ip(namespace, account_id, app: str) -> str | None:
    """IP of the *one* pod for this account that is Ready **and** not terminating, else None.

    Pure pod query — the single source of truth for "is there a live pod and where".
    Deliberately does NOT consult ``status.phase``: phase is *derived from* this, so
    coupling them would be circular. The ``deletion_timestamp is None`` filter drops a
    pod that is mid-termination (its IP is about to disappear) so callers never hand out
    or probe a doomed endpoint.
    """
    selector = f"app={app},priva.io/account-id={account_id}"
    pods = core().list_namespaced_pod(namespace, label_selector=selector).items
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
