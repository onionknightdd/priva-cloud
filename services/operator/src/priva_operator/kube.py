"""Kubernetes client + manifest builders for the per-account agent-runner objects.

Bodies are plain dicts (the client serializes them). All create_* are idempotent
(409 AlreadyExists is swallowed) so reconcile can run repeatedly.
"""

from __future__ import annotations

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


# --- the inherit cascade: CR spec field (per-account override) > global runner_defaults
# (the admin "Agent Runner Sandbox" panel) > static env settings (the ultimate seed/
# fail-soft when data-spine is unreachable). `defaults` is a RunnerDefaultsRecord or None.

def resolve_resources(spec: dict, settings, defaults=None) -> dict:
    """CR spec.resources -> container `resources` (requests==limits = Guaranteed QoS),
    inheriting the global default then the env seed when a field is omitted."""
    r = (spec.get("resources") or {})
    cores = r.get("cpu")
    if cores is None:
        cores = defaults.cpu_cores if defaults else settings.kubernetes.runner_cpu_cores
    mb = r.get("memoryMb")
    if mb is None:
        mb = defaults.memory_mb if defaults else settings.kubernetes.runner_memory_mb
    q = {"cpu": cpu_quantity(float(cores)), "memory": mem_quantity(int(mb))}
    return {"requests": dict(q), "limits": dict(q)}


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
    uid = int(settings.kubernetes.runner_uid)
    gid = int(settings.kubernetes.runner_gid)
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": names.deploy_name(account_id), "namespace": namespace,
                     "labels": lbl, "ownerReferences": [owner]},
        "spec": {
            "replicas": 0,  # scale-to-zero from birth; the operator is the sole scaler
            "strategy": {"type": "Recreate"},  # one pod per subPath; clean cutover on restart
            "selector": {"matchLabels": lbl},
            "template": {
                "metadata": {"labels": lbl},
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
                    "containers": [{
                        "name": "agent-runner",
                        "image": image,
                        "imagePullPolicy": pull_policy,
                        "resources": resolve_resources(spec, settings, defaults),
                        "ports": [{"containerPort": settings.kubernetes.runner_service_port}],
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
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
                            # Per-account writable dir for managed-hook context (e.g. the
                            # risky-tools seed's risky_tools.json). The global managed
                            # command passes this through the fire-log wrapper so a global
                            # script reads per-account context from a fixed absolute path.
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
        logger.info("created %s (%d enforced hooks)", MANAGED_POLICY_CM, len(enforced))
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
        logger.info("updated %s (%d enforced hooks)", MANAGED_POLICY_CM, len(enforced))
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


# --- reconcile primitives ---------------------------------------------------

def ensure_runtime_objects(namespace, account_id, username, image, pull_policy, settings, owner, spec,
                           defaults=None) -> None:
    # Provision the per-account subdir + quota on the shared export FIRST (idempotent:
    # mkdir + chown + set the backend quota), then render the Deployment to mount it.
    # No per-account PVC — the runner subPaths into the one shared RWX export claim.
    mount_info = get_backend(settings).provision(account_id, resolve_storage_gb(spec, settings, defaults))
    _ignore_conflict(core().create_namespaced_service,
                     namespace, _service_body(namespace, account_id, settings.kubernetes.runner_service_port, owner))
    _ignore_conflict(apps().create_namespaced_deployment,
                     namespace, _deployment_body(namespace, account_id, username, image, pull_policy,
                                                 settings, owner, spec, mount_info, defaults))


def patch_deployment_resources(namespace, account_id, resources: dict) -> None:
    """Strategic-merge patch the container resources by name. With strategy=Recreate
    this restarts a running pod with the new requests/limits (dormant at replicas 0)."""
    body = {"spec": {"template": {"spec": {"containers": [
        {"name": "agent-runner", "resources": resources}]}}}}
    apps().patch_namespaced_deployment(names.deploy_name(account_id), namespace, body)


def patch_deployment_runtime(namespace, account_id, resources: dict, image: str) -> None:
    """Patch the container resources + image together (by container name). Called from
    the wake/ensure scale-up path while the Deployment is at replicas 0, so a stale
    template is refreshed to the current effective config WITHOUT restarting a running
    pod (the "apply on next restart" policy)."""
    body = {"spec": {"template": {"spec": {"containers": [
        {"name": "agent-runner", "resources": resources, "image": image}]}}}}
    apps().patch_namespaced_deployment(names.deploy_name(account_id), namespace, body)


def get_replicas(namespace, account_id) -> int:
    try:
        d = apps().read_namespaced_deployment(names.deploy_name(account_id), namespace)
        return d.spec.replicas or 0
    except client.ApiException as exc:
        if exc.status == 404:
            return -1
        raise


def scale(namespace, account_id, replicas: int) -> None:
    apps().patch_namespaced_deployment_scale(
        names.deploy_name(account_id), namespace, {"spec": {"replicas": replicas}})


def current_ready_pod_ip(namespace, account_id) -> str | None:
    """IP of the *one* pod for this account that is Ready **and** not terminating, else None.

    Pure pod query — the single source of truth for "is there a live pod and where".
    Deliberately does NOT consult ``status.phase``: phase is *derived from* this, so
    coupling them would be circular. The ``deletion_timestamp is None`` filter drops a
    pod that is mid-termination (its IP is about to disappear) so callers never hand out
    or probe a doomed endpoint.
    """
    selector = f"priva.io/account-id={account_id}"
    pods = core().list_namespaced_pod(namespace, label_selector=selector).items
    for p in pods:
        if p.metadata.deletion_timestamp is not None:
            continue  # terminating — skip its soon-to-vanish IP
        ready = any(c.type == "Ready" and c.status == "True" for c in (p.status.conditions or []))
        if ready and p.status.pod_ip:
            return p.status.pod_ip
    return None


def wait_pod_ready(namespace, account_id, timeout: float = 60.0) -> str | None:
    """Poll until a Ready, non-terminating pod for this account appears; return its podIP."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ip = current_ready_pod_ip(namespace, account_id)
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
