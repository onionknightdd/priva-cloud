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

import functools
import inspect
import threading
import time
from types import SimpleNamespace

import httpx
import kopf

from priva_common import drain_token
from priva_common.config import get_settings
from priva_common.network_isolation import isolation_intent_digest
from priva_common.tenant_lifecycle import AGENTTENANT_FINALIZER
from priva_operator import (
    GROUP,
    PLURAL,
    VERSION,
    egress_proxy,
    kube,
    names,
    storage_backend,
)

# Throttle the global managed-policy render so N per-account handlers don't each
# hit data-spine every tick. The render is digest-guarded (write only on change);
# this just bounds the read rate. Module-level: shared across all CR handlers.
_MANAGED_POLICY_MIN_INTERVAL = 15.0
_managed_policy_last_render = 0.0
# Same throttle for the tenant-isolation NetworkPolicies, which are likewise a
# single namespace-scoped object set read from data-spine by every CR handler.
_NETWORK_POLICY_MIN_INTERVAL = 15.0
_network_policy_last_render = 0.0
_network_isolation_cache = None
_network_isolation_last_attempt = 0.0
# Desired state and successfully-applied state are deliberately separate. A
# failed tighten may update the desired cache, but must never make the throttle
# or readiness path treat that unapplied snapshot as healthy.
_network_isolation_applied_intent: str | None = None
_network_isolation_dirty = True
# Kopf executes synchronous handlers in worker threads. Isolation objects are
# namespace-global, so selecting a snapshot and applying it must be one ordered
# operation: otherwise a sibling can read the old cache while a tightened
# snapshot is in flight and write that old policy after the new one.
_network_isolation_converge_lock = threading.Lock()
_runner_defaults_cache = None
_runner_defaults_last_attempt = 0.0
_RUNNER_DEFAULTS_MIN_INTERVAL = 15.0
_RUNNER_DRAIN_PHASES = frozenset({"Draining", "DrainingLegacy"})

# Teardown budget. kopf holds its finalizer until the delete handler stops asking for a
# retry, so an unbounded retry would wedge the CR in Terminating forever — and with it the
# account row the control plane only sweeps once the CR is gone. Bound the attempts and
# release with a loud log instead: a named leak is recoverable by hand, a stuck CR is not.
_PURGE_MAX_ATTEMPTS = 10
_PURGE_RETRY_BACKOFF = 30.0
_PURGE_POD_TERMINATION_TIMEOUT = 30.0
_purging: dict[str, str | None] = {}
_account_lifecycle_locks: dict[str, threading.RLock] = {}
_account_lifecycle_locks_guard = threading.Lock()


def _account_lifecycle_lock(account_id: str) -> threading.RLock:
    with _account_lifecycle_locks_guard:
        return _account_lifecycle_locks.setdefault(account_id, threading.RLock())


def _discard_stale_route_patch(patch) -> None:
    """Remove fields which could reopen routing after lifecycle state changed."""
    if patch is None or not hasattr(patch, "status"):
        return
    for key in ("phase", "podIP", "readyReplicas", "startedAt", "idleSince"):
        patch.status.pop(key, None)
    patch.status.pop("terminal", None)


def _serialize_account_lifecycle(fn):
    """Serialize all create/wake/scale/delete mutations for one account.

    Live deletion checks alone still have a check-then-scale gap. Holding one
    process-local lock means an in-flight wake finishes before purge scales it
    back down, or purge records `_purging` before a queued wake can proceed.
    """

    signature = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        bound = signature.bind_partial(*args, **kwargs)
        spec = bound.arguments.get("spec") or {}
        name = bound.arguments.get("name")
        account_id = spec.get("accountId") or name
        if not account_id:
            return fn(*args, **kwargs)
        with _account_lifecycle_lock(str(account_id)):
            result = fn(*args, **kwargs)
            # The API server can accept a desiredState/delete change while this
            # synchronous handler owns the process-local lock. Before Kopf
            # flushes its accumulated patch, re-authorize active-snapshot
            # handlers and strip any route-opening fields if the CR moved.
            if spec.get("desiredState", "active") == "active":
                namespace = bound.arguments.get("namespace")
                uid = bound.arguments.get("uid")
                patch = bound.arguments.get("patch")
                if namespace and kube.agenttenant_teardown_started(
                    namespace, str(account_id), uid
                ):
                    _discard_stale_route_patch(patch)
            return result

    return wrapped


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


def _network_isolation_locked(*, force: bool = False):
    """Last-known-good isolation snapshot, or fail closed before pod mutation.

    Caller must hold ``_network_isolation_converge_lock``.
    A cached snapshot is safe during a data-spine outage: the same proxy config,
    pod env and NetworkPolicies remain in force. With no snapshot there is no
    safe template to render, so callers must retry instead of creating an
    unpoliced tenant pod.
    """
    global _network_isolation_cache, _network_isolation_last_attempt
    global _network_isolation_applied_intent, _network_isolation_dirty
    now = time.monotonic()
    if not force and now - _network_isolation_last_attempt < _NETWORK_POLICY_MIN_INTERVAL:
        if _network_isolation_cache is None:
            raise RuntimeError("network isolation settings are not available")
        return _network_isolation_cache
    _network_isolation_last_attempt = now
    settings = get_settings()
    try:
        from priva_common.dataplane import get_client
        candidate = get_client().network_isolation.get()
        candidate_intent = isolation_intent_digest(candidate, settings)
        cached_intent = (
            isolation_intent_digest(_network_isolation_cache, settings)
            if _network_isolation_cache is not None
            else None
        )
        if (
            candidate_intent != cached_intent
            or candidate_intent != _network_isolation_applied_intent
        ):
            _network_isolation_dirty = True
        _network_isolation_cache = candidate
    except Exception as exc:
        if _network_isolation_cache is None:
            # An Operator restart loses the in-memory LKG while the already
            # applied boundary remains healthy. Recover only from the dedicated,
            # topology-bound snapshot and only after proving that the live CNI
            # fact, complete NetworkPolicy set and proxy generation still match.
            try:
                recovered = kube.load_isolation_snapshot(
                    settings.kubernetes.namespace_tenants,
                    settings,
                )
                recovered_ready = (
                    recovered is not None
                    and kube.isolation_snapshot_resources_ready(
                        settings.kubernetes.namespace_tenants,
                        recovered,
                        settings,
                    )
                )
            except Exception as recovery_exc:
                raise RuntimeError(
                    "network isolation settings unavailable and persisted "
                    "boundary validation failed; refusing tenant pod mutation"
                ) from recovery_exc
            if not recovered_ready:
                raise RuntimeError(
                    "network isolation settings unavailable and no verified "
                    "persisted boundary exists; refusing tenant pod mutation"
                ) from exc
            _network_isolation_cache = recovered
            _network_isolation_applied_intent = isolation_intent_digest(
                recovered,
                settings,
            )
            _network_isolation_dirty = False
    return _network_isolation_cache


def _network_isolation(*, force: bool = False):
    """Thread-safe snapshot access outside a complete isolation converge."""
    with _network_isolation_converge_lock:
        return _network_isolation_locked(force=force)


