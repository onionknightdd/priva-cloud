"""Kubernetes client + manifest builders for the per-account agent-runner objects.

Bodies are plain dicts (the client serializes them). All create_* are idempotent
(409 AlreadyExists is swallowed) so reconcile can run repeatedly.
"""

from __future__ import annotations

import time

from kubernetes import client, config

from priva_common.logging import get_app_logger
from priva_operator import GROUP, PLURAL, VERSION, names

logger = get_app_logger(__name__)

_loaded = False


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


def storage_quantity(gb: int) -> str:
    return f"{int(gb)}Gi"


def _parse_gi(q: str | None) -> int | None:
    """Parse a storage quantity we wrote (always Gi) back to an int. None if absent."""
    if not q:
        return None
    s = str(q).strip()
    if s.endswith("Gi"):
        return int(float(s[:-2]))
    if s.endswith("G"):
        return int(float(s[:-1]))
    try:
        return int(float(s))
    except ValueError:
        return None


def resolve_resources(spec: dict, settings) -> dict:
    """CR spec.resources -> container `resources` (requests==limits = Guaranteed QoS),
    falling back to cluster settings when a field is omitted."""
    r = (spec.get("resources") or {})
    cores = r.get("cpu")
    cores = float(cores) if cores is not None else float(settings.kubernetes.runner_cpu_cores)
    mb = r.get("memoryMb")
    mb = int(mb) if mb is not None else int(settings.kubernetes.runner_memory_mb)
    q = {"cpu": cpu_quantity(cores), "memory": mem_quantity(mb)}
    return {"requests": dict(q), "limits": dict(q)}


def resolve_storage_gb(spec: dict, settings) -> int:
    sg = spec.get("storageGb")
    return int(sg) if sg is not None else int(settings.kubernetes.runner_storage_gb)


# --- manifest builders ------------------------------------------------------

def _deployment_body(namespace, account_id, username, image, pull_policy, settings, owner, spec) -> dict:
    lbl = names.labels(account_id)
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": names.deploy_name(account_id), "namespace": namespace,
                     "labels": lbl, "ownerReferences": [owner]},
        "spec": {
            "replicas": 0,  # scale-to-zero from birth; the operator is the sole scaler
            "strategy": {"type": "Recreate"},  # never two pods on one RWO PVC
            "selector": {"matchLabels": lbl},
            "template": {
                "metadata": {"labels": lbl},
                "spec": {
                    "containers": [{
                        "name": "agent-runner",
                        "image": image,
                        "imagePullPolicy": pull_policy,
                        "resources": resolve_resources(spec, settings),
                        "ports": [{"containerPort": settings.kubernetes.runner_service_port}],
                        "env": [
                            {"name": "ACCOUNT_ID", "value": account_id},
                            {"name": "USERNAME", "value": username},
                            {"name": "AGENT_RUNNER_HOST", "value": "0.0.0.0"},
                            {"name": "AGENT_RUNNER_PORT", "value": str(settings.kubernetes.runner_service_port)},
                            {"name": "PRIVA_SERVER__WORK_DIR", "value": "/workspace"},
                            {"name": "WORKSPACE_DIR", "value": "/workspace"},
                            {"name": "PRIVA_HOME", "value": "/workspace/.priva"},
                            {"name": "CLAUDE_CONFIG_DIR", "value": "/workspace/.claude"},
                            # The runtime drives the claude CLI with
                            # permission_mode=bypassPermissions (= --dangerously-skip-permissions).
                            # The CLI refuses that flag when running as root ("cannot be used
                            # with root/sudo privileges") and exits 1 → the agent run fails.
                            # This per-account pod IS an isolated sandbox, so assert it: the
                            # CLI's IS_SANDBOX escape allows the flag as root. (Hardening to a
                            # non-root UID is deferred — would need PVC/PRIVA_HOME ownership.)
                            {"name": "IS_SANDBOX", "value": "1"},
                        ],
                        "envFrom": [
                            {"configMapRef": {"name": "priva-config"}},
                            {"secretRef": {"name": "priva-shared-secret"}},
                            {"secretRef": {"name": names.secret_name(account_id), "optional": True}},
                        ],
                        "volumeMounts": [{"name": "data", "mountPath": "/workspace"}],
                        "readinessProbe": {
                            "httpGet": {"path": "/health", "port": settings.kubernetes.runner_service_port},
                            "initialDelaySeconds": 2, "periodSeconds": 3, "failureThreshold": 30,
                        },
                    }],
                    "volumes": [{"name": "data",
                                 "persistentVolumeClaim": {"claimName": names.pvc_name(account_id)}}],
                },
            },
        },
    }


def _service_body(namespace, account_id, port, owner) -> dict:
    lbl = names.labels(account_id)
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": names.svc_name(account_id), "namespace": namespace,
                     "labels": lbl, "ownerReferences": [owner]},
        "spec": {"selector": lbl, "ports": [{"port": port, "targetPort": port, "name": "http"}]},
    }


def _pvc_body(namespace, account_id, owner, settings, spec) -> dict:
    pvc_spec = {
        "accessModes": ["ReadWriteOnce"],
        "resources": {"requests": {"storage": storage_quantity(resolve_storage_gb(spec, settings))}},
    }
    sc = settings.kubernetes.runner_storage_class
    if sc:  # "" => omit storageClassName => cluster default SC
        pvc_spec["storageClassName"] = sc
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {"name": names.pvc_name(account_id), "namespace": namespace,
                     "labels": names.labels(account_id), "ownerReferences": [owner]},
        "spec": pvc_spec,
    }


# --- reconcile primitives ---------------------------------------------------

def ensure_runtime_objects(namespace, account_id, username, image, pull_policy, settings, owner, spec) -> None:
    _ignore_conflict(core().create_namespaced_persistent_volume_claim,
                     namespace, _pvc_body(namespace, account_id, owner, settings, spec))
    _ignore_conflict(core().create_namespaced_service,
                     namespace, _service_body(namespace, account_id, settings.kubernetes.runner_service_port, owner))
    _ignore_conflict(apps().create_namespaced_deployment,
                     namespace, _deployment_body(namespace, account_id, username, image, pull_policy, settings, owner, spec))


def patch_deployment_resources(namespace, account_id, resources: dict) -> None:
    """Strategic-merge patch the container resources by name. With strategy=Recreate
    this restarts a running pod with the new requests/limits (dormant at replicas 0)."""
    body = {"spec": {"template": {"spec": {"containers": [
        {"name": "agent-runner", "resources": resources}]}}}}
    apps().patch_namespaced_deployment(names.deploy_name(account_id), namespace, body)


def get_pvc_storage_gi(namespace, account_id) -> int | None:
    """Current requested PVC storage in Gi (grow-only comparison). None if absent."""
    try:
        p = core().read_namespaced_persistent_volume_claim(names.pvc_name(account_id), namespace)
    except client.ApiException as exc:
        if exc.status == 404:
            return None
        raise
    req = (p.spec.resources.requests or {}) if p.spec and p.spec.resources else {}
    return _parse_gi(req.get("storage"))


def patch_pvc_storage(namespace, account_id, gb: int) -> None:
    body = {"spec": {"resources": {"requests": {"storage": storage_quantity(gb)}}}}
    core().patch_namespaced_persistent_volume_claim(names.pvc_name(account_id), namespace, body)


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
