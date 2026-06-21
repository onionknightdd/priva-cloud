"""Kubernetes client + manifest builders for the per-account agent-runner objects.

Bodies are plain dicts (the client serializes them). All create_* are idempotent
(409 AlreadyExists is swallowed) so reconcile can run repeatedly.
"""

from __future__ import annotations

import time

from kubernetes import client, config

from priva_common.logging import get_app_logger
from priva_operator import names

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


def _ignore_conflict(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except client.ApiException as exc:
        if exc.status == 409:  # AlreadyExists
            return None
        raise


# --- manifest builders ------------------------------------------------------

def _deployment_body(namespace, account_id, username, image, pull_policy, settings, owner) -> dict:
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


def _pvc_body(namespace, account_id, owner) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {"name": names.pvc_name(account_id), "namespace": namespace,
                     "labels": names.labels(account_id), "ownerReferences": [owner]},
        "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "1Gi"}}},
    }


# --- reconcile primitives ---------------------------------------------------

def ensure_runtime_objects(namespace, account_id, username, image, pull_policy, settings, owner) -> None:
    _ignore_conflict(core().create_namespaced_persistent_volume_claim,
                     namespace, _pvc_body(namespace, account_id, owner))
    _ignore_conflict(core().create_namespaced_service,
                     namespace, _service_body(namespace, account_id, settings.kubernetes.runner_service_port, owner))
    _ignore_conflict(apps().create_namespaced_deployment,
                     namespace, _deployment_body(namespace, account_id, username, image, pull_policy, settings, owner))


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


def wait_pod_ready(namespace, account_id, timeout: float = 60.0) -> str | None:
    """Poll until a pod for this account is Ready; return its podIP."""
    deadline = time.monotonic() + timeout
    selector = f"priva.io/account-id={account_id}"
    while time.monotonic() < deadline:
        pods = core().list_namespaced_pod(namespace, label_selector=selector).items
        for p in pods:
            ready = any(c.type == "Ready" and c.status == "True" for c in (p.status.conditions or []))
            if ready and p.status.pod_ip:
                return p.status.pod_ip
        time.sleep(1.5)
    return None