def _render_network_policies(namespace, logger, *, force=False):
    """Digest-guarded, throttled converge of the egress proxy + tenant policies.

    Fail-soft like the managed policy: a data-spine blip must not take the
    operator down. The cost of the soft failure differs though — a stale policy
    set stays in force, which is the safe direction (nothing re-opens).
    """
    global _network_policy_last_render
    global _network_isolation_applied_intent, _network_isolation_dirty
    with _network_isolation_converge_lock:
        # Re-evaluate both snapshot and throttle after acquiring the lock. A
        # sibling which waited behind a successful force converge must observe
        # its new cache/last-render, not apply the cache it saw beforehand.
        iso = _network_isolation_locked(force=force)
        settings = get_settings()
        expected_intent = isolation_intent_digest(iso, settings)
        now = time.monotonic()
        if (
            not force
            and not _network_isolation_dirty
            and _network_isolation_applied_intent == expected_intent
            and (now - _network_policy_last_render)
            < _NETWORK_POLICY_MIN_INTERVAL
        ):
            return iso
        try:
            kube.ensure_isolation(
                namespace, strict=True, iso=iso, settings=settings)
            kube.persist_isolation_snapshot(namespace, iso, settings)
        except Exception:
            _network_isolation_dirty = True
            raise
        # An error or partial converge remains immediately retryable.
        _network_isolation_applied_intent = expected_intent
        _network_isolation_dirty = False
        _network_policy_last_render = time.monotonic()
        return iso


def _workload_isolation(namespace, logger, *, force=False, wait=False):
    """Converge one snapshot and require the proxy before a pod can start."""
    settings = get_settings()
    iso = _render_network_policies(namespace, logger, force=force)
    expected_intent = isolation_intent_digest(iso, settings)
    expected_config_sha = egress_proxy.config_sha256(
        egress_proxy.render_squid_conf(iso, settings)
    )
    if (
        _network_isolation_dirty
        or _network_isolation_applied_intent != expected_intent
    ):
        raise kopf.TemporaryError(
            "tenant network isolation generation is not fully applied",
            delay=5,
        )
    if not kube.network_policy_enforced(namespace, settings):
        raise kopf.TemporaryError(
            "CNI ingress+egress enforcement is not verified; refusing tenant pod mutation",
            delay=30,
        )
    timeout = float(settings.kubernetes.wake_timeout_seconds) if wait else 0.0
    ready = (
        kube.wait_egress_proxy_ready(
            namespace,
            timeout=timeout,
            expected_intent=expected_intent,
            expected_config_sha=expected_config_sha,
            settings=settings,
            require_all_replicas=False,
        )
        if wait
        else kube.egress_proxy_ready(
            namespace,
            expected_intent=expected_intent,
            expected_config_sha=expected_config_sha,
            settings=settings,
            require_all_replicas=False,
        )
    )
    if not ready:
        raise kopf.TemporaryError(
            "egress proxy is not Ready; refusing to create or wake a tenant pod",
            delay=5,
        )
    return iso


@kopf.on.startup()
def configure_operator_persistence(settings: kopf.OperatorSettings, **_):
    """Pin the marker Control Panel pre-installs on every AgentTenant."""
    settings.persistence.finalizer = AGENTTENANT_FINALIZER


@kopf.on.startup()
def bootstrap_network_policies(logger, **_):
    """Converge the egress proxy + tenant NetworkPolicies once at operator boot.

    Startup records the failure but stays alive so timer handlers can quiesce
    workloads left running by a previous process. Every create/wake path has its
    own fail-closed gate.
    """
    namespace = _tenants_namespace()
    settings = get_settings()
    # Startup/resume events can overlap. Use the same serialized force converge
    # as create/wake instead of splitting the snapshot read from its writes.
    try:
        iso = _render_network_policies(namespace, logger, force=True)
    except Exception:
        logger.error(
            "tenant isolation bootstrap failed; create/wake remains blocked and "
            "running tenant workloads will be quiesced",
            exc_info=True,
        )
        return
    if not kube.network_policy_enforced(namespace, settings):
        logger.error(
            "CNI ingress+egress enforcement probe has not passed; "
            "tenant create/wake is blocked; run deploy/checks/networkpolicy-cni.sh"
        )
        return
    expected_intent = isolation_intent_digest(iso, settings)
    expected_config_sha = egress_proxy.config_sha256(
        egress_proxy.render_squid_conf(iso, settings)
    )
    if not kube.wait_egress_proxy_ready(
        namespace,
        timeout=float(settings.kubernetes.wake_timeout_seconds),
        expected_intent=expected_intent,
        expected_config_sha=expected_config_sha,
        settings=settings,
        require_all_replicas=False,
    ):
        logger.error(
            "egress proxy did not become Ready during operator startup; "
            "tenant create/wake remains blocked"
        )


@kopf.on.startup()
def bootstrap_managed_policy(logger, **_):
    """Create the managed-policy ConfigMap once at operator boot, before any pod
    mounts it — so a cold-started account wakes with the current enforced set.

    The runner mounts this ConfigMap with optional:False, so if it is missing no
    runner pod can start. Log that consequence explicitly rather than leaving a
    generic warning: a failure here is now an outage, not a degraded mode.
    """
    namespace = _tenants_namespace()
    try:
        kube.ensure_managed_policy_configmap(namespace, strict=True)
    except Exception:
        logger.error(
            "managed-policy bootstrap FAILED — %s is absent, so runner pods cannot "
            "start (the policy mount is required, by design). Check data-spine.",
            kube.MANAGED_POLICY_CM, exc_info=True,
        )
        raise
    global _managed_policy_last_render
    _managed_policy_last_render = time.monotonic()


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


def _live_teardown_started(
    account_id: str,
    namespace: str,
    uid: str | None,
    meta=None,
) -> bool:
    """Live fail-closed authorization before workload mutation.

    In addition to process-local purge/deletion state, the Kubernetes guard
    requires the same CR UID and live ``spec.desiredState=active``.
    """
    if _teardown_started(account_id, meta):
        return True
    return kube.agenttenant_teardown_started(namespace, account_id, uid)


def _quiesce_if_inactive(spec, name, namespace, status, patch, logger) -> bool:
    """Enforce lifecycle shutdown without depending on identity or data-spine config."""
    desired_state = spec.get("desiredState", "active")
    if desired_state == "active":
        return False
    account_id = spec.get("accountId") or name
    phase = "Purged" if desired_state == "purge" else "Offboarding"
    replicas = kube.get_replicas(namespace, account_id)
    terminal_replicas = kube.get_terminal_replicas(namespace, account_id)
    terminal_status = {
        **(status.get("terminal") or {}), "phase": phase, "podIP": None,
        "readyReplicas": 0, "activeSessions": 0,
    }
    if replicas > 0 or terminal_replicas > 0:
        # One status write closes every official routing source before either
        # Deployment is touched. Then close admission inside the concrete Pods
        # so a request which already resolved an endpoint cannot enter late.
        kube.set_cr_status(
            namespace,
            account_id,
            phase=phase,
            podIP=None,
            readyReplicas=0,
            terminal=terminal_status,
        )
        _force_close_account_admission(
            namespace, account_id, get_settings(), logger
        )
    if replicas > 0:
        kube.scale(namespace, account_id, 0)
    if terminal_replicas > 0:
        kube.scale_terminal(namespace, account_id, 0)
    if status.get("terminal") != terminal_status:
        patch.status["terminal"] = terminal_status
    if (status.get("phase") != phase or status.get("podIP") is not None
            or int(status.get("readyReplicas") or 0) != 0):
        patch.status["phase"] = phase
        patch.status["podIP"] = None
        patch.status["readyReplicas"] = 0
    return True


