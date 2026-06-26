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
from priva_operator import GROUP, PLURAL, VERSION, kube, names, secrets, storage_backend


def _ids(spec, name):
    account_id = spec.get("accountId") or name
    username = spec.get("username") or account_id
    return account_id, username


def _runner_type(spec) -> str:
    return spec.get("agentRunnerType") or "auto_scale"


def _is_persistent(spec) -> bool:
    return _runner_type(spec) == "persistent"


def _runner_defaults():
    """Global runner defaults from data-spine (the admin Sandbox panel). Fail-soft:
    any error (data-spine blip) returns None so the resolvers fall back to the env
    seed — a transient outage must never crash reconcile or mis-size a pod."""
    try:
        from priva_common.dataplane import get_client
        return get_client().runner_defaults.get()
    except Exception:
        return None


@kopf.on.create(GROUP, VERSION, PLURAL)
@kopf.on.resume(GROUP, VERSION, PLURAL)
def ensure(spec, name, namespace, uid, status, patch, logger, **_):
    s = get_settings()
    account_id, username = _ids(spec, name)
    defaults = _runner_defaults()
    image = kube.resolve_image(spec, s, defaults)
    owner = names.owner_ref(name, uid)
    kube.ensure_runtime_objects(
        namespace, account_id, username, image, s.kubernetes.runner_image_pull_policy, s, owner, spec, defaults)

    replicas = kube.get_replicas(namespace, account_id)

    # Persistent runners are always-on: bring them to 1 here (the reconcile-to-desired
    # home that runs on create AND resume, so it self-heals across operator restarts).
    # Guard on desiredState so an offboarding/purge account is never force-woken.
    if _is_persistent(spec) and spec.get("desiredState", "active") == "active":
        if replicas != 1:
            # About to scale up — refresh the template to the current effective config
            # while at 0 (free; the running-pod case below is left untouched).
            kube.patch_deployment_runtime(
                namespace, account_id, kube.resolve_resources(spec, s, defaults), image)
            # Materialize creds first so the pod is request-ready the moment it's Running.
            secrets.materialize(namespace, account_id, owner)
            kube.scale(namespace, account_id, 1)
        pod_ip = kube.wait_pod_ready(namespace, account_id, timeout=float(s.kubernetes.wake_timeout_seconds))
        if pod_ip:
            patch.status["phase"] = "Running"
            patch.status["podIP"] = pod_ip
            patch.status["readyReplicas"] = 1
            # Preserve the min_alive clock across resume; only set it on a fresh pod.
            patch.status["startedAt"] = status.get("startedAt") or time.time()
        else:
            patch.status["phase"] = "Waking"
        logger.info("ensured persistent runner at 1 for account=%s", account_id)
        return

    if replicas <= 0:
        patch.status["phase"] = "Zero"
        patch.status["readyReplicas"] = 0
    logger.info("ensured runtime objects for account=%s type=%s", account_id, _runner_type(spec))


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

    # Cold scale-up: refresh the Deployment template to the current effective config
    # (CR override > global default > env seed) while at replicas 0, so this wake picks
    # up any default change without ever restarting a running pod.
    defaults = _runner_defaults()
    kube.patch_deployment_runtime(
        namespace, account_id, kube.resolve_resources(spec, s, defaults),
        kube.resolve_image(spec, s, defaults))
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
    defaults = _runner_defaults()  # one fetch per tick; reused below

    # --- volume-quota reconcile (restart-free) ---------------------------------------
    # Converge the per-account quota to the effective value (CR override > global default
    # > env seed) so a global storage-default change propagates to inherited accounts
    # WITHOUT a wake. Guard on status.storageGb so the quota-manager is called only on
    # drift, not every 10s tick. Runs even when scaled to zero (the quota outlives the pod).
    desired_gb = kube.resolve_storage_gb(spec, s, defaults)
    if int(status.get("storageGb") or 0) != desired_gb:
        try:
            storage_backend.get_backend(s).set_quota(account_id, desired_gb)
            patch.status["storageGb"] = desired_gb
            patch.status["storageWarning"] = None
            logger.info("reconciled volume quota account=%s -> %dGi", account_id, desired_gb)
        except Exception as exc:  # backend blip — surface, don't crash the tick
            patch.status["storageWarning"] = f"quota reconcile failed: {exc}"
            logger.warning("quota reconcile failed account=%s: %s", account_id, exc)

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

    # Persistent runner: never idle-sweep to zero. podIP self-heal above still runs
    # (so a replaced persistent pod's status stays correct); only the sleep is skipped.
    if _is_persistent(spec):
        return

    # --- idle sweep (always against the real, healed IP — never status.podIP) ---------
    started_at = status.get("startedAt")
    if not started_at:
        return
    # Inherit cascade: CR spec.idle.* (override) > global default > env seed. Read live
    # each tick (`defaults` fetched at the top), so a default change takes effect on the
    # next sweep with NO pod restart.
    idle_cfg = spec.get("idle") or {}
    grace = idle_cfg.get("graceSeconds")
    if grace is None:
        grace = defaults.idle_grace_seconds if defaults else s.kubernetes.idle_grace_seconds
    grace = int(grace)
    min_alive = idle_cfg.get("minAliveAfterWakeSeconds")
    if min_alive is None:
        min_alive = (defaults.min_alive_after_wake_seconds if defaults
                     else s.kubernetes.min_alive_after_wake_seconds)
    min_alive = int(min_alive)
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


