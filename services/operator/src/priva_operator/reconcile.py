"""kopf reconcile handlers for AgentTenant — the sole scaler (0<->1).

- create/resume: ensure Deployment(0) + Service + PVC exist (idempotent).
- spec.wake.requestedAt change: materialize the creds Secret from data-spine,
  scale 0->1, wait for the pod, record podIP/startedAt on status (the EPP reads it).
- timer: idle sweep — poll the pod /health and scale 1->0 once idle past grace.
"""

from __future__ import annotations

import time

import httpx
import kopf

from priva_common.config import get_settings
from priva_operator import GROUP, PLURAL, VERSION, kube, names, secrets


def _ids(spec, name):
    account_id = spec.get("accountId") or name
    username = spec.get("username") or account_id
    return account_id, username


@kopf.on.create(GROUP, VERSION, PLURAL)
@kopf.on.resume(GROUP, VERSION, PLURAL)
def ensure(spec, name, namespace, uid, patch, logger, **_):
    s = get_settings()
    account_id, username = _ids(spec, name)
    image = spec.get("image") or s.kubernetes.runner_image
    owner = names.owner_ref(name, uid)
    kube.ensure_runtime_objects(
        namespace, account_id, username, image, s.kubernetes.runner_image_pull_policy, s, owner)
    if kube.get_replicas(namespace, account_id) <= 0:
        patch.status["phase"] = "Zero"
        patch.status["readyReplicas"] = 0
    logger.info("ensured runtime objects for account=%s", account_id)


@kopf.on.field(GROUP, VERSION, PLURAL, field="spec.wake.requestedAt")
def on_wake(spec, name, namespace, uid, status, patch, logger, **_):
    s = get_settings()
    account_id, _username = _ids(spec, name)
    owner = names.owner_ref(name, uid)

    if kube.get_replicas(namespace, account_id) == 1 and status.get("podIP"):
        logger.info("wake no-op (already running) account=%s", account_id)
        return

    n = secrets.materialize(namespace, account_id, owner)
    kube.scale(namespace, account_id, 1)
    patch.status["phase"] = "Waking"
    pod_ip = kube.wait_pod_ready(namespace, account_id, timeout=float(s.kubernetes.wake_timeout_seconds))
    if pod_ip is None:
        patch.status["phase"] = "Waking"  # stays waking; next wake retries
        logger.warning("wake timed out waiting for pod readiness account=%s", account_id)
        return
    patch.status["phase"] = "Running"
    patch.status["podIP"] = pod_ip
    patch.status["readyReplicas"] = 1
    patch.status["startedAt"] = time.time()
    patch.status["idleSince"] = None
    logger.info("woke account=%s pod=%s creds=%d keys", account_id, pod_ip, n)


@kopf.timer(GROUP, VERSION, PLURAL, interval=30.0, sharp=False)
def idle_sweep(spec, name, namespace, status, patch, logger, **_):
    s = get_settings()
    account_id, _ = _ids(spec, name)
    if kube.get_replicas(namespace, account_id) != 1:
        return
    started_at = status.get("startedAt")
    pod_ip = status.get("podIP")
    if not pod_ip or not started_at:
        return

    idle_cfg = spec.get("idle") or {}
    grace = int(idle_cfg.get("graceSeconds", s.kubernetes.idle_grace_seconds))
    min_alive = int(idle_cfg.get("minAliveAfterWakeSeconds", s.kubernetes.min_alive_after_wake_seconds))
    now = time.time()
    if now - float(started_at) < min_alive:
        return

    try:
        port = s.kubernetes.runner_service_port
        h = httpx.get(f"http://{pod_ip}:{port}/health", timeout=2.0, trust_env=False).json()
    except Exception as exc:  # unreachable -> don't sleep this tick (safe; retry next)
        logger.debug("idle probe failed account=%s: %s", account_id, exc)
        return

    active = int(h.get("active_runs", 1))
    last = float(h.get("last_activity_ts", now))
    if active == 0 and (now - last) > grace:
        kube.scale(namespace, account_id, 0)
        secrets.delete(namespace, account_id)
        patch.status["phase"] = "Zero"
        patch.status["podIP"] = None
        patch.status["readyReplicas"] = 0
        patch.status["idleSince"] = now
        logger.info("slept idle account=%s (idle %.0fs > grace %ds)", account_id, now - last, grace)