def _quiesce_for_network_failure(
    *,
    namespace: str,
    account_id: str,
    status: dict,
    patch,
    logger,
    reason: str,
) -> None:
    """Close routing and stop both tenant workloads when isolation is unverified.

    The direct status write precedes admission fencing and scaling so the EPP no
    longer resolves a Pod even if either later operation encounters a transient
    API/network failure. Each mutation is retried by the ten-second timer.
    """
    phase = "IsolationBlocked"
    terminal_status = {
        **(status.get("terminal") or {}),
        "phase": phase,
        "podIP": None,
        "readyReplicas": 0,
        "activeSessions": 0,
    }
    _upsert_condition(
        status,
        patch,
        "NetworkIsolationReady",
        False,
        "BoundaryUnverified",
        reason,
    )
    patch.status["phase"] = phase
    patch.status["podIP"] = None
    patch.status["readyReplicas"] = 0
    patch.status["terminal"] = terminal_status

    try:
        kube.set_cr_status(
            namespace,
            account_id,
            phase=phase,
            podIP=None,
            readyReplicas=0,
            terminal=terminal_status,
        )
    except Exception:
        logger.error(
            "failed to persist isolation route gate account=%s",
            account_id,
            exc_info=True,
        )

    try:
        _force_close_account_admission(
            namespace, account_id, get_settings(), logger
        )
    except Exception:
        logger.warning(
            "failed to close in-pod admission during isolation failure account=%s",
            account_id,
            exc_info=True,
        )

    try:
        if kube.get_replicas(namespace, account_id) > 0:
            kube.scale(namespace, account_id, 0)
    except Exception:
        logger.error(
            "failed to scale Runner after isolation failure account=%s",
            account_id,
            exc_info=True,
        )
    try:
        if kube.get_terminal_replicas(namespace, account_id) > 0:
            kube.scale_terminal(namespace, account_id, 0)
    except Exception:
        logger.error(
            "failed to scale Terminal after isolation failure account=%s",
            account_id,
            exc_info=True,
        )


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


def _drain_headers(capability: str | None) -> dict[str, str] | None:
    """Return only the Pod-local drain capability.

    A control-plane service token is accepted by data-spine and must never cross
    into a tenant workload.  Pods without the capability are legacy/invalid and
    therefore cannot be drained in-process; callers keep the existing fail-closed
    route gate and scale-down behavior instead of falling back to that credential.
    """
    if not capability:
        return None
    return {drain_token.HEADER: capability}


def _begin_terminal_drain(
    pod_ip: str,
    port: int,
    revision: int | None,
    logger,
    *,
    force: bool = False,
    capability: str | None = None,
) -> bool:
    """Close Terminal admission, using revision CAS unless teardown is forced."""
    params = {"force": "true"} if force else {"revision": int(revision)}
    headers = _drain_headers(capability)
    if headers is None:
        logger.warning("terminal drain unavailable pod=%s: missing per-Pod capability", pod_ip)
        return False
    try:
        with httpx.Client(timeout=2.0, trust_env=False) as cx:
            response = cx.post(
                f"http://{pod_ip}:{port}/internal/drain",
                params=params,
                headers=headers,
            )
    except Exception as exc:
        logger.debug("terminal drain request failed pod=%s: %s", pod_ip, exc)
        return False
    if response.status_code == 200:
        return True
    if response.status_code not in (404, 409):
        logger.warning("terminal drain rejected pod=%s status=%s", pod_ip, response.status_code)
    return False


def _begin_runner_drain(
    pod_ip: str,
    port: int,
    revision: int | None,
    logger,
    *,
    force: bool = False,
    capability: str | None = None,
) -> int | None:
    """Close Runner admission, using revision CAS unless teardown is forced."""
    params = {"force": "true"} if force else {"revision": int(revision)}
    headers = _drain_headers(capability)
    if headers is None:
        logger.warning("runner drain unavailable pod=%s: missing per-Pod capability", pod_ip)
        return None
    try:
        with httpx.Client(timeout=2.0, trust_env=False) as cx:
            response = cx.post(
                f"http://{pod_ip}:{port}/internal/drain",
                params=params,
                headers=headers,
            )
    except Exception as exc:
        logger.debug("runner drain request failed pod=%s: %s", pod_ip, exc)
        return None
    if response.status_code == 200:
        try:
            return int(response.json()["activity_revision"])
        except (KeyError, TypeError, ValueError):
            logger.warning("runner drain returned an invalid revision pod=%s", pod_ip)
            return None
    if response.status_code not in (404, 409):
        logger.warning(
            "runner drain rejected pod=%s status=%s",
            pod_ip,
            response.status_code,
        )
    return None


def _force_close_runner_admission(
    namespace: str,
    account_id: str,
    settings,
    logger,
) -> None:
    runner_ip = kube.current_ready_pod_ip(namespace, account_id)
    if runner_ip:
        if _begin_runner_drain(
            runner_ip,
            int(settings.kubernetes.runner_service_port),
            None,
            logger,
            force=True,
            capability=kube.applied_runner_drain_token(namespace, account_id),
        ) is None:
            logger.warning(
                "forced Runner admission fence was unavailable account=%s pod=%s",
                account_id,
                runner_ip,
            )


def _force_close_terminal_admission(
    namespace: str,
    account_id: str,
    settings,
    logger,
) -> None:
    terminal_ip = kube.current_ready_terminal_pod_ip(namespace, account_id)
    if terminal_ip and not _begin_terminal_drain(
        terminal_ip,
        int(settings.kubernetes.terminal_service_port),
        None,
        logger,
        force=True,
        capability=kube.applied_terminal_drain_token(namespace, account_id),
    ):
        logger.warning(
            "forced Terminal admission fence was unavailable account=%s pod=%s",
            account_id,
            terminal_ip,
        )


def _force_close_account_admission(
    namespace: str,
    account_id: str,
    settings,
    logger,
) -> None:
    """Best-effort in-Pod fences after the durable CR route gate is closed.

    New images accept the per-Pod capability even across signing-key rotation.
    Legacy images may reject or lack the endpoint; scale-to-zero still follows
    immediately and the CR gate prevents any new official route resolution.
    """
    _force_close_runner_admission(namespace, account_id, settings, logger)
    _force_close_terminal_admission(namespace, account_id, settings, logger)


