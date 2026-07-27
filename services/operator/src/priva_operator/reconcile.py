"""kopf reconcile handlers for AgentTenant — the sole scaler (0<->1).

- create/resume: ensure Deployment(0) + Service + PVC exist (idempotent).
- spec.wake.requestedAt change: scale 0->1, wait for the pod, record podIP/startedAt
  on status (the EPP reads it). When already scaled to 1, resolve the *real* Ready
  pod IP instead of trusting status.
- timer: a periodic reconcile (status is derived, not authoritative) that heals
  status.podIP against pod reality, then runs the idle /health sweep that scales
  1->0 once idle past grace.

Creds are NOT injected by the operator: each account's BYOK creds live in its own
``/workspace/.claude/settings.json`` on the PVC (written + read by the pod itself),
so there is no per-pod Secret to materialize at wake or GC at sleep.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import httpx
import kopf

from priva_common.config import get_settings
from priva_operator import GROUP, PLURAL, VERSION, kube, names, storage_backend

# Throttle the global managed-policy render so N per-account handlers don't each
# hit data-spine every tick. The render is digest-guarded (write only on change);
# this just bounds the read rate. Module-level: shared across all CR handlers.
_MANAGED_POLICY_MIN_INTERVAL = 15.0
_managed_policy_last_render = 0.0
_runner_defaults_cache = None
_runner_defaults_last_attempt = 0.0
_RUNNER_DEFAULTS_MIN_INTERVAL = 15.0

# Teardown budget. kopf holds its finalizer until the delete handler stops asking for a
# retry, so an unbounded retry would wedge the CR in Terminating forever — and with it the
# account row the control plane only sweeps once the CR is gone. Bound the attempts and
# release with a loud log instead: a named leak is recoverable by hand, a stuck CR is not.
_PURGE_MAX_ATTEMPTS = 10
_PURGE_RETRY_BACKOFF = 30.0
_purging: dict[str, str | None] = {}


def _tenants_namespace() -> str:
    return get_settings().kubernetes.namespace_tenants


def _render_managed_policy(namespace, logger, *, force=False) -> None:
    """Digest-guarded, throttled render of the global managed-policy ConfigMap."""
    global _managed_policy_last_render
    now = time.monotonic()
    if not force and (now - _managed_policy_last_render) < _MANAGED_POLICY_MIN_INTERVAL:
        return
    _managed_policy_last_render = now
    try:
        kube.ensure_managed_policy_configmap(namespace)
    except Exception:
        logger.warning("managed-policy reconcile failed", exc_info=True)


@kopf.on.startup()
def bootstrap_managed_policy(logger, **_):
    """Create the managed-policy ConfigMap once at operator boot, before any pod
    mounts it — so a cold-started account wakes with the current enforced set."""
    _render_managed_policy(_tenants_namespace(), logger, force=True)


def _upsert_condition(status, patch, condition_type: str, ok: bool, reason: str, message: str) -> None:
    wanted_status = "True" if ok else "False"
    source = patch.status.get("conditions", status.get("conditions") or [])
    existing = next((item for item in source if item.get("type") == condition_type), None)
    if existing and all((
        existing.get("status") == wanted_status,
        existing.get("reason") == reason,
        existing.get("message") == message,
    )):
        return
    conditions = [
        dict(item) for item in source
        if item.get("type") != condition_type
    ]
    conditions.append({
        "type": condition_type,
        "status": wanted_status,
        "reason": reason,
        "message": message,
        "lastTransitionTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    patch.status["conditions"] = conditions


def _ids(spec, name):
    account_id = spec.get("accountId")
    if not account_id:
        raise ValueError(f"AgentTenant {name!r} is missing required spec.accountId")
    username = spec.get("username")
    if not username:
        raise ValueError(f"AgentTenant {name!r} is missing required spec.username")
    return account_id, username


def _ids_or_reject(spec, name, status, patch, logger):
    try:
        result = _ids(spec, name)
    except ValueError as exc:
        _upsert_condition(status, patch, "IdentityReady", False, "IdentityIncomplete", str(exc))
        logger.error("identity reconcile blocked: %s", exc)
        return None
    _upsert_condition(status, patch, "IdentityReady", True, "Resolved", "account identity is complete")
    return result


def _teardown_started(account_id: str, meta=None) -> bool:
    """True once *this* CR is being purged — nothing may (re)provision for it.

    ``meta.deletionTimestamp`` covers every handler invoked after the delete lands. The
    process-local record additionally covers a timer tick that was already inside the thread
    pool at that moment: kopf stops *spawning* timers for a deleting object but cannot
    cancel a sync handler mid-flight, and such a tick would otherwise re-provision the
    volume the finalizer just reclaimed. Entries are never pruned, so each records the torn
    down CR's ``metadata.uid`` and only blocks that object: an out-of-band ``kubectl delete``
    of a still-active account's CR must not brick the CR the control plane then re-creates.
    An unknown uid on either side keeps the conservative "block this account" answer.
    """
    meta = meta or {}
    if account_id in _purging:
        purged_uid = _purging[account_id]
        uid = meta.get("uid")
        if purged_uid is None or uid is None or purged_uid == uid:
            return True
    return bool(meta.get("deletionTimestamp"))


def _quiesce_if_inactive(spec, name, namespace, status, patch) -> bool:
    """Enforce lifecycle shutdown without depending on identity or data-spine config."""
    desired_state = spec.get("desiredState", "active")
    if desired_state == "active":
        return False
    account_id = spec.get("accountId") or name
    phase = "Purged" if desired_state == "purge" else "Offboarding"
    replicas = kube.get_replicas(namespace, account_id)
    terminal_replicas = kube.get_terminal_replicas(namespace, account_id)
    if replicas > 0:
        kube.set_cr_status(
            namespace, account_id, phase=phase, podIP=None, readyReplicas=0)
        kube.scale(namespace, account_id, 0)
    terminal_status = {
        **(status.get("terminal") or {}), "phase": phase, "podIP": None,
        "readyReplicas": 0, "activeSessions": 0,
    }
    if terminal_replicas > 0:
        kube.set_cr_status(namespace, account_id, terminal=terminal_status)
        kube.scale_terminal(namespace, account_id, 0)
    if status.get("terminal") != terminal_status:
        patch.status["terminal"] = terminal_status
    if (status.get("phase") != phase or status.get("podIP") is not None
            or int(status.get("readyReplicas") or 0) != 0):
        patch.status["phase"] = phase
        patch.status["podIP"] = None
        patch.status["readyReplicas"] = 0
    return True


def _runner_type(spec) -> str:
    return spec.get("agentRunnerType") or "auto_scale"


def _is_persistent(spec) -> bool:
    return _runner_type(spec) == "persistent"


def _defaults_from_spec(spec):
    raw = spec.get("runtimeDefaults") or {}
    terminal = raw.get("terminal") or {}
    required = (
        "idleGraceSeconds", "minAliveAfterWakeSeconds", "cpuCores", "memoryMb",
        "storageGb",
    )
    terminal_required = (
        "resourcePercent", "maxSessions", "idleTimeoutSeconds",
        "maxLifetimeSeconds", "scaleDownGraceSeconds",
    )
    if not all(key in raw for key in required) or not all(
            key in terminal for key in terminal_required):
        return None
    return SimpleNamespace(
        idle_grace_seconds=int(raw["idleGraceSeconds"]),
        min_alive_after_wake_seconds=int(raw["minAliveAfterWakeSeconds"]),
        cpu_cores=float(raw["cpuCores"]),
        memory_mb=int(raw["memoryMb"]),
        storage_gb=int(raw["storageGb"]),
        terminal_resource_percent=int(terminal["resourcePercent"]),
        terminal_max_sessions=int(terminal["maxSessions"]),
        terminal_idle_timeout_seconds=int(terminal["idleTimeoutSeconds"]),
        terminal_max_lifetime_seconds=int(terminal["maxLifetimeSeconds"]),
        terminal_scale_down_grace_seconds=int(terminal["scaleDownGraceSeconds"]),
    )


def _runner_defaults(spec=None):
    """Resolve a CR-local snapshot, then a process-wide last-known-good record.

    Returning ``None`` means no safe desired state exists. Callers must make no
    workload/quota mutation in that case; in particular it is never interpreted as a
    zero-percent Terminal policy.
    """
    global _runner_defaults_cache, _runner_defaults_last_attempt
    snapshot = _defaults_from_spec(spec or {})
    if snapshot is not None:
        return snapshot
    now = time.monotonic()
    if now - _runner_defaults_last_attempt < _RUNNER_DEFAULTS_MIN_INTERVAL:
        return _runner_defaults_cache
    _runner_defaults_last_attempt = now
    try:
        from priva_common.dataplane import get_client
        _runner_defaults_cache = get_client().runner_defaults.get()
    except Exception:
        pass
    return _runner_defaults_cache


def _defaults_or_reject(spec, status, patch, logger):
    defaults = _runner_defaults(spec)
    if defaults is None:
        _upsert_condition(
            status, patch, "ConfigurationReady", False, "DefaultsUnavailable",
            "runtimeDefaults is incomplete and data-spine has no last-known-good value",
        )
        logger.warning("runtime reconcile deferred: no safe defaults snapshot")
        return None
    _upsert_condition(
        status, patch, "ConfigurationReady", True, "Resolved",
        "runtime defaults snapshot is available",
    )
    return defaults


def _begin_terminal_drain(pod_ip: str, port: int, revision: int, logger) -> bool:
    """Atomically stop new terminal reservations iff the observed revision is current."""
    try:
        with httpx.Client(timeout=2.0, trust_env=False) as cx:
            response = cx.post(
                f"http://{pod_ip}:{port}/internal/drain",
                params={"revision": int(revision)},
            )
    except Exception as exc:
        logger.debug("terminal drain request failed pod=%s: %s", pod_ip, exc)
        return False
    if response.status_code == 200:
        return True
    if response.status_code not in (404, 409):
        logger.warning("terminal drain rejected pod=%s status=%s", pod_ip, response.status_code)
    return False


@kopf.on.create(GROUP, VERSION, PLURAL)
@kopf.on.resume(GROUP, VERSION, PLURAL)
def ensure(spec, name, namespace, uid, status, patch, logger, meta=None, **_):
    s = get_settings()
    if _teardown_started(spec.get("accountId") or name, meta):
        return
    if _quiesce_if_inactive(spec, name, namespace, status, patch):
        return
    identity = _ids_or_reject(spec, name, status, patch, logger)
    if identity is None:
        return
    account_id, username = identity
    defaults = _defaults_or_reject(spec, status, patch, logger)
    if defaults is None:
        return
    desired_generation = kube.allocation_hash(spec, s, defaults, username)
    if status.get("desiredAllocationHash") != desired_generation:
        patch.status["desiredAllocationHash"] = desired_generation

    image = kube.resolve_image(spec, s)
    owner = names.owner_ref(name, uid)
    # Make sure the global managed-policy CM exists before this account's pod
    # mounts it (force past the throttle on create/resume).
    _render_managed_policy(namespace, logger, force=True)
    kube.ensure_runtime_objects(
        namespace, account_id, username, image, s.kubernetes.runner_image_pull_policy, s, owner, spec, defaults)

    terminal_percent = kube.resolve_terminal_percent(s, defaults)
    if terminal_percent > 0:
        kube.ensure_terminal_objects(
            namespace, account_id, username, image, s.kubernetes.runner_image_pull_policy,
            s, owner, spec, defaults)
        if kube.get_terminal_replicas(namespace, account_id) <= 0:
            patch.status["terminal"] = {
                **(status.get("terminal") or {}),
                "phase": "Zero",
                "readyReplicas": 0,
                "resourcePercent": terminal_percent,
            }
    else:
        terminal_replicas = kube.get_terminal_replicas(namespace, account_id)
        if terminal_replicas > 0:
            kube.scale_terminal(namespace, account_id, 0)
        patch.status["terminal"] = {
            **(status.get("terminal") or {}),
            "phase": "Disabled",
            "podIP": None,
            "readyReplicas": 0,
            "activeSessions": 0,
            "resourcePercent": 0,
        }

    replicas = kube.get_replicas(namespace, account_id)

    # Persistent runners are always-on: bring them to 1 here (the reconcile-to-desired
    # home that runs on create AND resume, so it self-heals across operator restarts).
    # Guard on desiredState so an offboarding/purge account is never force-woken.
    if _is_persistent(spec) and spec.get("desiredState", "active") == "active":
        if replicas < 0:
            patch.status["phase"] = "PendingTerminalDrain"
            patch.status["podIP"] = None
            patch.status["readyReplicas"] = 0
            logger.warning("persistent Runner create deferred by live Terminal account=%s", account_id)
            return
        if replicas != 1:
            # ensure_runtime_objects above already converged the full template to the
            # current effective config while at 0 — just scale up.
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


@kopf.on.delete(
    GROUP, VERSION, PLURAL, retries=_PURGE_MAX_ATTEMPTS, backoff=_PURGE_RETRY_BACKOFF)
def purge(spec, name, namespace, logger, uid=None, retry=0, **_):
    """Reclaim what Kubernetes will not, then let kopf release the finalizer.

    Deployments and Services carry an ownerReference, so the API server collects them on
    its own; the account's volume is either not a Kubernetes object at all (dev: a loop
    image on the NFS box) or an unowned claim (prod), so it is reclaimed here or never.
    Identity comes off the spec the way ``_quiesce_if_inactive`` takes it, so teardown
    never blocks on an incomplete spec, and every step tolerates an already-done
    predecessor — kopf re-enters this handler after a crash or a failed attempt.
    """
    s = get_settings()
    account_id = spec.get("accountId") or name
    _purging[account_id] = uid
    try:
        if kube.get_replicas(namespace, account_id) > 0:
            kube.scale(namespace, account_id, 0)
        if kube.get_terminal_replicas(namespace, account_id) > 0:
            kube.scale_terminal(namespace, account_id, 0)
        storage_backend.get_backend(s).deprovision(account_id)
        # Safety net for a backend that does not own the claim. cephfs does own it and
        # just deleted it; pvc-protection keeps the object visible while it terminates,
        # so a hit there is expected rather than something left behind.
        if (kube.delete_export_claim(namespace, account_id)
                and s.kubernetes.storage_backend != "cephfs"):
            logger.warning(
                "purge: reclaimed export claim the backend left behind account=%s claim=%s",
                account_id, names.export_claim(account_id))
    except Exception as exc:
        if retry + 1 < _PURGE_MAX_ATTEMPTS:
            logger.warning("purge: volume reclaim failed account=%s: %s", account_id, exc)
            raise
        logger.error(
            "purge: releasing finalizer after %d failed reclaim attempts account=%s "
            "backend=%s claim=%s — the account volume is LEAKED and needs manual "
            "reclamation: %s",
            _PURGE_MAX_ATTEMPTS, account_id, s.kubernetes.storage_backend,
            names.export_claim(account_id), exc)
        return
    logger.info("purged account=%s", account_id)


@kopf.on.field(GROUP, VERSION, PLURAL, field="spec.wake.requestedAt")
def on_wake(spec, name, namespace, uid, status, patch, logger, meta=None, **_):
    s = get_settings()
    if _teardown_started(spec.get("accountId") or name, meta):
        return
    if _quiesce_if_inactive(spec, name, namespace, status, patch):
        return
    identity = _ids_or_reject(spec, name, status, patch, logger)
    if identity is None:
        return
    account_id, username = identity
    # Reality-based guard (#1/#4): when the Deployment is already scaled to 1, don't
    # re-scale — resolve the *real* Ready pod IP and write it. Trusting status.podIP
    # here would re-bless a dead/replaced pod; resolving from
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

    # Cold scale-up: converge the FULL Deployment template (volumes/env/mounts, not just
    # image+resources) to the current effective config (resources: CR override > global
    # default; image: CR override > operator settings) while at replicas 0 — so a tenant
    # born under an older operator picks up
    # template additions on its next wake, without ever restarting a running pod.
    defaults = _defaults_or_reject(spec, status, patch, logger)
    if defaults is None:
        return
    kube.ensure_runtime_objects(
        namespace, account_id, username, kube.resolve_image(spec, s),
        s.kubernetes.runner_image_pull_policy, s, names.owner_ref(name, uid), spec, defaults)
    if kube.get_replicas(namespace, account_id) < 0:
        patch.status["phase"] = "PendingTerminalDrain"
        patch.status["podIP"] = None
        patch.status["readyReplicas"] = 0
        return
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
    logger.info("woke account=%s pod=%s", account_id, pod_ip)


@kopf.on.field(GROUP, VERSION, PLURAL, field="spec.terminalWake.requestedAt")
def on_terminal_wake(spec, name, namespace, uid, status, patch, logger, meta=None, **_):
    """Wake only the independent Terminal Deployment. Runner state is untouched."""
    s = get_settings()
    if _teardown_started(spec.get("accountId") or name, meta):
        return
    if _quiesce_if_inactive(spec, name, namespace, status, patch):
        return
    identity = _ids_or_reject(spec, name, status, patch, logger)
    if identity is None:
        return
    account_id, username = identity
    defaults = _defaults_or_reject(spec, status, patch, logger)
    if defaults is None:
        return
    percent = kube.resolve_terminal_percent(s, defaults)
    desired_generation = kube.allocation_hash(spec, s, defaults, username)
    if status.get("desiredAllocationHash") != desired_generation:
        patch.status["desiredAllocationHash"] = desired_generation
    current = status.get("terminal") or {}
    if percent <= 0:
        patch.status["terminal"] = {
            **current, "phase": "Disabled", "podIP": None, "readyReplicas": 0,
            "activeSessions": 0, "resourcePercent": 0,
        }
        return

    # Never let a newly-enabled Terminal overlap a still-running Runner that has
    # the old, unsplit resource template. Dormant runners are converged without a
    # restart; active runners keep running and Terminal reports pending until their
    # next zero-to-one convergence (for example, Admin shutdown followed by the
    # next request).  We intentionally do not mutate a live Runner's cgroup budget.
    runner_replicas = kube.get_replicas(namespace, account_id)
    terminal_replicas = kube.get_terminal_replicas(namespace, account_id)
    applied_percent = None
    applied_generation = kube.applied_allocation_hash(namespace, account_id)
    if runner_replicas == 0 and terminal_replicas <= 0:
        kube.ensure_runtime_objects(
            namespace, account_id, username, kube.resolve_image(spec, s),
            s.kubernetes.runner_image_pull_policy, s, names.owner_ref(name, uid), spec, defaults)
        applied_generation = kube.applied_allocation_hash(namespace, account_id)
    if runner_replicas > 0:
        applied_percent = kube.applied_terminal_percent(namespace, account_id)
    if terminal_replicas <= 0 and runner_replicas > 0 and applied_generation != desired_generation:
        patch.status["terminal"] = {
            **current, "phase": "PendingRunnerRestart", "podIP": None,
            "readyReplicas": 0, "resourcePercent": int(applied_percent or 0),
            "allocationHash": applied_generation,
        }
        logger.info("terminal wake deferred until runner restart account=%s", account_id)
        return

    replicas = terminal_replicas
    if replicas == 1:
        pod_ip = (kube.current_ready_terminal_pod_ip(namespace, account_id)
                  or kube.wait_terminal_pod_ready(
                      namespace, account_id, timeout=float(s.kubernetes.wake_timeout_seconds)))
        if pod_ip is None:
            patch.status["terminal"] = {
                **current, "phase": "Waking", "podIP": None, "readyReplicas": 0,
                "resourcePercent": percent, "allocationHash": applied_generation,
            }
            return
        patch.status["terminal"] = {
            **current, "phase": "Running", "podIP": pod_ip, "readyReplicas": 1,
            "resourcePercent": percent, "allocationHash": applied_generation,
            "startedAt": (time.time() if pod_ip != current.get("podIP")
                          else current.get("startedAt") or time.time()),
        }
        return

    image = kube.resolve_image(spec, s)
    kube.ensure_terminal_objects(
        namespace, account_id, username, image, s.kubernetes.runner_image_pull_policy,
        s, names.owner_ref(name, uid), spec, defaults)
    kube.scale_terminal(namespace, account_id, 1)
    patch.status["terminal"] = {
        **current, "phase": "Waking", "podIP": None, "readyReplicas": 0,
        "resourcePercent": percent, "allocationHash": desired_generation,
    }
    pod_ip = kube.wait_terminal_pod_ready(
        namespace, account_id, timeout=float(s.kubernetes.wake_timeout_seconds))
    if pod_ip is None:
        logger.warning("terminal wake timed out account=%s", account_id)
        return
    patch.status["terminal"] = {
        **current, "phase": "Running", "podIP": pod_ip, "readyReplicas": 1,
        "resourcePercent": percent, "allocationHash": desired_generation,
        "startedAt": time.time(), "activeSessions": 0,
    }
    logger.info("woke terminal account=%s pod=%s", account_id, pod_ip)


@kopf.timer(GROUP, VERSION, PLURAL, interval=10.0, sharp=False)
def reconcile_terminal(spec, name, namespace, uid, status, patch, logger, meta=None, **_):
    """Derive Terminal status and scale 1->0 after its last session plus grace."""
    s = get_settings()
    if _teardown_started(spec.get("accountId") or name, meta):
        return
    if _quiesce_if_inactive(spec, name, namespace, status, patch):
        return
    identity = _ids_or_reject(spec, name, status, patch, logger)
    if identity is None:
        return
    account_id, username = identity
    defaults = _defaults_or_reject(spec, status, patch, logger)
    if defaults is None:
        return
    percent = kube.resolve_terminal_percent(s, defaults)
    desired_generation = kube.allocation_hash(spec, s, defaults, username)
    if status.get("desiredAllocationHash") != desired_generation:
        patch.status["desiredAllocationHash"] = desired_generation
    current = status.get("terminal") or {}
    replicas = kube.get_terminal_replicas(namespace, account_id)

    if percent <= 0:
        disabled = {
            **current, "phase": "Disabled", "podIP": None, "readyReplicas": 0,
            "activeSessions": 0, "resourcePercent": 0,
        }
        if replicas > 0:
            kube.set_cr_status(namespace, account_id, terminal=disabled)
            kube.scale_terminal(namespace, account_id, 0)
        if current != disabled:
            patch.status["terminal"] = disabled
        return

    runner_replicas = kube.get_replicas(namespace, account_id)
    applied_percent = kube.applied_terminal_percent(namespace, account_id)
    applied_generation = kube.applied_allocation_hash(namespace, account_id)
    pending_runner_restart = (
        runner_replicas > 0 and applied_generation != desired_generation)

    # A dormant Terminal cannot safely start beside a live Runner whose resource
    # split belongs to an older allocation generation. Keep this state stable until
    # the Runner next reaches zero; the former logic overwrote PendingRunnerRestart
    # with Zero every 10 seconds and made the Agent UI offer a guaranteed-503 shell.
    if replicas <= 0 and pending_runner_restart:
        patch.status["terminal"] = {
            **current, "phase": "PendingRunnerRestart", "podIP": None,
            "readyReplicas": 0, "activeSessions": 0,
            "resourcePercent": int(applied_percent or 0),
            "allocationHash": applied_generation,
        }
        return

    if replicas < 0:
        if _teardown_started(account_id, meta):
            return
        kube.ensure_terminal_objects(
            namespace, account_id, username, kube.resolve_image(spec, s),
            s.kubernetes.runner_image_pull_policy, s, names.owner_ref(name, uid), spec, defaults)
        patch.status["terminal"] = {
            **current, "phase": "Zero", "readyReplicas": 0,
            "resourcePercent": percent, "allocationHash": desired_generation,
        }
        return
    if replicas == 0:
        if (current.get("phase") != "Zero"
                or current.get("resourcePercent") != percent
                or current.get("allocationHash") != applied_generation):
            patch.status["terminal"] = {
                **current, "phase": "Zero", "podIP": None, "readyReplicas": 0,
                "activeSessions": 0, "resourcePercent": percent,
                "allocationHash": applied_generation,
            }
        return
    if replicas != 1:
        return

    running_percent = int(applied_percent if applied_percent is not None else percent)
    real_ip = kube.current_ready_terminal_pod_ip(namespace, account_id)
    if real_ip is None:
        patch.status["terminal"] = {
            **current, "phase": "Waking", "podIP": None, "readyReplicas": 0,
            "resourcePercent": running_percent, "allocationHash": applied_generation,
        }
        return

    try:
        port = s.kubernetes.terminal_service_port
        health = httpx.get(f"http://{real_ip}:{port}/health", timeout=2.0, trust_env=False).json()
    except Exception as exc:
        logger.debug("terminal health probe failed account=%s: %s", account_id, exc)
        return
    active = int(health.get("active_sessions", 0))
    last_activity = float(health.get("last_activity_ts", time.time()))
    session_revision = health.get("session_revision")
    now = time.time()
    legacy_draining = current.get("phase") == "DrainingLegacy"
    desired_status = {
        **current,
        "phase": ("DrainingLegacy" if legacy_draining else "Running"),
        "podIP": real_ip, "readyReplicas": 1,
        "activeSessions": active, "lastActivityTs": last_activity,
        "resourcePercent": running_percent, "allocationHash": applied_generation,
    }
    grace = int(getattr(defaults, "terminal_scale_down_grace_seconds",
                        s.kubernetes.terminal_scale_down_grace_seconds))
    if active == 0 and (legacy_draining or now - last_activity >= grace):
        if session_revision is None and not legacy_draining:
            # Upgrade bridge for a running old terminald image. Status is the admission
            # gate: Control Panel returns 503 without patching terminalWake while this
            # phase is visible. A later timer re-probes the pod before scaling, catching
            # any request that was already in flight when the gate flipped.
            draining = {
                **desired_status, "phase": "DrainingLegacy", "activeSessions": 0,
                "drainStartedAt": now,
            }
            kube.set_cr_status(namespace, account_id, terminal=draining)
            patch.status["terminal"] = draining
            return
        # terminald performs the revision check and flips its reservation gate under
        # one mutex. A WebSocket accepted after our health read makes this return 409;
        # the status-gated legacy path above handles older terminald images.
        if session_revision is not None and not _begin_terminal_drain(
                real_ip, port, int(session_revision), logger):
            return
        if session_revision is not None:
            draining = {
                **desired_status, "phase": "Draining", "activeSessions": 0,
            }
            kube.set_cr_status(namespace, account_id, terminal=draining)
        zero = {
            **desired_status,
            "phase": ("PendingRunnerRestart" if pending_runner_restart else "Zero"),
            "podIP": None, "readyReplicas": 0,
            "activeSessions": 0, "idleSince": last_activity,
        }
        kube.scale_terminal(namespace, account_id, 0)
        patch.status["terminal"] = zero
        logger.info("slept idle terminal account=%s", account_id)
        return
    patch.status["terminal"] = desired_status


@kopf.timer(GROUP, VERSION, PLURAL, interval=10.0, sharp=False)
def reconcile_runtime(spec, name, namespace, status, patch, logger, uid=None, meta=None, **_):
    """Periodic reconcile — status is *derived, not authoritative*, so re-derive it each
    tick: (1) heal status.podIP against the real Ready pod, (2) idle-sweep 1->0 past
    grace. Cheap pod-list, so the interval is short for fast self-heal of a dead/replaced
    pod (#1)."""
    s = get_settings()
    if _teardown_started(spec.get("accountId") or name, meta):
        return
    if _quiesce_if_inactive(spec, name, namespace, status, patch):
        return
    identity = _ids_or_reject(spec, name, status, patch, logger)
    if identity is None:
        return
    account_id, username = identity
    replicas = kube.get_replicas(namespace, account_id)
    defaults = _defaults_or_reject(spec, status, patch, logger)
    if defaults is None:
        return
    desired_generation = kube.allocation_hash(spec, s, defaults, username)
    if status.get("desiredAllocationHash") != desired_generation:
        patch.status["desiredAllocationHash"] = desired_generation

    # Converge the global managed policy (admin hook edits have no CR event, so
    # this timer is how they propagate). Throttled + digest-guarded → cheap.
    _render_managed_policy(namespace, logger)

    # --- volume-quota reconcile (restart-free) ---------------------------------------
    # Converge the per-account quota to the effective value (CR override > global default
    # > env seed) so a global storage-default change propagates to inherited accounts
    # WITHOUT a wake. Guard on status.storageGb so the quota-manager is called only on
    # drift, not every 10s tick. Runs even when scaled to zero (the quota outlives the pod).
    desired_gb = kube.resolve_storage_gb(spec, s, defaults)
    rejected_gb = status.get("storageRejectedGb")
    retry_after = float(status.get("storageRetryAfter") or 0)
    quota_needs_sync = int(status.get("storageGb") or 0) != desired_gb
    if quota_needs_sync and rejected_gb != desired_gb and time.time() >= retry_after:
        try:
            storage_backend.get_backend(s).set_quota(account_id, desired_gb)
            patch.status["storageGb"] = desired_gb
            patch.status["storageWarning"] = None
            patch.status["storageRejectedGb"] = None
            patch.status["storageRetryAfter"] = None
            logger.info("reconciled volume quota account=%s -> %dGi", account_id, desired_gb)
        except storage_backend.QuotaRejectedError as exc:
            # 409/shrink rejection is stable for this desired value. Remember it so a
            # 10-second timer does not hammer the backend forever; a changed desired_gb
            # automatically clears the guard and is attempted once.
            patch.status["storageRejectedGb"] = desired_gb
            patch.status["storageRetryAfter"] = None
            patch.status["storageWarning"] = f"quota rejected: {exc}"
            logger.warning("quota rejected account=%s desired=%dGi: %s", account_id, desired_gb, exc)
        except Exception as exc:  # transient backend blip — bounded retry cadence
            patch.status["storageWarning"] = f"quota reconcile failed: {exc}"
            patch.status["storageRetryAfter"] = time.time() + 60.0
            logger.warning("quota reconcile failed account=%s: %s", account_id, exc)

    # A dormant object is the safe convergence boundary for all identity/allocation
    # changes. Persistent runners are then restored to one, closing the desired-state
    # loop even after an out-of-band scale or operator restart.
    if replicas <= 0:
        if _teardown_started(account_id, meta):
            return
        applied_generation = kube.applied_allocation_hash(namespace, account_id)
        if applied_generation != desired_generation:
            kube.ensure_runtime_objects(
                namespace, account_id, username, kube.resolve_image(spec, s),
                s.kubernetes.runner_image_pull_policy, s,
                names.owner_ref(name, uid), spec, defaults)
        if _is_persistent(spec):
            if kube.get_replicas(namespace, account_id) < 0:
                patch.status["phase"] = "PendingTerminalDrain"
                patch.status["podIP"] = None
                patch.status["readyReplicas"] = 0
                return
            kube.scale(namespace, account_id, 1)
            patch.status["phase"] = "Waking"
            patch.status["podIP"] = None
            patch.status["readyReplicas"] = 0
            patch.status["startedAt"] = time.time()
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

    applied_generation = kube.applied_allocation_hash(namespace, account_id)
    if applied_generation != desired_generation:
        _upsert_condition(
            status, patch, "AllocationReady", False, "PendingRunnerRestart",
            "running Runner uses an older identity or allocation generation",
        )
    else:
        _upsert_condition(
            status, patch, "AllocationReady", True, "Applied",
            "Runner and desired allocation generations match",
        )

    # Persistent runners do not idle-scale, but an idle instance with a stale template
    # gets a controlled zero boundary so it cannot remain wrong forever.
    if _is_persistent(spec):
        if applied_generation != desired_generation:
            try:
                port = s.kubernetes.runner_service_port
                health = httpx.get(
                    f"http://{real_ip}:{port}/health", timeout=2.0, trust_env=False).json()
            except Exception as exc:
                logger.debug("persistent convergence probe failed account=%s: %s", account_id, exc)
                return
            if int(health.get("active_runs", 1)) == 0:
                kube.set_cr_status(
                    namespace, account_id, phase="Waking", podIP=None, readyReplicas=0)
                kube.scale(namespace, account_id, 0)
                patch.status["phase"] = "Waking"
                patch.status["podIP"] = None
                patch.status["readyReplicas"] = 0
                logger.info("restarting idle persistent Runner for allocation account=%s", account_id)
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
        patch.status["phase"] = "Zero"
        patch.status["podIP"] = None
        patch.status["readyReplicas"] = 0
        patch.status["idleSince"] = now
        logger.info("slept idle account=%s (idle %.0fs > grace %ds)", account_id, now - last, grace)


# --- live admin edits (CR spec patches from control-panel.update_tenant_runtime) ----
# Each handler skips the CREATE event (old is None) — `ensure` already builds objects
# with the correct resources/storage and scales persistent. They act only on real edits.

@kopf.on.field(GROUP, VERSION, PLURAL, field="spec.desiredState")
def on_desired_spec_change(
    spec, name, namespace, uid, status, patch, logger, old=None, meta=None, **_
):
    """Immediately converge lifecycle gates; identity/default drift uses the timers.

    A global defaults edit patches every CR. Avoid attaching a field handler to that
    snapshot: doing so would fan out storage/policy work at once. The bounded per-tenant
    timers converge dormant templates, while live pods keep their safe restart boundary.
    """
    if old is None and not status:
        return  # initial CREATE is handled once by ensure(), not once per watched field
    ensure(
        spec=spec, name=name, namespace=namespace, uid=uid, status=status,
        patch=patch, logger=logger, meta=meta,
    )


@kopf.on.field(GROUP, VERSION, PLURAL, field="spec.agentRunnerType")
def on_runner_type_change(
    spec, name, namespace, uid, old, new, status, patch, logger, meta=None, **_
):
    if old is None:
        return
    account_id, _ = _ids(spec, name)
    if new == "persistent" and spec.get("desiredState", "active") == "active":
        # Converge/create the dormant template before scale-up. Directly scaling an
        # absent or stale Deployment can either 404 or violate the allocation boundary.
        ensure(
            spec=spec, name=name, namespace=namespace, uid=uid, status=status,
            patch=patch, logger=logger, meta=meta,
        )
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
    terminal_replicas = kube.get_terminal_replicas(namespace, account_id)
    if replicas != 0 or terminal_replicas > 0:
        logger.info(
            "resources change deferred (runner=%s terminal=%s, applies when both are dormant) account=%s",
            replicas, terminal_replicas, account_id)
        return
    defaults = _runner_defaults(spec)
    if defaults is None:
        logger.warning("resources change deferred: no safe defaults account=%s", account_id)
        return
    resources = kube.resolve_resources(spec, s, defaults)
    try:
        kube.patch_deployment_resources(namespace, account_id, resources)
        if kube.get_terminal_replicas(namespace, account_id) == 0:
            kube.patch_terminal_resources(
                namespace, account_id, kube.resolve_terminal_resources(spec, s, defaults))
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
        defaults = _runner_defaults(spec)
        if defaults is None:
            logger.warning("storage change deferred: no safe defaults account=%s", name)
            return
        new = kube.resolve_storage_gb(spec, get_settings(), defaults)
    s = get_settings()
    account_id, _ = _ids(spec, name)
    desired = int(new)
    # Set the per-account quota on the storage backend (XFS project quota in dev). Unlike
    # a PVC, a backend quota can SHRINK, so both grow and shrink are honored directly.
    try:
        storage_backend.get_backend(s).set_quota(account_id, desired)
        patch.status["storageGb"] = desired
        patch.status["storageWarning"] = None
        patch.status["storageRejectedGb"] = None
        patch.status["storageRetryAfter"] = None
        logger.info("set volume quota account={} -> {}Gi", account_id, desired)
    except storage_backend.QuotaRejectedError as exc:
        patch.status["storageRejectedGb"] = desired
        patch.status["storageRetryAfter"] = None
        patch.status["storageWarning"] = f"quota rejected: {exc}"
        logger.warning("quota rejected account=%s desired=%dGi: %s", account_id, desired, exc)
    except Exception as exc:
        # Backend blip — surface it; do NOT re-raise (an un-retriable error would make kopf
        # retry the handler forever). The next reconcile/edit can re-apply.
        patch.status["storageWarning"] = f"quota set failed: {exc}"
        patch.status["storageRetryAfter"] = time.time() + 60.0
        logger.error("quota set rejected account=%s: %s", account_id, exc)
