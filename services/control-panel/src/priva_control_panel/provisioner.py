"""Kubernetes provisioner — control-panel as the control plane.

On user creation: ``ensure_tenant`` writes an AgentTenant CR (the operator
reconciles it into a scale-to-zero Deployment + Service + PVC). On a runtime
request: ``wake_and_wait`` patches spec.wake.requestedAt and polls the CR
status until the operator reports the woken pod's IP — the endpoint the ext_proc
EPP steers agentgateway to. Deterministic naming means no registry is needed.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from kubernetes import client, config

from priva_common.config import get_settings
from priva_common.logging import get_app_logger

logger = get_app_logger(__name__)

GROUP = "priva.io"
VERSION = "v1alpha1"
PLURAL = "agenttenants"

_loaded = False


def _load() -> None:
    global _loaded
    if _loaded:
        return
    s = get_settings()
    try:
        if s.kubernetes.in_cluster:
            config.load_incluster_config()
        else:
            config.load_kube_config(config_file=s.kubernetes.kubeconfig)
    except Exception:
        config.load_kube_config(config_file=s.kubernetes.kubeconfig)
    _loaded = True


def _custom() -> "client.CustomObjectsApi":
    _load()
    return client.CustomObjectsApi()


def _apps() -> "client.AppsV1Api":
    _load()
    return client.AppsV1Api()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_tenant(account_id: str, username: str) -> None:
    """Create the AgentTenant CR for a new account (idempotent)."""
    s = get_settings()
    ns = s.kubernetes.namespace_tenants
    body = {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": "AgentTenant",
        "metadata": {"name": account_id, "namespace": ns},
        "spec": {
            "accountId": account_id,
            "username": username,
            "desiredState": "active",
            "image": s.kubernetes.runner_image,
            "idle": {
                "graceSeconds": s.kubernetes.idle_grace_seconds,
                "minAliveAfterWakeSeconds": s.kubernetes.min_alive_after_wake_seconds,
            },
            "concurrency": {"maxConcurrentSessions": s.kubernetes.max_concurrent_sessions},
        },
    }
    try:
        _custom().create_namespaced_custom_object(GROUP, VERSION, ns, PLURAL, body)
        logger.info("provisioned AgentTenant account={}", account_id)
    except client.ApiException as exc:
        if exc.status != 409:  # AlreadyExists is fine (re-provision)
            raise


def _status(account_id: str) -> dict:
    s = get_settings()
    ns = s.kubernetes.namespace_tenants
    try:
        obj = _custom().get_namespaced_custom_object(GROUP, VERSION, ns, PLURAL, account_id)
        return obj.get("status") or {}
    except client.ApiException as exc:
        if exc.status == 404:
            return {}
        raise


def wake_and_wait(account_id: str) -> str | None:
    """Ensure the account's pod is awake; return the steer endpoint ``ip:port`` or None on timeout."""
    s = get_settings()
    ns = s.kubernetes.namespace_tenants
    port = s.kubernetes.runner_service_port

    st = _status(account_id)
    if st.get("phase") == "Running" and st.get("podIP"):
        return f"{st['podIP']}:{port}"

    # Patch the only scale-up trigger; the operator does the rest.
    try:
        _custom().patch_namespaced_custom_object(
            GROUP, VERSION, ns, PLURAL, account_id, {"spec": {"wake": {"requestedAt": _now_iso()}}})
    except client.ApiException as exc:
        logger.warning("wake patch failed account={}: {}", account_id, exc)
        return None

    deadline = time.monotonic() + float(s.kubernetes.wake_timeout_seconds)
    while time.monotonic() < deadline:
        st = _status(account_id)
        if st.get("phase") == "Running" and st.get("podIP"):
            return f"{st['podIP']}:{port}"
        time.sleep(1.0)
    return None


def terminate(account_id: str) -> None:
    """Admin: scale the account's runner to zero now (alpha: direct scale)."""
    s = get_settings()
    ns = s.kubernetes.namespace_tenants
    try:
        _apps().patch_namespaced_deployment_scale(
            f"ar-{account_id}", ns, {"spec": {"replicas": 0}})
    except client.ApiException as exc:
        if exc.status != 404:
            raise