def _runner_drain_ready(
    *,
    namespace: str,
    account_id: str,
    pod_ip: str,
    port: int,
    health: dict,
    status: dict,
    patch,
    logger,
) -> bool:
    """Close admission before scale.

    Images without ``activity_revision`` have no atomic admission fence. They
    remain routable and running until explicitly upgraded; a status-only
    two-tick bridge cannot close the race for a request which already resolved
    the Pod but has not entered its middleware yet.
    """
    revision = health.get("activity_revision")
    if revision is None:
        if status.get("phase") == "Draining":
            # A prior atomic close is durable for that process. A malformed or
            # transiently old health response must not reopen routing to it;
            # keep the gate and retry until the pod either reports a revision
            # again or disappears after scale-to-zero.
            patch.status["phase"] = "Draining"
            patch.status["podIP"] = pod_ip
            patch.status["readyReplicas"] = 1
            return False
        _upsert_condition(
            status,
            patch,
            "DrainUnsupported",
            True,
            "UpgradeRequired",
            "Runner image has no atomic drain endpoint; automatic scale-down is disabled",
        )
        # Heal objects left in the old two-tick bridge state. Keeping them
        # DrainingLegacy would make the still-running Pod permanently
        # unroutable, while scaling it would reintroduce the original TOCTOU.
        if status.get("phase") == "DrainingLegacy":
            kube.set_cr_status(
                namespace,
                account_id,
                phase="Running",
                podIP=pod_ip,
                readyReplicas=1,
            )
        patch.status["phase"] = "Running"
        patch.status["podIP"] = pod_ip
        patch.status["readyReplicas"] = 1
        return False

    _upsert_condition(
        status,
        patch,
        "DrainUnsupported",
        False,
        "Supported",
        "Runner image exposes the atomic revision drain endpoint",
    )
    if int(health.get("active_runs", 1)) != 0:
        return False

    drained_revision = _begin_runner_drain(
        pod_ip,
        port,
        int(revision),
        logger,
        capability=kube.applied_runner_drain_token(namespace, account_id),
    )
    if drained_revision is None:
        return False
    # Persist the route gate after the in-pod admission gate has closed.
    kube.set_cr_status(
        namespace,
        account_id,
        phase="Draining",
        podIP=pod_ip,
        readyReplicas=1,
    )
    patch.status["phase"] = "Draining"
    patch.status["podIP"] = pod_ip
    patch.status["readyReplicas"] = 1

    # Reconfirm after the status write. This is idempotent for the same process
    # and also protects against a container restart between the first close and
    # the Kubernetes mutation.
    return _begin_runner_drain(
        pod_ip,
        port,
        drained_revision,
        logger,
        capability=kube.applied_runner_drain_token(namespace, account_id),
    ) is not None