# --- live admin edits (CR spec patches from control-panel.update_tenant_runtime) ----
# Each handler skips the CREATE event (old is None) — `ensure` already builds objects
# with the correct resources/storage and scales persistent. They act only on real edits.

@kopf.on.field(GROUP, VERSION, PLURAL, field="spec.agentRunnerType")
def on_runner_type_change(spec, name, namespace, uid, old, new, patch, logger, **_):
    if old is None:
        return
    s = get_settings()
    account_id, _ = _ids(spec, name)
    owner = names.owner_ref(name, uid)
    if new == "persistent" and spec.get("desiredState", "active") == "active":
        if kube.get_replicas(namespace, account_id) != 1:
            secrets.materialize(namespace, account_id, owner)
            kube.scale(namespace, account_id, 1)
            patch.status["phase"] = "Waking"
            patch.status["startedAt"] = time.time()
        logger.info("runner_type -> persistent, pinned to 1 account=%s", account_id)
    elif new == "auto_scale":
        # Re-enable the idle sweep; do NOT eagerly scale down a possibly-busy pod —
        # the next idle tick sweeps it once genuinely idle past grace.
        logger.info("runner_type -> auto_scale, idle sweep re-enabled account=%s", account_id)


@kopf.on.field(GROUP, VERSION, PLURAL, field="spec.resources")
def on_resources_change(spec, name, namespace, old, new, logger, **_):
    if old is None:
        return
    s = get_settings()
    account_id, _ = _ids(spec, name)
    # "Apply on next restart": resources need a pod (re)start, so DON'T patch a running
    # pod (strategy=Recreate would force-restart it). Patch only when dormant (replicas
    # 0) — the change applies cleanly on the next wake; a running pod picks it up via
    # on_wake's pre-scale refresh after it next sleeps/restarts. Resolving with defaults
    # so an override cleared back to inherit re-resolves correctly.
    replicas = kube.get_replicas(namespace, account_id)
    if replicas != 0:
        logger.info("resources change deferred (replicas=%s, applies on next restart) account=%s",
                    replicas, account_id)
        return
    resources = kube.resolve_resources(spec, s, _runner_defaults())
    try:
        kube.patch_deployment_resources(namespace, account_id, resources)
        logger.info("resources updated (dormant) account=%s -> %s", account_id, resources)
    except kube.client.ApiException as exc:
        if exc.status == 404:
            logger.warning("resources change but no Deployment yet account=%s", account_id)
            return
        raise


@kopf.on.field(GROUP, VERSION, PLURAL, field="spec.storageGb")
def on_storage_change(spec, name, namespace, old, new, patch, logger, **_):
    if old is None:
        return
    if new is None:
        # Override cleared (field removed) — back to inherit. Re-resolve from the cascade
        # now; the periodic reconcile would also converge it on the next tick.
        new = kube.resolve_storage_gb(spec, get_settings(), _runner_defaults())
    s = get_settings()
    account_id, _ = _ids(spec, name)
    desired = int(new)
    # Set the per-account quota on the storage backend (XFS project quota in dev). Unlike
    # a PVC, a backend quota can SHRINK, so both grow and shrink are honored directly.
    try:
        storage_backend.get_backend(s).set_quota(account_id, desired)
        patch.status["storageGb"] = desired
        patch.status["storageWarning"] = None
        logger.info("set volume quota account={} -> {}Gi", account_id, desired)
    except Exception as exc:
        # Backend blip — surface it; do NOT re-raise (an un-retriable error would make kopf
        # retry the handler forever). The next reconcile/edit can re-apply.
        patch.status["storageWarning"] = f"quota set failed: {exc}"
        logger.error("quota set rejected account=%s: %s", account_id, exc)
