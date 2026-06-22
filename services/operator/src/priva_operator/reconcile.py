"""kopf reconcile handlers for AgentTenant — the sole scaler (0<->1).

- create/resume: ensure Deployment(0) + Service + PVC exist (idempotent).
- spec.wake.requestedAt change: materialize the creds Secret from data-spine,
  scale 0->1, wait for the pod, record podIP/startedAt on status (the EPP reads it).
  When already scaled to 1, resolve the *real* Ready pod IP instead of trusting status.
- timer: a periodic reconcile (status is derived, not authoritative) that heals
  status.podIP against pod reality, GCs the creds Secret when scaled down, then runs
  the idle /health sweep that scales 1->0 once idle past grace.
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

    # Reality-based guard (#1/#4): when the Deployment is already scaled to 1, don't
    # re-materialize the Secret or re-scale — resolve the *real* Ready pod IP and write
    # it. Trusting status.podIP here would re-bless a dead/replaced pod; resolving from
    # pod reality makes the wake path itself self-correcting, so correctness no longer
    # depends on the timer cadence (the timer only shrinks the EPP warm-path stale window).
    if kube.get_replicas(namespace, account_id) == 1:
        pod_ip = kube.current_ready_pod_ip(namespace, account_id) or kube.wait_pod_ready(
            namespace, account_id, timeout=float(s.kubernetes.wake_timeout_seconds))
        if pod_ip is None:
            patch.status["phase"] = "Waking"  # replacement gap; next wake/timer retries
            logger.warning("wake: replicas==1 but no Ready pod account=%s", account_id)
            return
        if pod_ip != status.get("podIP"):
            # A changed IP means a replacement pod — give it its own min_alive window.
            patch.status["startedAt"] = time.time()
        patch.status["phase"] = "Running"
        patch.status["podIP"] = pod_ip
        patch.status["readyReplicas"] = 1
        patch.status["idleSince"] = None
        logger.info("wake resolved (already scaled to 1) account=%s pod=%s", account_id, pod_ip)
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


@kopf.timer(GROUP, VERSION, PLURAL, interval=10.0, sharp=False)
def reconcile_runtime(spec, name, namespace, status, patch, logger, **_):
    """Periodic reconcile — status is *derived, not authoritative*, so re-derive it each
    tick: (1) GC the creds Secret when scaled down, (2) heal status.podIP against the
    real Ready pod, (3) idle-sweep 1->0 past grace. Cheap pod-list, so the interval is
    short for fast self-heal of a dead/replaced pod (#1)."""
    s = get_settings()
    account_id, _ = _ids(spec, name)
    replicas = kube.get_replicas(namespace, account_id)

    # --- #7: GC the plaintext creds Secret when the runtime is scaled down -----------
    # Reconcile Secret existence against replicas==0, NOT podIP=None: on a *replacement*
    # the Deployment is still at 1 and the new pod needs the Secret via envFrom. Only a
    # genuinely zero-replica runtime (idle-slept, or scaled down out-of-band/offboarded)
    # should lose its Secret.
    if replicas <= 0:
        if secrets.exists(namespace, account_id):
            secrets.delete(namespace, account_id)
            logger.info("gc'd creds secret (replicas=%d) account=%s", replicas, account_id)
        return

    if replicas != 1:
        return  # mid-scale / unexpected — let it settle

    # --- #1: heal status.podIP against the real Ready pod ----------------------------
    real_ip = kube.current_ready_pod_ip(namespace, account_id)
    if real_ip is None:
        # replicas==1 but no Ready, non-terminating pod = the replacement gap. Flip the
        # CR not-routable so the EPP re-resolves, and bail BEFORE the idle probe so we
        # never probe a stale IP.
        patch.status["phase"] = "Waking"
        patch.status["podIP"] = None
        patch.status["readyReplicas"] = 0
        return

    if real_ip != status.get("podIP"):
        # A live pod at an IP status doesn't know — heal. A *changed* podIP is a
        # replacement pod, which must get its own min_alive anti-thrash window (don't let
        # it inherit the dead pod's startedAt clock).
        patch.status["phase"] = "Running"
        patch.status["podIP"] = real_ip
        patch.status["readyReplicas"] = 1
        patch.status["startedAt"] = time.time()
        patch.status["idleSince"] = None
        logger.info("healed stale podIP account=%s -> %s", account_id, real_ip)
        return  # next tick runs the idle check against the now-correct IP

    # --- idle sweep (always against the real, healed IP — never status.podIP) ---------
    started_at = status.get("startedAt")
    if not started_at:
        return
    idle_cfg = spec.get("idle") or {}
    grace = int(idle_cfg.get("graceSeconds", s.kubernetes.idle_grace_seconds))
    min_alive = int(idle_cfg.get("minAliveAfterWakeSeconds", s.kubernetes.min_alive_after_wake_seconds))
    now = time.time()
    if now - float(started_at) < min_alive:
        return

    try:
        port = s.kubernetes.runner_service_port
        h = httpx.get(f"http://{real_ip}:{port}/health", timeout=2.0, trust_env=False).json()
    except Exception as exc:  # unreachable -> don't sleep this tick (safe; retry next)
        logger.debug("idle probe failed account=%s: %s", account_id, exc)
        return

    active = int(h.get("active_runs", 1))
    last = float(h.get("last_activity_ts", now))
    if active == 0 and (now - last) > grace:
        # --- #2: flip status not-routable BEFORE teardown ----------------------------
        # kopf's deferred patch.status can't flip first, so go direct. Shrinks the window
        # where the EPP hands out a doomed endpoint; the residual micro-race is caught by
        # the EPP warm-path liveness probe. Every step is idempotent (a mid-handler
        # resourceVersion bump can 409 → kopf retries the whole handler).
        kube.set_cr_status(namespace, account_id, phase="Zero", podIP=None, readyReplicas=0)
        kube.scale(namespace, account_id, 0)
        secrets.delete(namespace, account_id)
        patch.status["phase"] = "Zero"
        patch.status["podIP"] = None
        patch.status["readyReplicas"] = 0
        patch.status["idleSince"] = now
        logger.info("slept idle account=%s (idle %.0fs > grace %ds)", account_id, now - last, grace)