@kopf.on.create(GROUP, VERSION, PLURAL)
@kopf.on.resume(GROUP, VERSION, PLURAL)
@_serialize_account_lifecycle
def ensure(spec, name, namespace, uid, status, patch, logger, meta=None, **_):
    s = get_settings()
    if _teardown_started(spec.get("accountId") or name, meta):
        return
    if _quiesce_if_inactive(spec, name, namespace, status, patch, logger):
        return
    identity = _ids_or_reject(spec, name, status, patch, logger)
    if identity is None:
        return
    account_id, username = identity
    if _live_teardown_started(account_id, namespace, uid, meta):
        return
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
    # Isolation must be in force before the pod exists, not after — a runner that
    # comes up into an unpoliced namespace is unprotected for the whole window.
    isolation = _workload_isolation(
        namespace, logger, force=True, wait=True)
    if _live_teardown_started(account_id, namespace, uid, meta):
        return
    kube.ensure_runtime_objects(
        namespace, account_id, username, image, s.kubernetes.runner_image_pull_policy, s, owner, spec,
        defaults, isolation)
    if _live_teardown_started(account_id, namespace, uid, meta):
        return

    terminal_percent = kube.resolve_terminal_percent(s, defaults)
    if terminal_percent > 0:
        if _live_teardown_started(account_id, namespace, uid, meta):
            return
        kube.ensure_terminal_objects(
            namespace, account_id, username, image, s.kubernetes.runner_image_pull_policy,
            s, owner, spec, defaults, isolation)
        if _live_teardown_started(account_id, namespace, uid, meta):
            return
        if kube.get_terminal_replicas(namespace, account_id) <= 0:
            patch.status["terminal"] = {
                **(status.get("terminal") or {}),
                "phase": "Zero",
                "readyReplicas": 0,
                "resourcePercent": terminal_percent,
            }
    else:
        terminal_replicas = kube.get_terminal_replicas(namespace, account_id)
        disabled_terminal = {
            **(status.get("terminal") or {}),
            "phase": "Disabled",
            "podIP": None,
            "readyReplicas": 0,
            "activeSessions": 0,
            "resourcePercent": 0,
        }
        if terminal_replicas > 0:
            kube.set_cr_status(
                namespace, account_id, terminal=disabled_terminal
            )
            _force_close_terminal_admission(
                namespace, account_id, s, logger
            )
            kube.scale_terminal(namespace, account_id, 0)
        patch.status["terminal"] = disabled_terminal

    replicas = kube.get_replicas(namespace, account_id)

    # Persistent runners are always-on: bring them to 1 here (the reconcile-to-desired
    # home that runs on create AND resume, so it self-heals across operator restarts).
    # Guard on desiredState so an offboarding/purge account is never force-woken.
    if _is_persistent(spec) and spec.get("desiredState", "active") == "active":
        if _live_teardown_started(account_id, namespace, uid, meta):
            return
        if replicas < 0:
            patch.status["phase"] = "PendingTerminalDrain"
            patch.status["podIP"] = None
            patch.status["readyReplicas"] = 0
            logger.warning("persistent Runner create deferred by live Terminal account=%s", account_id)
            return
        if replicas != 1:
            # ensure_runtime_objects above already converged the full template to the
            # current effective config while at 0 — just scale up.
            if _live_teardown_started(account_id, namespace, uid, meta):
                return
            kube.scale(namespace, account_id, 1)
            if _live_teardown_started(account_id, namespace, uid, meta):
                return
        if _live_teardown_started(account_id, namespace, uid, meta):
            return
        pod_ip = kube.wait_pod_ready(namespace, account_id, timeout=float(s.kubernetes.wake_timeout_seconds))
        if _live_teardown_started(account_id, namespace, uid, meta):
            return
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
@_serialize_account_lifecycle
def purge(spec, name, namespace, logger, uid=None, retry=0, status=None, **_):
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
    status = status or {}
    try:
        # Close every routing source before touching replicas. This also makes
        # an accidental out-of-band CR deletion fail closed while its old pods
        # are being collected.
        terminal_status = {
            **(status.get("terminal") or {}),
            "phase": "Draining",
            "podIP": None,
            "readyReplicas": 0,
            "activeSessions": 0,
        }
        kube.set_cr_status(
            namespace,
            account_id,
            phase="Draining",
            podIP=None,
            readyReplicas=0,
            terminal=terminal_status,
        )
        _force_close_account_admission(namespace, account_id, s, logger)
        if kube.get_replicas(namespace, account_id) > 0:
            kube.scale(namespace, account_id, 0)
        if kube.get_terminal_replicas(namespace, account_id) > 0:
            kube.scale_terminal(namespace, account_id, 0)
        # Deployment.spec.replicas is desired state, not proof that the old
        # processes have stopped. Include NotReady and Terminating Pods: either
        # may still have the account volume mounted and be flushing writes.
        if not kube.wait_account_workload_pods_gone(
            namespace,
            account_id,
            timeout=_PURGE_POD_TERMINATION_TIMEOUT,
        ):
            raise RuntimeError(
                f"account workload Pods did not terminate before purge: {account_id}"
            )
        if spec.get("desiredState") != "purge":
            # Owner-referenced runtime objects may be removed, but storage is
            # account data and must survive an accidental `kubectl delete` of
            # an otherwise active/offboarding CR. Control Panel will recreate
            # the CR with a new UID and attach the existing volume.
            logger.error(
                "AgentTenant deleted without desiredState=purge; preserved storage "
                "account=%s",
                account_id,
            )
            return
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
@_serialize_account_lifecycle
def on_wake(spec, name, namespace, uid, status, patch, logger, meta=None, **_):
    s = get_settings()
    if _teardown_started(spec.get("accountId") or name, meta):
        return
    if _quiesce_if_inactive(spec, name, namespace, status, patch, logger):
        return
    if status.get("phase") in _RUNNER_DRAIN_PHASES:
        return
    identity = _ids_or_reject(spec, name, status, patch, logger)
    if identity is None:
        return
    account_id, username = identity
    if _live_teardown_started(account_id, namespace, uid, meta):
        return
    if kube.current_cr_phase(namespace, account_id) in _RUNNER_DRAIN_PHASES:
        return
    replicas = kube.get_replicas(namespace, account_id)
    # A warm pod is still subject to the live network boundary. Without this
    # check, a stale wake event could overwrite IsolationBlocked with Running
    # after the timer had closed routing.
    isolation = _workload_isolation(
        namespace, logger, wait=replicas != 1
    )
    # Reality-based guard (#1/#4): when the Deployment is already scaled to 1, don't
    # re-scale — resolve the *real* Ready pod IP and write it. Trusting status.podIP
    # here would re-bless a dead/replaced pod; resolving from
    # pod reality makes the wake path itself self-correcting, so correctness no longer
    # depends on the timer cadence (the timer only shrinks the EPP warm-path stale window).
    if replicas == 1:
        pod_ip = kube.current_ready_pod_ip(namespace, account_id) or kube.wait_pod_ready(
            namespace, account_id, timeout=float(s.kubernetes.wake_timeout_seconds))
        if pod_ip is None:
            patch.status["phase"] = "Waking"  # replacement gap; next wake/timer retries
            logger.warning("wake: replicas==1 but no Ready pod account=%s", account_id)
            return
        if pod_ip != status.get("podIP"):
            # A changed IP means a replacement pod — give it its own min_alive window.
            patch.status["startedAt"] = time.time()
        # The handler's `status` argument may predate an atomic drain written by
        # a concurrent timer. Never re-bless that pod from a stale wake event.
        if (
            _live_teardown_started(account_id, namespace, uid, meta)
            or kube.current_cr_phase(namespace, account_id) in _RUNNER_DRAIN_PHASES
        ):
            return
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
    if _live_teardown_started(account_id, namespace, uid, meta):
        return
    kube.ensure_runtime_objects(
        namespace, account_id, username, kube.resolve_image(spec, s),
        s.kubernetes.runner_image_pull_policy, s, names.owner_ref(name, uid), spec, defaults,
        isolation)
    if _live_teardown_started(account_id, namespace, uid, meta):
        return
    if kube.get_replicas(namespace, account_id) < 0:
        patch.status["phase"] = "PendingTerminalDrain"
        patch.status["podIP"] = None
        patch.status["readyReplicas"] = 0
        return
    if _live_teardown_started(account_id, namespace, uid, meta):
        return
    kube.scale(namespace, account_id, 1)
    if _live_teardown_started(account_id, namespace, uid, meta):
        return
    patch.status["phase"] = "Waking"
    pod_ip = kube.wait_pod_ready(namespace, account_id, timeout=float(s.kubernetes.wake_timeout_seconds))
    if _live_teardown_started(account_id, namespace, uid, meta):
        return
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
@_serialize_account_lifecycle
def on_terminal_wake(spec, name, namespace, uid, status, patch, logger, meta=None, **_):
    """Wake only the independent Terminal Deployment. Runner state is untouched."""
    s = get_settings()
    if _teardown_started(spec.get("accountId") or name, meta):
        return
    if _quiesce_if_inactive(spec, name, namespace, status, patch, logger):
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
    if current.get("phase") in _RUNNER_DRAIN_PHASES:
        return

    def drain_started() -> bool:
        return (
            kube.current_cr_terminal_phase(namespace, account_id)
            in _RUNNER_DRAIN_PHASES
        )

    def blocked() -> bool:
        return (
            _live_teardown_started(account_id, namespace, uid, meta)
            or drain_started()
        )

    # The handler snapshot may predate the timer's direct Draining write.
    if blocked():
        return
    if percent <= 0:
        disabled = {
            **current, "phase": "Disabled", "podIP": None, "readyReplicas": 0,
            "activeSessions": 0, "resourcePercent": 0,
        }
        if kube.get_terminal_replicas(namespace, account_id) > 0:
            kube.set_cr_status(namespace, account_id, terminal=disabled)
            _force_close_terminal_admission(
                namespace, account_id, s, logger
            )
            kube.scale_terminal(namespace, account_id, 0)
        patch.status["terminal"] = disabled
        return

    # Never let a newly-enabled Terminal overlap a still-running Runner that has
    # the old, unsplit resource template. Dormant runners are converged without a
    # restart; active runners keep running and Terminal reports pending until their
    # next zero-to-one convergence (for example, Admin shutdown followed by the
    # next request).  We intentionally do not mutate a live Runner's cgroup budget.
    runner_replicas = kube.get_replicas(namespace, account_id)
    terminal_replicas = kube.get_terminal_replicas(namespace, account_id)
    isolation = _workload_isolation(
        namespace, logger, wait=terminal_replicas != 1
    )
    applied_percent = None
    applied_generation = kube.applied_allocation_hash(namespace, account_id)
    if runner_replicas == 0 and terminal_replicas <= 0:
        if blocked():
            return
        kube.ensure_runtime_objects(
            namespace, account_id, username, kube.resolve_image(spec, s),
            s.kubernetes.runner_image_pull_policy, s, names.owner_ref(name, uid), spec, defaults,
            isolation)
        if blocked():
            return
        applied_generation = kube.applied_allocation_hash(namespace, account_id)
    if runner_replicas > 0:
        applied_percent = kube.applied_terminal_percent(namespace, account_id)
    if terminal_replicas <= 0 and runner_replicas > 0 and applied_generation != desired_generation:
        if blocked():
            return
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
        if blocked():
            return
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
    if blocked():
        return
    kube.ensure_terminal_objects(
        namespace, account_id, username, image, s.kubernetes.runner_image_pull_policy,
        s, names.owner_ref(name, uid), spec, defaults, isolation)
    if blocked():
        return
    kube.scale_terminal(namespace, account_id, 1)
    if blocked():
        return
    pod_ip = kube.wait_terminal_pod_ready(
        namespace, account_id, timeout=float(s.kubernetes.wake_timeout_seconds))
    if blocked():
        # Nothing from this stale wake may overwrite the timer's direct
        # Draining status when Kopf flushes the handler patch.
        patch.status.pop("terminal", None)
        return
    if pod_ip is None:
        patch.status["terminal"] = {
            **current, "phase": "Waking", "podIP": None, "readyReplicas": 0,
            "resourcePercent": percent, "allocationHash": desired_generation,
        }
        logger.warning("terminal wake timed out account=%s", account_id)
        return
    patch.status["terminal"] = {
        **current, "phase": "Running", "podIP": pod_ip, "readyReplicas": 1,
        "resourcePercent": percent, "allocationHash": desired_generation,
        "startedAt": time.time(), "activeSessions": 0,
    }
    logger.info("woke terminal account=%s pod=%s", account_id, pod_ip)


