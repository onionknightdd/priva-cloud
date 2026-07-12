"""Waker — the scheduler's only Kubernetes surface (design §4/§6, drill SR2).

Lifts the control-panel EPP's proven wake pattern (provisioner.py): patch
``AgentTenant spec.wake.requestedAt`` (the ONLY scale-up trigger — the operator
is the sole scaler) and poll the CR status until the operator reports the pod
Running with an IP. RBAC needs exactly ``agenttenants`` get/patch +
``agenttenants/status`` read — no pod verbs, ever.
"""

from __future__ import annotations

import asyncio
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _status(account_id: str) -> dict:
    s = get_settings()
    try:
        obj = _custom().get_namespaced_custom_object(
            GROUP, VERSION, s.kubernetes.namespace_tenants, PLURAL, account_id)
        return obj.get("status") or {}
    except client.ApiException as exc:
        if exc.status == 404:
            return {}
        raise


def _patch_wake(account_id: str) -> None:
    s = get_settings()
    _custom().patch_namespaced_custom_object(
        GROUP, VERSION, s.kubernetes.namespace_tenants, PLURAL, account_id,
        {"spec": {"wake": {"requestedAt": _now_iso()}}})


def _running(status: dict) -> bool:
    return status.get("phase") == "Running" and bool(status.get("podIP"))


async def wake_and_wait(account_id: str) -> bool:
    """Ensure the account's pod is awake. True = Ready (dial the stable Service
    DNS ``ar-{account_id}``); False = not up within ``wake_timeout_seconds``
    (the dispatcher's retry loop re-enters here).

    Warm path skips the patch entirely; blocking kube calls run in threads so
    concurrent fires never stall the clock loop.
    """
    s = get_settings()
    try:
        if _running(await asyncio.to_thread(_status, account_id)):
            return True
        await asyncio.to_thread(_patch_wake, account_id)
    except client.ApiException as exc:
        logger.warning("wake patch failed account={}: {}", account_id, exc)
        return False

    deadline = time.monotonic() + float(s.kubernetes.wake_timeout_seconds)
    while time.monotonic() < deadline:
        try:
            if _running(await asyncio.to_thread(_status, account_id)):
                return True
        except client.ApiException as exc:
            logger.warning("wake status poll failed account={}: {}", account_id, exc)
            return False
        await asyncio.sleep(0.5)
    logger.warning("wake timed out account={} after {}s", account_id, s.kubernetes.wake_timeout_seconds)
    return False