@kopf.timer(GROUP, VERSION, PLURAL, interval=10.0, sharp=False)
@_serialize_account_lifecycle
def reconcile_terminal(spec, name, namespace, uid, status, patch, logger, meta=None, **_):
    """Derive Terminal status and scale 1->0 after its last session plus grace."""
    s = get_settings()
    if _teardown_started(spec.get("accountId") or name, meta):
        return
    if _quiesce_if_inactive(spec, name, namespace, status, patch, logger):
        return
    identity = _ids_or_reject(spec, name, status, patch, logger)
    if identity is None:
        return
    account_id, username = identity
    if _live_teardown_started(account_id, namespace, uid, meta):
        return
    current = status.get("terminal") or {}
    replicas = kube.get_terminal_replicas(namespace, account_id)

    # A direct Draining write survives an Operator crash even when the trailing
    # Kopf patch to Zero does not. Recover from Deployment/Pod reality without
    # depending on data-spine/defaults, but keep the route gate until the old
    # process (including a Terminating Pod) is actually gone.
    if replicas <= 0 and current.get("phase") in _RUNNER_DRAIN_PHASES:
        if not kube.workload_pods_gone(namespace, account_id, "terminal"):
            return
        zero = {
            **current,
            "phase": "Zero",
            "podIP": None,
            "readyReplicas": 0,
            "activeSessions": 0,
        }
        kube.set_cr_status(namespace, account_id, terminal=zero)
        patch.status["terminal"] = zero
        return

    defaults = _defaults_or_reject(spec, status, patch, logger)
    if defaults is None:
        return
    percent = kube.resolve_terminal_percent(s, defaults)
    desired_generation = kube.allocation_hash(spec, s, defaults, username)
    desired_template_generation = kube.terminal_template_hash(
        spec,
        s,
        defaults,
        username,
    )
    if status.get("desiredAllocationHash") != desired_generation:
        patch.status["desiredAllocationHash"] = desired_generation

    if percent <= 0:
        disabled = {
            **current, "phase": "Disabled", "podIP": None, "readyReplicas": 0,
            "activeSessions": 0, "resourcePercent": 0,
        }
        if replicas > 0:
            kube.set_cr_status(namespace, account_id, terminal=disabled)
            _force_close_terminal_admission(
                namespace, account_id, s, logger
            )
            kube.scale_terminal(namespace, account_id, 0)
        if current != disabled:
            patch.status["terminal"] = disabled
        return

    verified_isolation = None
    if replicas > 0:
        try:
            verified_isolation = _render_network_policies(namespace, logger)
            expected_intent = isolation_intent_digest(verified_isolation, s)
            expected_config_sha = egress_proxy.config_sha256(
                egress_proxy.render_squid_conf(verified_isolation, s)
            )
            if (
                _network_isolation_dirty
                or _network_isolation_applied_intent != expected_intent
                or not kube.network_policy_enforced(namespace, s)
                or not kube.egress_proxy_ready(
                    namespace,
                    expected_intent=expected_intent,
                    expected_config_sha=expected_config_sha,
                    settings=s,
                    require_all_replicas=False,
                )
            ):
                raise RuntimeError("tenant network boundary is not verified")
        except Exception as exc:
            logger.error(
                "tenant network boundary failed during Terminal reconcile "
                "account=%s: %s",
                account_id,
                exc,
            )
            _quiesce_for_network_failure(
                namespace=namespace,
                account_id=account_id,
                status=status,
                patch=patch,
                logger=logger,
                reason=str(exc) or type(exc).__name__,
            )
            return

    runner_replicas = kube.get_replicas(namespace, account_id)
    applied_percent = kube.applied_terminal_percent(namespace, account_id)
    applied_generation = kube.applied_allocation_hash(namespace, account_id)
    applied_template_generation = kube.applied_terminal_template_hash(
        namespace, account_id)
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
        if _live_teardown_started(account_id, namespace, uid, meta):
            return
        isolation = _workload_isolation(namespace, logger)
        if _live_teardown_started(account_id, namespace, uid, meta):
            return
        kube.ensure_terminal_objects(
            namespace, account_id, username, kube.resolve_image(spec, s),
            s.kubernetes.runner_image_pull_policy, s, names.owner_ref(name, uid), spec, defaults,
            isolation)
        if _live_teardown_started(account_id, namespace, uid, meta):
            return
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
        phase = (
            current.get("phase")
            if current.get("phase") in _RUNNER_DRAIN_PHASES
            else "Waking"
        )
        patch.status["terminal"] = {
            **current, "phase": phase, "podIP": None, "readyReplicas": 0,
            "resourcePercent": running_percent, "allocationHash": applied_generation,
        }
        return

    drain_capability = kube.applied_terminal_drain_token(
        namespace, account_id
    )
    health_headers = _drain_headers(drain_capability)
    if health_headers is None:
        logger.warning(
            "terminal health unavailable account=%s: missing per-Pod capability",
            account_id,
        )
        return
    try:
        port = s.kubernetes.terminal_service_port
        health = httpx.get(
            f"http://{real_ip}:{port}/health",
            timeout=2.0,
            trust_env=False,
            headers=health_headers,
        ).json()
    except Exception as exc:
        logger.debug("terminal health probe failed account=%s: %s", account_id, exc)
        return
    active = int(health.get("active_sessions", 0))
    last_activity = float(health.get("last_activity_ts", time.time()))
    session_revision = health.get("session_revision")
    now = time.time()
    drain_in_progress = current.get("phase") == "Draining"
    isolation = verified_isolation or _network_isolation()
    egress_stale = (
        kube.applied_terminal_egress_generation(namespace, account_id)
        != kube.egress_generation(isolation, s)
    )
    template_stale = applied_template_generation != desired_template_generation
    _upsert_condition(
        status,
        patch,
        "TerminalEgressReady",
        not egress_stale,
        "Applied" if not egress_stale else "PendingTerminalRestart",
        "Terminal proxy environment matches the active egress generation"
        if not egress_stale
        else "Terminal proxy environment applies after active sessions finish",
    )
    _upsert_condition(
        status,
        patch,
        "TerminalTemplateReady",
        not template_stale,
        "Applied" if not template_stale else "PendingTerminalRestart",
        "Terminal template generation matches"
        if not template_stale
        else "Terminal image, policy, allocation, or verification key applies on restart",
    )
    # Remove legacy diagnostic fields when a replacement image supports the
    # atomic endpoint; spreading ``current`` without this would preserve stale
    # UpgradeRequired UI forever.
    status_base = {
        key: value
        for key, value in current.items()
        if key not in ("drainCondition", "drainReason")
    }
    desired_status = {
        **status_base,
        "phase": ("Draining" if drain_in_progress else "Running"),
        "podIP": real_ip, "readyReplicas": 1,
        "activeSessions": active, "lastActivityTs": last_activity,
        "resourcePercent": running_percent, "allocationHash": applied_generation,
    }

    if session_revision is None:
        _upsert_condition(
            status,
            patch,
            "TerminalDrainUnsupported",
            True,
            "UpgradeRequired",
            "terminald image has no atomic drain endpoint; automatic scale-down is disabled",
        )
        # A supported drain which temporarily returns malformed health remains
        # fail-closed. A genuine legacy image stays routable Running and is never
        # auto-scaled; heal any old DrainingLegacy bridge left by the prior code.
        if not drain_in_progress:
            desired_status["phase"] = "Running"
        desired_status["drainCondition"] = "DrainUnsupported"
        desired_status["drainReason"] = "UpgradeRequired"
        if current.get("phase") == "DrainingLegacy":
            kube.set_cr_status(namespace, account_id, terminal=desired_status)
        patch.status["terminal"] = desired_status
        return

    _upsert_condition(
        status,
        patch,
        "TerminalDrainUnsupported",
        False,
        "Supported",
        "terminald exposes the atomic revision drain endpoint",
    )
    grace = int(getattr(defaults, "terminal_scale_down_grace_seconds",
                        s.kubernetes.terminal_scale_down_grace_seconds))
    if active == 0 and (
            egress_stale
            or template_stale
            or drain_in_progress
            or now - last_activity >= grace):
        # terminald performs the revision check and flips its reservation gate under
        # one mutex. A WebSocket accepted after our health read makes this return 409;
        # older terminald images never enter this branch.
        if not _begin_terminal_drain(
                real_ip, port, int(session_revision), logger,
                capability=drain_capability):
            return
        draining = {
            **desired_status, "phase": "Draining", "activeSessions": 0,
        }
        kube.set_cr_status(namespace, account_id, terminal=draining)
        patch.status["terminal"] = draining
        # Reconfirm after the direct status write. This is idempotent for the
        # same process and protects against a container restart in the gap.
        if not _begin_terminal_drain(
                real_ip, port, int(session_revision), logger,
                capability=drain_capability):
            return
        kube.scale_terminal(namespace, account_id, 0)
        # Do not expose Zero (and therefore permit a new wake) until the old Pod
        # has physically disappeared. A rapid 0→1 desired-state flip can cancel
        # deletion before the Deployment controller acts, leaving this
        # permanently drained process in place.
        patch.status["terminal"] = {
            **draining,
            "idleSince": last_activity,
        }
        logger.info(
            "slept idle terminal account=%s%s",
            account_id,
            " for template/egress migration"
            if (template_stale or egress_stale)
            else "",
        )
        return
    patch.status["terminal"] = desired_status


@kopf.timer(GROUP, VERSION, PLURAL, interval=10.0, sharp=False)
@_serialize_account_lifecycle
def reconcile_runtime(spec, name, namespace, status, patch, logger, uid=None, meta=None, **_):
    """Periodic reconcile — status is *derived, not authoritative*, so re-derive it each
    tick: (1) heal status.podIP against the real Ready pod, (2) idle-sweep 1->0 past
    grace. Cheap pod-list, so the interval is short for fast self-heal of a dead/replaced
    pod (#1)."""
    s = get_settings()
    if _teardown_started(spec.get("accountId") or name, meta):
        return
    if _quiesce_if_inactive(spec, name, namespace, status, patch, logger):
        return
    identity = _ids_or_reject(spec, name, status, patch, logger)
    if identity is None:
        return
    account_id, username = identity
    if _live_teardown_started(account_id, namespace, uid, meta):
        return
    replicas = kube.get_replicas(namespace, account_id)

    # The direct Draining status is the durable route fence. If scale-to-zero
    # succeeded but the handler died before Kopf flushed its trailing Zero
    # patch, derive completion from Deployment + physical Pod state. Persistent
    # runners deliberately return here and wake on the next tick, after the
    # recovery status itself is durable.
    if replicas <= 0 and status.get("phase") in _RUNNER_DRAIN_PHASES:
        if not kube.workload_pods_gone(namespace, account_id, "agent-runner"):
            return
        kube.set_cr_status(
            namespace,
            account_id,
            phase="Zero",
            podIP=None,
            readyReplicas=0,
        )
        patch.status["phase"] = "Zero"
        patch.status["podIP"] = None
        patch.status["readyReplicas"] = 0
        return

    defaults = _defaults_or_reject(spec, status, patch, logger)
    if defaults is None:
        return
    desired_generation = kube.allocation_hash(spec, s, defaults, username)
    if status.get("desiredAllocationHash") != desired_generation:
        patch.status["desiredAllocationHash"] = desired_generation

    # Converge the global managed policy (admin hook edits have no CR event, so
    # this timer is how they propagate). Throttled + digest-guarded → cheap.
    _render_managed_policy(namespace, logger)
    # Same story for isolation: an admin toggle has no CR event, so this timer is
    # how it propagates. NetworkPolicy changes take effect immediately; a changed
    # proxy environment/template converges separately at an idle zero boundary.
    try:
        isolation = _render_network_policies(namespace, logger)
        expected_intent = isolation_intent_digest(isolation, s)
        expected_config_sha = egress_proxy.config_sha256(
            egress_proxy.render_squid_conf(isolation, s)
        )
        boundary_ready = (
            not _network_isolation_dirty
            and _network_isolation_applied_intent == expected_intent
            and kube.network_policy_enforced(namespace, s)
            and kube.egress_proxy_ready(
                namespace,
                expected_intent=expected_intent,
                expected_config_sha=expected_config_sha,
                settings=s,
                require_all_replicas=False,
            )
        )
        if not boundary_ready:
            raise RuntimeError("tenant network boundary is not verified")
        _upsert_condition(
            status,
            patch,
            "NetworkIsolationReady",
            True,
            "Applied",
            "CNI fact, NetworkPolicies, and egress proxy match the desired generation",
        )
    except Exception as exc:
        logger.error(
            "tenant network boundary failed account=%s: %s",
            account_id,
            exc,
        )
        _quiesce_for_network_failure(
            namespace=namespace,
            account_id=account_id,
            status=status,
            patch=patch,
            logger=logger,
            reason=str(exc) or type(exc).__name__,
        )
        return

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
        if _live_teardown_started(account_id, namespace, uid, meta):
            return
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
        if _live_teardown_started(account_id, namespace, uid, meta):
            return
        applied_generation = kube.applied_allocation_hash(namespace, account_id)
        # The egress generation is a SECOND trigger, deliberately not folded into
        # the allocation hash (that has eight call sites; one that forgot to thread
        # `iso` would mean a permanent desired!=applied mismatch, i.e. a restart
        # loop). Without its own trigger the config never converges at all: a
        # PERSISTENT runner is never dormant, so ensure_runtime_objects always
        # returns early and this branch has nothing to compare. Measured — the
        # proxy env simply never arrived, and nothing said so.
        iso = isolation
        egress_drift = (kube.applied_egress_generation(namespace, account_id)
                        != kube.egress_generation(iso, s))
        if applied_generation != desired_generation or egress_drift:
            if _live_teardown_started(account_id, namespace, uid, meta):
                return
            kube.ensure_runtime_objects(
                namespace, account_id, username, kube.resolve_image(spec, s),
                s.kubernetes.runner_image_pull_policy, s,
                names.owner_ref(name, uid), spec, defaults, iso)
            if _live_teardown_started(account_id, namespace, uid, meta):
                return
        if _is_persistent(spec):
            if _live_teardown_started(account_id, namespace, uid, meta):
                return
            if (
                not kube.network_policy_enforced(namespace, s)
                or not kube.egress_proxy_ready(
                    namespace,
                    expected_intent=isolation_intent_digest(isolation, s),
                    expected_config_sha=egress_proxy.config_sha256(
                        egress_proxy.render_squid_conf(isolation, s)
                    ),
                    settings=s,
                    require_all_replicas=False,
                )
            ):
                raise kopf.TemporaryError(
                    "tenant network boundary is not Ready; persistent Runner remains at zero",
                    delay=5,
                )
            if kube.get_replicas(namespace, account_id) < 0:
                patch.status["phase"] = "PendingTerminalDrain"
                patch.status["podIP"] = None
                patch.status["readyReplicas"] = 0
                return
            if _live_teardown_started(account_id, namespace, uid, meta):
                return
            kube.scale(namespace, account_id, 1)
            if _live_teardown_started(account_id, namespace, uid, meta):
                return
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
        patch.status["phase"] = (
            status.get("phase")
            if status.get("phase") in _RUNNER_DRAIN_PHASES
            else "Waking"
        )
        patch.status["podIP"] = None
        patch.status["readyReplicas"] = 0
        return

    if real_ip != status.get("podIP"):
        # A live pod at an IP status doesn't know — heal. A *changed* podIP is a
        # replacement pod, which must get its own min_alive anti-thrash window (don't let
        # it inherit the dead pod's startedAt clock).
        # Preserve a concurrent drain gate. Re-blessing it as Running would let
        # a stale wake/timer route new work into a pod being scaled down.
        phase = (
            status.get("phase")
            if status.get("phase") in _RUNNER_DRAIN_PHASES
            else "Running"
        )
        patch.status["phase"] = phase
        patch.status["podIP"] = real_ip
        patch.status["readyReplicas"] = 1
        if phase == "Running":
            patch.status["startedAt"] = time.time()
            patch.status["idleSince"] = None
        logger.info("healed stale podIP account=%s -> %s", account_id, real_ip)
        return  # next tick runs the idle check against the now-correct IP

    applied_generation = kube.applied_allocation_hash(namespace, account_id)
    # Never interrupt an active run. Record drift on the CR, then use the
    # active-runs-free boundary below to replace both persistent and autoscaled
    # runners whose environment/template predates this isolation generation.
    egress_stale = (kube.applied_egress_generation(namespace, account_id)
                    != kube.egress_generation(isolation, s))
    if applied_generation != desired_generation or egress_stale:
        _upsert_condition(
            status, patch, "AllocationReady", False, "PendingRunnerRestart",
            "running Runner uses an older identity or allocation generation"
            if applied_generation != desired_generation else
            "running Runner predates the current egress policy; it applies on restart",
        )
    else:
        _upsert_condition(
            status, patch, "AllocationReady", True, "Applied",
            "Runner and desired allocation generations match",
        )

    # Persistent runners do not idle-scale, but an idle instance with a stale template
    # gets a controlled zero boundary so it cannot remain wrong forever.
    if _is_persistent(spec):
        if applied_generation != desired_generation or egress_stale:
            try:
                port = s.kubernetes.runner_service_port
                health = httpx.get(
                    f"http://{real_ip}:{port}/health", timeout=2.0, trust_env=False).json()
            except Exception as exc:
                logger.debug("persistent convergence probe failed account=%s: %s", account_id, exc)
                return
            if _runner_drain_ready(
                namespace=namespace,
                account_id=account_id,
                pod_ip=real_ip,
                port=port,
                health=health,
                status=status,
                patch=patch,
                logger=logger,
            ):
                kube.scale(namespace, account_id, 0)
                # Keep the durable route/admission gate until the old Pod has
                # physically disappeared. A new wake before the Deployment
                # controller observes replicas=0 could otherwise cancel the
                # deletion and re-bless this permanently drained process.
                patch.status["phase"] = "Draining"
                patch.status["podIP"] = real_ip
                patch.status["readyReplicas"] = 1
                logger.info(
                    "restarting idle persistent Runner for template/egress account=%s",
                    account_id,
                )
        return

    # --- idle sweep (always against the real, healed IP — never status.podIP) ---------
    # An old unrestricted-mode pod has no proxy env. The new stable policy selects
    # it immediately, so leaving it alive indefinitely would make every external
    # call fail. Replace it at the first active-run-free boundary.
    if egress_stale:
        try:
            port = s.kubernetes.runner_service_port
            health = httpx.get(
                f"http://{real_ip}:{port}/health", timeout=2.0, trust_env=False).json()
        except Exception as exc:
            logger.debug("egress migration probe failed account=%s: %s", account_id, exc)
            return
        if _runner_drain_ready(
            namespace=namespace,
            account_id=account_id,
            pod_ip=real_ip,
            port=port,
            health=health,
            status=status,
            patch=patch,
            logger=logger,
        ):
            kube.scale(namespace, account_id, 0)
            patch.status["phase"] = "Draining"
            patch.status["podIP"] = real_ip
            patch.status["readyReplicas"] = 1
            logger.info("stopped idle Runner for egress template migration account=%s", account_id)
        return

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
    # A prior attempt may have persisted Draining and then lost the scale
    # response or post-status drain confirmation. Once the conflicting activity is gone,
    # resume immediately instead of leaving the tenant unroutable for a fresh
    # full idle-grace window.
    if active == 0 and (
            status.get("phase") == "Draining" or (now - last) > grace):
        if not _runner_drain_ready(
            namespace=namespace,
            account_id=account_id,
            pod_ip=real_ip,
            port=port,
            health=h,
            status=status,
            patch=patch,
            logger=logger,
        ):
            return
        kube.scale(namespace, account_id, 0)
        patch.status["phase"] = "Draining"
        patch.status["podIP"] = real_ip
        patch.status["readyReplicas"] = 1
        patch.status["idleSince"] = now
        logger.info("slept idle account=%s (idle %.0fs > grace %ds)", account_id, now - last, grace)


# --- live admin edits (CR spec patches from control-panel.update_tenant_runtime) ----
# Each handler skips the CREATE event (old is None) — `ensure` already builds objects
# with the correct resources/storage and scales persistent. They act only on real edits.

@kopf.on.field(GROUP, VERSION, PLURAL, field="spec.desiredState")
@_serialize_account_lifecycle
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
@_serialize_account_lifecycle
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
@_serialize_account_lifecycle
def on_resources_change(
    spec, name, namespace, old, new, logger, uid=None, meta=None, **_,
):
    if old is None:
        return
    s = get_settings()
    account_id, _ = _ids(spec, name)
    if _live_teardown_started(account_id, namespace, uid, meta):
        return
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
    if _live_teardown_started(account_id, namespace, uid, meta):
        return
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
@_serialize_account_lifecycle
def on_storage_change(
    spec, name, namespace, old, new, patch, logger, uid=None, meta=None, **_,
):
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
    if _live_teardown_started(account_id, namespace, uid, meta):
        return
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
