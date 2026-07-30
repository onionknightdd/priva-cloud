"""Kubernetes provisioner — control-panel as the control plane.

On user creation: ``ensure_tenant`` writes an AgentTenant CR (the operator
reconciles it into a scale-to-zero Deployment + Service + PVC). On a runtime
request: ``wake_and_wait`` patches spec.wake.requestedAt and polls the CR
status until the operator reports the woken pod's IP — the endpoint the ext_proc
EPP steers agentgateway to. Deterministic naming means no registry is needed.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from kubernetes import client, config

from priva_common.config import get_settings
from priva_common.logging import get_app_logger
from priva_common.tenant_lifecycle import AGENTTENANT_FINALIZER

logger = get_app_logger(__name__)

# Per-account in-flight wake Tasks (coalescing). Concurrent cold requests for one account
# await a single shared wake (one spec.wake patch, one poll loop) instead of each firing
# their own. In-process / per-replica only: prod runs N EPP replicas, so this dedupes
# within a replica — the operator's idempotent on_wake is the real cross-replica safety net.
_wake_tasks: dict[str, "asyncio.Task[str | None]"] = {}
_terminal_wake_tasks: dict[str, "asyncio.Task[str | None]"] = {}

GROUP = "priva.io"
VERSION = "v1alpha1"
PLURAL = "agenttenants"
_loaded = False
_RESTART_POD_TIMEOUT_SECONDS = 60.0
_RESTART_POD_POLL_SECONDS = 0.25
_RESTART_DRAINING = "draining"
_RESTART_WAKING = "waking"
# Keep a durable purge marker for several sync ticks after the CR disappears.
# Otherwise a pass that captured an older active snapshot can recreate the CR
# after the account row was reaped, leaving an orphan runtime/storage allocation.
_PURGED_ROW_REAP_GRACE_SECONDS = 300.0


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


def _core() -> "client.CoreV1Api":
    _load()
    return client.CoreV1Api()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _purge_reap_grace_elapsed(user) -> bool:
    """Whether a real data-spine tombstone is old enough to hard-delete."""
    updated_at = getattr(user, "updated_at", None)
    if not isinstance(updated_at, datetime):
        # Compatibility for old embedders/test records that predate updated_at.
        return True
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - updated_at).total_seconds()
    return age >= _PURGED_ROW_REAP_GRACE_SECONDS


def _get_tenant_live(account_id: str) -> dict | None:
    """Point-read one CR; a namespace-wide list snapshot is not deletion proof."""
    s = get_settings()
    try:
        return _custom().get_namespaced_custom_object(
            GROUP,
            VERSION,
            s.kubernetes.namespace_tenants,
            PLURAL,
            account_id,
        )
    except client.ApiException as exc:
        if exc.status == 404:
            return None
        raise


def _runtime_defaults_spec(defaults: Any | None = None) -> dict:
    """Serialize the platform defaults into the AgentTenant desired-state snapshot.

    The operator must not reinterpret a data-spine outage as a zero/disabled policy.
    Control Panel therefore resolves the global record once and stores the complete,
    non-sensitive result on each CR.  Per-account override fields remain separate and
    continue to win in the operator's resolver cascade.
    """
    if defaults is None:
        from priva_common.dataplane import get_client
        defaults = get_client().runner_defaults.get()
    return {
        "idleGraceSeconds": int(defaults.idle_grace_seconds),
        "minAliveAfterWakeSeconds": int(defaults.min_alive_after_wake_seconds),
        "cpuCores": float(defaults.cpu_cores),
        "memoryMb": int(defaults.memory_mb),
        "storageGb": int(defaults.storage_gb),
        "terminal": {
            "resourcePercent": int(defaults.terminal_resource_percent),
            "maxSessions": int(defaults.terminal_max_sessions),
            "idleTimeoutSeconds": int(defaults.terminal_idle_timeout_seconds),
            "maxLifetimeSeconds": int(defaults.terminal_max_lifetime_seconds),
            "scaleDownGraceSeconds": int(defaults.terminal_scale_down_grace_seconds),
        },
    }


def _desired_state_for(status: str | None) -> str:
    """Account lifecycle status → CR ``spec.desiredState``.

    Anything but ``active`` quiesces the account's pods. This mapper is used for
    ordinary ensure/repair only; destructive purge explicitly writes
    ``desiredState=purge`` immediately before deleting the finalized CR.
    """
    return "active" if (status or "active") == "active" else "offboarding"


def _tenant_spec(
    account_id: str,
    username: str,
    *,
    status: str | None = None,
    runner_type: str | None = None,
    cpu: float | None = None,
    memory_mb: int | None = None,
    storage_gb: int | None = None,
    runtime_defaults: dict | None = None,
) -> dict:
    s = get_settings()
    spec: dict = {
        "accountId": account_id,
        "username": username,
        "desiredState": _desired_state_for(status),
        "agentRunnerType": runner_type or "auto_scale",
        "concurrency": {"maxConcurrentSessions": s.kubernetes.max_concurrent_sessions},
        "runtimeDefaults": runtime_defaults or _runtime_defaults_spec(),
    }
    res: dict = {}
    if cpu is not None:
        res["cpu"] = float(cpu)
    if memory_mb is not None:
        res["memoryMb"] = int(memory_mb)
    if res:
        spec["resources"] = res
    if storage_gb is not None:
        spec["storageGb"] = int(storage_gb)
    return spec


def _repair_existing_tenant(account_id: str, username: str, runtime_defaults: dict) -> bool:
    """Repair authoritative identity/default fields without overwriting overrides."""
    s = get_settings()
    ns = s.kubernetes.namespace_tenants
    obj = _custom().get_namespaced_custom_object(GROUP, VERSION, ns, PLURAL, account_id)
    current = obj.get("spec") or {}
    metadata = obj.get("metadata") or {}
    existing_account_id = current.get("accountId")
    if existing_account_id and existing_account_id != account_id:
        raise ValueError(
            f"AgentTenant {account_id!r} contains mismatched accountId "
            f"{existing_account_id!r}")
    wanted = {
        "accountId": account_id,
        "username": username,
        "runtimeDefaults": runtime_defaults,
    }
    finalizers = list(metadata.get("finalizers") or [])
    finalizer_missing = AGENTTENANT_FINALIZER not in finalizers
    if all(current.get(key) == value for key, value in wanted.items()) and not finalizer_missing:
        return False
    if finalizer_missing:
        finalizers.append(AGENTTENANT_FINALIZER)
    _custom().patch_namespaced_custom_object(
        GROUP,
        VERSION,
        ns,
        PLURAL,
        account_id,
        {"metadata": {"finalizers": finalizers}, "spec": wanted},
    )
    logger.info("repaired AgentTenant identity/defaults account={} username={}", account_id, username)
    return True


def ensure_tenant(account_id: str, username: str, *, status: str | None = None,
                  runner_type: str | None = None,
                  cpu: float | None = None, memory_mb: int | None = None,
                  storage_gb: int | None = None,
                  runtime_defaults: dict | None = None) -> None:
    """Create or repair the AgentTenant CR for an account (idempotent).

    Inheritable fields (resources, storageGb, idle) are written ONLY when an
    explicit per-account value is passed — otherwise they are OMITTED so the field is
    absent on the CR, which the operator reads as "inherit the global runner default"
    (the admin Sandbox panel). A present field = a per-account override that wins and
    stops following the default. The admin create-user path passes nothing (full
    inherit); the self-registration approval path passes the user-requested specs
    (= overrides). ``status`` is the account's lifecycle state: a non-active account is
    created quiesced, so a re-created CR can never wake a frozen account back up."""
    s = get_settings()
    ns = s.kubernetes.namespace_tenants
    resolved_defaults = runtime_defaults or _runtime_defaults_spec()
    spec = _tenant_spec(
        account_id, username, status=status, runner_type=runner_type, cpu=cpu,
        memory_mb=memory_mb, storage_gb=storage_gb,
        runtime_defaults=resolved_defaults,
    )
    body = {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": "AgentTenant",
        "metadata": {
            "name": account_id,
            "namespace": ns,
            "finalizers": [AGENTTENANT_FINALIZER],
        },
        "spec": spec,
    }
    try:
        _custom().create_namespaced_custom_object(GROUP, VERSION, ns, PLURAL, body)
        logger.info("provisioned AgentTenant account={}", account_id)
    except client.ApiException as exc:
        if exc.status != 409:
            raise
        # AlreadyExists is not sufficient: a manually re-applied CR may be missing
        # username/runtimeDefaults. Repair only authoritative fields and preserve all
        # per-account overrides and lifecycle state.
        _repair_existing_tenant(account_id, username, resolved_defaults)


def sync_all_tenants(*, defaults: Any | None = None) -> dict[str, int]:
    """Reconcile active data-spine accounts into complete AgentTenant CRs.

    One account/default/resource list is used for the whole pass. Existing objects are
    patched only on drift, so the periodic backstop creates no steady-state CR events.
    Also the purge sweep: a ``purged`` account never gets its CR back, and once the
    operator's teardown finalizer has released the CR, this reaps the tombstone row —
    the step that survives a control-panel crash mid-purge.
    """
    from priva_common.dataplane import get_client
    from priva_common.user_store import get_user_store

    dp = get_client()
    runtime_defaults = _runtime_defaults_spec(defaults or dp.runner_defaults.get())
    resources = {row.account_id: row for row in dp.resource_specs.list()}
    existing = {item.get("metadata", {}).get("name"): item for item in list_tenants()}
    counts = {"created": 0, "repaired": 0, "unchanged": 0, "skipped": 0, "purged": 0}
    store = get_user_store()
    for user in store.list_users():
        account_id = user.account_id
        if not account_id:
            counts["skipped"] += 1
            continue
        obj = existing.get(account_id)
        if user.status == "offboarding":
            # ``offboarding`` is the crash-safe revoked-before-delete stage.
            # Always materialize a finalized purge tombstone, even if the CR is
            # currently absent; only after Kubernetes accepts its delete do we
            # advance the row to ``purged``. A crash at any instruction repeats
            # an idempotent deprovision rather than mistaking "never started"
            # for "finalizer completed".
            try:
                delete_tenant(account_id, user.username)
                store.update_user(user.username, status="purged")
                logger.info(
                    "purge finalizer requested account={} username={}",
                    account_id,
                    user.username,
                )
                counts["skipped"] += 1
            except Exception as exc:
                logger.warning("purge sweep failed account={}: {}", account_id, exc)
                counts["skipped"] += 1
            continue
        if user.status == "purged":
            # A purge that keeps failing must not abort the pass — every other account
            # still has to be created / repaired / converged on this tick.
            try:
                if obj is not None:
                    # Never re-create, and re-issue the delete: this is the retry for a
                    # purge whose CR delete failed (or never ran) after the tombstone.
                    delete_tenant(account_id, user.username)
                    counts["skipped"] += 1
                elif not _purge_reap_grace_elapsed(user):
                    # Let any sync pass holding an older "active" snapshot
                    # finish and observe the durable tombstone first.
                    counts["skipped"] += 1
                else:
                    # The list above is only a snapshot. Prove the CR is still
                    # absent at the destructive decision point.
                    live_obj = _get_tenant_live(account_id)
                    if live_obj is not None:
                        delete_tenant(account_id, user.username)
                        counts["skipped"] += 1
                    else:
                        dp.accounts.delete(account_id)
                        logger.info(
                            "purge complete, account row reaped account={} username={}",
                            account_id,
                            user.username,
                        )
                        counts["purged"] += 1
            except Exception as exc:
                logger.warning("purge sweep failed account={}: {}", account_id, exc)
                counts["skipped"] += 1
            continue
        if obj is not None:
            current = obj.get("spec") or {}
            wanted = {
                "accountId": account_id,
                "username": user.username,
                "runtimeDefaults": runtime_defaults,
            }
            drifted = not all(current.get(key) == value for key, value in wanted.items())
            if drifted:
                _repair_existing_tenant(account_id, user.username, runtime_defaults)
            desired_state = _desired_state_for(user.status)
            if current.get("desiredState", "active") != desired_state:
                # A CR patch that keeps failing must not abort the pass either — the
                # accounts after this one still need their tick (tombstones included).
                try:
                    set_tenant_desired_state(account_id, desired_state)
                except Exception as exc:
                    logger.warning("desiredState converge failed account={}: {}", account_id, exc)
                    counts["skipped"] += 1
                    continue
                drifted = True
            counts["repaired" if drifted else "unchanged"] += 1
            continue
        if user.status != "active":
            counts["skipped"] += 1
            continue
        # Re-read the authoritative account immediately before CREATE. The list
        # captured at the start of this pass may predate disable/offboarding.
        try:
            live_user = store.get_user(user.username)
        except Exception as exc:
            logger.warning(
                "account status recheck failed account={}: {}", account_id, exc
            )
            counts["skipped"] += 1
            continue
        if (
            live_user is None
            or live_user.account_id != account_id
            or live_user.status != "active"
        ):
            counts["skipped"] += 1
            continue
        resource = resources.get(account_id)
        ensure_tenant(
            account_id,
            user.username,
            status=user.status,
            runner_type=user.agent_runner_type,
            cpu=(resource.cpu_cores if resource else None),
            memory_mb=(resource.memory_mb if resource else None),
            storage_gb=(resource.volume_gb if resource else None),
            runtime_defaults=runtime_defaults,
        )
        # Close the remaining status-read → CR-create window. A concurrent purge
        # carries explicit destructive intent, so fold a stale create back into
        # the finalized delete protocol. A disable only quiesces it.
        try:
            live_user = store.get_user(user.username)
        except Exception as exc:
            logger.warning(
                "post-create account status recheck failed account={}: {}",
                account_id,
                exc,
            )
            counts["skipped"] += 1
            continue
        same_account = (
            live_user is not None and live_user.account_id == account_id
        )
        if same_account and live_user.status in ("offboarding", "purged"):
            delete_tenant(account_id, user.username)
            counts["skipped"] += 1
        elif same_account and live_user.status != "active":
            set_tenant_desired_state(account_id, "offboarding")
            counts["skipped"] += 1
        elif not same_account:
            # A missing/mismatched DB row alone is not authorization to destroy
            # storage. Keep the finalized CR and make the orphan visible.
            logger.error(
                "orphan AgentTenant detected after create account={} username={}",
                account_id,
                user.username,
            )
            counts["skipped"] += 1
        else:
            counts["created"] += 1
    logger.info("AgentTenant account sync complete {}", counts)
    return counts


def update_tenant_runtime(account_id: str, *, runner_type: str | None = None,
                          cpu: float | None = None, memory_mb: int | None = None,
                          storage_gb: int | None = None) -> None:
    """Admin live-edit: patch ONLY the provided keys onto the AgentTenant spec
    (strategic merge). The operator's field handlers do all cluster mutation —
    re-template the Deployment resources (Recreate restart), grow the PVC, or
    toggle persistent. The provisioner never touches Deployments/PVCs directly."""
    s = get_settings()
    ns = s.kubernetes.namespace_tenants
    spec: dict = {}
    if runner_type is not None:
        if runner_type not in ("auto_scale", "persistent"):
            raise ValueError(f"bad runner_type {runner_type!r}")
        spec["agentRunnerType"] = runner_type
    res: dict = {}
    if cpu is not None:
        res["cpu"] = float(cpu)
    if memory_mb is not None:
        res["memoryMb"] = int(memory_mb)
    if res:
        spec["resources"] = res
    if storage_gb is not None:
        spec["storageGb"] = int(storage_gb)
    if not spec:
        return
    _custom().patch_namespaced_custom_object(GROUP, VERSION, ns, PLURAL, account_id, {"spec": spec})
    logger.info("patched AgentTenant runtime account={} spec={}", account_id, spec)


def set_tenant_desired_state(account_id: str, desired_state: str) -> None:
    """Admin disable/enable: patch the lifecycle field the operator quiesces on.

    ``offboarding`` scales Runner + Terminal to zero and keeps them there; ``active``
    hands the account back to the wake path. A missing CR (404) is a no-op — the
    account's status is authoritative and ``sync_all_tenants`` re-creates the CR
    already carrying the right desiredState. Blocking kube call."""
    if desired_state not in ("active", "offboarding", "purge"):
        raise ValueError(f"bad desiredState {desired_state!r}")
    s = get_settings()
    ns = s.kubernetes.namespace_tenants
    try:
        _custom().patch_namespaced_custom_object(
            GROUP, VERSION, ns, PLURAL, account_id, {"spec": {"desiredState": desired_state}})
    except client.ApiException as exc:
        if exc.status != 404:
            raise
        return
    logger.info("patched AgentTenant desiredState account={} state={}", account_id, desired_state)


def delete_tenant(account_id: str, username: str | None = None) -> None:
    """Purge: mark destructive intent, then delete the account's AgentTenant CR.

    ``spec.desiredState=purge`` is committed first so the delete finalizer can
    distinguish this authorized account purge from an accidental/out-of-band CR
    deletion. Only the former may destroy storage. If the CR is absent, this
    creates a finalized purge tombstone first, so "not found" is never confused
    with proof that teardown completed. Blocking kube call.
    """
    s = get_settings()
    ns = s.kubernetes.namespace_tenants
    api = _custom()
    try:
        obj = api.get_namespaced_custom_object(
            GROUP, VERSION, ns, PLURAL, account_id
        )
    except client.ApiException as exc:
        if exc.status != 404:
            raise
        if not username:
            raise RuntimeError(
                "AgentTenant is absent; username is required to create a "
                "finalized purge tombstone"
            ) from exc
        body = {
            "apiVersion": f"{GROUP}/{VERSION}",
            "kind": "AgentTenant",
            "metadata": {
                "name": account_id,
                "namespace": ns,
                "finalizers": [AGENTTENANT_FINALIZER],
            },
            "spec": {
                "accountId": account_id,
                "username": username,
                "desiredState": "purge",
            },
        }
        try:
            api.create_namespaced_custom_object(
                GROUP, VERSION, ns, PLURAL, body
            )
        except client.ApiException as create_exc:
            if create_exc.status != 409:
                raise
            # GET→CREATE is a race. Never interpret AlreadyExists as proof that
            # the winner carries our destructive-intent marker: it may be an
            # ordinary active CR created by the periodic repair loop. Re-read
            # and patch that concrete object before issuing DELETE.
            try:
                obj = api.get_namespaced_custom_object(
                    GROUP, VERSION, ns, PLURAL, account_id
                )
            except client.ApiException as read_exc:
                raise RuntimeError(
                    "AgentTenant creation conflicted, but the winning object "
                    "could not be read and marked for purge"
                ) from read_exc
        else:
            obj = None  # The object we created already has marker + finalizer.

    if obj is not None:
        metadata = obj.get("metadata") or {}
        current = obj.get("spec") or {}
        if metadata.get("deletionTimestamp"):
            # Kubernetes forbids adding a new finalizer after deletion starts.
            # Do not bless an out-of-band delete as a completed account purge:
            # keep the account offboarding and let the next sweep create a
            # fresh finalized tombstone after this object disappears.
            if (
                current.get("desiredState") != "purge"
                or AGENTTENANT_FINALIZER not in (metadata.get("finalizers") or [])
            ):
                raise RuntimeError(
                    "AgentTenant is already deleting without the finalized "
                    "purge marker"
                )
        else:
            finalizers = list(metadata.get("finalizers") or [])
            if AGENTTENANT_FINALIZER not in finalizers:
                finalizers.append(AGENTTENANT_FINALIZER)
            api.patch_namespaced_custom_object(
                GROUP,
                VERSION,
                ns,
                PLURAL,
                account_id,
                {
                    "metadata": {"finalizers": finalizers},
                    "spec": {"desiredState": "purge"},
                },
            )
    try:
        api.delete_namespaced_custom_object(
            GROUP, VERSION, ns, PLURAL, account_id
        )
    except client.ApiException as exc:
        # Safe idempotency only after this invocation proved/installed both the
        # purge marker and finalizer. Disappearance here means another delete
        # completed the same authorized teardown.
        if exc.status != 404:
            raise
    logger.info("deleted AgentTenant account={}", account_id)


def list_tenants() -> list[dict]:
    """List every AgentTenant CR (metadata + spec + operator-written status).

    Used by the admin fleet view to enumerate accounts and read each one's live
    phase / readyReplicas / podIP without a registry. Blocking kube call — call
    via asyncio.to_thread from async paths.
    """
    s = get_settings()
    ns = s.kubernetes.namespace_tenants
    try:
        resp = _custom().list_namespaced_custom_object(GROUP, VERSION, ns, PLURAL)
        return resp.get("items", []) or []
    except client.ApiException as exc:
        if exc.status == 404:
            return []
        raise


async def probe_health(pod_ip: str, port: int) -> dict | None:
    """Fetch a warm pod's ``/health`` body (active_runs, last_activity_ts, …).

    Fail-open: any error/timeout returns None so a single dead/replaced pod behind
    a stale status.podIP can't break the whole fleet snapshot. Like ``_alive``, a
    200 here could be a recycled-CIDR pod — the count is a live hint, not ledger.
    """
    try:
        async with httpx.AsyncClient(trust_env=False) as cx:
            r = await cx.get(f"http://{pod_ip}:{port}/health", timeout=1.5)
        if r.status_code < 500:
            return r.json()
    except Exception:
        return None
    return None


def list_gateway_pod_ips() -> list[str]:
    """IPs of the Running data-plane gateway (agentgateway) pods.

    The metrics port (15020) is a container port only — it's NOT on the Service —
    so the admin scrapes pod IPs directly, label-selected by the Gateway name.
    Best-effort: returns [] on any error so the gateway-traffic tile degrades to
    'unavailable' rather than failing the dashboard. Blocking kube call.
    """
    s = get_settings()
    ns = s.kubernetes.namespace_tenants
    selector = f"gateway.networking.k8s.io/gateway-name={s.kubernetes.gateway_name}"
    try:
        resp = _core().list_namespaced_pod(ns, label_selector=selector)
    except client.ApiException:
        return []
    return [p.status.pod_ip for p in resp.items
            if p.status and p.status.phase == "Running" and p.status.pod_ip]


def any_ready_runner_endpoint() -> str | None:
    """Endpoint ``ip:port`` of ANY ready agent-runner pod, or None if none awake.

    Used by control-panel's /sandbox/apidocs proxy (app.py): the OpenAPI schema is identical
    on every account's pod and a tokenless top-level browser navigation can't resolve a
    specific account, so control-panel proxies the docs from any Ready runner (the
    GIE/EPP response path would truncate the ~91KB schema). Blocking kube call.
    """
    s = get_settings()
    ns = s.kubernetes.namespace_tenants
    port = s.kubernetes.runner_service_port
    try:
        resp = _core().list_namespaced_pod(ns, label_selector="app=agent-runner")
    except client.ApiException:
        return None
    for p in resp.items:
        st = p.status
        if not st or st.phase != "Running" or not st.pod_ip:
            continue
        if p.metadata and p.metadata.deletion_timestamp is not None:
            continue
        if any(c.type == "Ready" and c.status == "True" for c in (st.conditions or [])):
            return f"{st.pod_ip}:{port}"
    return None


def _parse_gateway_metrics(text: str) -> dict:
    """Sum agentgateway_requests_total (by status-class + backend) and downstream
    connections from a Prometheus exposition. Each sample line is ``name{labels} value``;
    the value is the last whitespace token. Unparseable lines are skipped."""
    import re

    status_re = re.compile(r'status="(\d+)"')
    total = 0
    connections = 0
    by_status: dict[str, int] = {}
    by_backend: dict[str, int] = {}
    for line in text.splitlines():
        if not line or line[0] == "#":
            continue
        if line.startswith("agentgateway_requests_total"):
            try:
                val = int(float(line.rsplit(None, 1)[1]))
            except (ValueError, IndexError):
                continue
            total += val
            m = status_re.search(line)
            cls = f"{m.group(1)[0]}xx" if m else "other"
            by_status[cls] = by_status.get(cls, 0) + val
            backend = ("agent-runner" if "agent-runners" in line
                       else "control-panel" if "control-panel" in line else "other")
            by_backend[backend] = by_backend.get(backend, 0) + val
        elif line.startswith("agentgateway_downstream_connections_total"):
            try:
                connections += int(float(line.rsplit(None, 1)[1]))
            except (ValueError, IndexError):
                continue
    return {"total_requests": total, "connections": connections,
            "by_status_class": by_status, "by_backend": by_backend}


async def scrape_gateway_metrics(pod_ip: str, port: int) -> dict | None:
    """Fetch + parse the gateway pod's Prometheus ``/metrics``. Fail-open: any
    error / non-200 returns None so the admin can fall through to the next pod (or
    report unavailable). Cumulative counters — the SPA derives req/s from the delta."""
    try:
        async with httpx.AsyncClient(trust_env=False) as cx:
            r = await cx.get(f"http://{pod_ip}:{port}/metrics", timeout=2.0)
        if r.status_code != 200:
            return None
        return _parse_gateway_metrics(r.text)
    except Exception:
        return None


def _cpu_to_millicores(q: str) -> float:
    """K8s CPU quantity → millicores. metrics-server reports nanocores ('5383948n');
    other forms are 'm' (milli), 'u' (micro), or bare cores ('1', '0.5'). Bad input → 0."""
    if not q:
        return 0.0
    try:
        if q.endswith("n"):
            return float(q[:-1]) / 1e6
        if q.endswith("u"):
            return float(q[:-1]) / 1e3
        if q.endswith("m"):
            return float(q[:-1])
        return float(q) * 1000.0
    except ValueError:
        return 0.0


def _mem_to_mib(q: str) -> float:
    """K8s memory quantity → MiB. metrics-server reports binary 'Ki' ('82556Ki');
    handle the binary (Ki/Mi/Gi/Ti) and decimal (k/M/G/T) suffixes + bare bytes. Bad → 0."""
    if not q:
        return 0.0
    binary = {"Ki": 1 / 1024, "Mi": 1.0, "Gi": 1024.0, "Ti": 1024.0 * 1024}
    decimal = {"k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}
    try:
        for suf, factor in binary.items():
            if q.endswith(suf):
                return float(q[:-len(suf)]) * factor
        for suf, factor in decimal.items():
            if q.endswith(suf):
                return float(q[:-1]) * factor / (1024.0 * 1024.0)
        return float(q) / (1024.0 * 1024.0)  # bare bytes
    except ValueError:
        return 0.0


def scrape_runner_usage() -> dict | None:
    """Live CPU/memory of the agent-runner pods, summed per account.

    Reads the ``metrics.k8s.io/v1beta1`` PodMetrics (the same data ``kubectl top``
    shows; needs the metrics-server addon + a ``metrics.k8s.io: pods`` RBAC grant).
    Each PodMetrics carries the pod's labels, so we key by ``priva.io/account-id``
    rather than parsing pod names. Returns ``{account_id: {"cpu_m", "memory_mb"}}``.
    Fail-open: any error (metrics-server down, RBAC missing) returns None so the
    Resource Quota view degrades to 'unavailable' rather than failing. Blocking
    kube call — invoke via ``asyncio.to_thread`` from async paths.
    """
    s = get_settings()
    ns = s.kubernetes.namespace_tenants
    try:
        resp = _custom().list_namespaced_custom_object(
            "metrics.k8s.io", "v1beta1", ns, "pods", label_selector="app=agent-runner"
        )
    except Exception:
        return None

    usage: dict[str, dict[str, float]] = {}
    for item in resp.get("items") or []:
        meta = item.get("metadata") or {}
        account_id = (meta.get("labels") or {}).get("priva.io/account-id")
        if not account_id:
            continue
        cpu_m = 0.0
        mem_mb = 0.0
        for c in item.get("containers") or []:
            u = c.get("usage") or {}
            cpu_m += _cpu_to_millicores(u.get("cpu", ""))
            mem_mb += _mem_to_mib(u.get("memory", ""))
        acc = usage.setdefault(account_id, {"cpu_m": 0.0, "memory_mb": 0.0})
        acc["cpu_m"] += cpu_m
        acc["memory_mb"] += mem_mb
    return usage


def scrape_volume_usage() -> dict | None:
    """Per-account volume usage (used vs limit bytes), read **wake-free** from the
    quota-manager on the dev NFS server (``GET /usage`` → ``xfs_quota report``). No pod
    is touched, so it works while every runner is scaled to zero. Returns
    ``{account_id: {"used_bytes", "limit_bytes"}}``. Fail-open (None) like
    ``scrape_runner_usage`` so the Resource Quota view degrades to allocated-only.
    Blocking HTTP — invoke via ``asyncio.to_thread``. (Prod: the state-reader / Ceph API.)
    """
    s = get_settings()
    url = s.kubernetes.quota_manager_url.rstrip("/") + "/usage"
    try:
        # trust_env=False: internal Service hop must not route through a host proxy.
        r = httpx.get(url, timeout=4.0, trust_env=False)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def deployment_ready(name: str) -> dict | None:
    """Readiness of one system Deployment, read by exact name (no ``list``).

    Used by the System Map to derive up/down/degraded for ``operator`` and
    ``data-spine`` in ``namespace_system``. The control-panel RBAC grants
    ``deployments: [get, patch]`` (not list), so we read the single object.
    Fail-open: any error (incl. 404 / no kube) returns None — the caller treats
    None as ``down`` (the safe pessimistic reading). Blocking kube call — invoke
    via ``asyncio.to_thread`` from async paths.
    """
    s = get_settings()
    ns = s.kubernetes.namespace_system
    try:
        dep = _apps().read_namespaced_deployment(name, ns)
    except Exception:
        return None
    st = dep.status
    spec = dep.spec
    return {
        "ready": int(getattr(st, "ready_replicas", 0) or 0),
        "desired": int(getattr(spec, "replicas", 0) or 0),
        "available": int(getattr(st, "available_replicas", 0) or 0),
    }


async def dataspine_health() -> dict | None:
    """data-spine reachability (``readyz``) + ``stats`` via the data-plane admin client.

    Runs the blocking gRPC calls in a thread and fails soft: a slow/unreachable
    data-spine resolves to ``ready=False`` (so the inbound gRPC edges show ✕) and
    never stalls the System Map snapshot. ``stats`` degrades to {} on its own error.
    """
    def _probe() -> dict:
        from priva_common.dataplane import get_client

        admin = get_client().admin
        ok, detail = admin.readyz()
        try:
            stats = admin.stats()
        except Exception:
            stats = {}
        return {"ready": bool(ok), "detail": (detail or "")[:160], "stats": dict(stats or {})}

    try:
        return await asyncio.to_thread(_probe)
    except Exception as exc:  # pragma: no cover - data-spine optional locally
        return {"ready": False, "detail": str(exc)[:160], "stats": {}}


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


def _patch_wake(account_id: str, requested_at: str | None = None) -> None:
    """Patch the only scale-up trigger (spec.wake.requestedAt); the operator does the rest."""
    s = get_settings()
    ns = s.kubernetes.namespace_tenants
    _custom().patch_namespaced_custom_object(
        GROUP, VERSION, ns, PLURAL, account_id,
        {"spec": {"wake": {"requestedAt": requested_at or _now_iso()}}},
    )


def _restart_pending(status: dict) -> dict | None:
    pending = status.get("restartPending")
    return pending if isinstance(pending, dict) and pending else None


def _patch_restart_status(
    account_id: str,
    pending: dict | None,
    *,
    route_gate: bool,
) -> bool:
    """Persist one restart checkpoint through the CR status subresource.

    ``pending=None`` clears the marker via JSON merge-patch. A missing CR means
    there is no restart target; callers must not mutate the Deployment without
    the durable route gate.
    """
    s = get_settings()
    ns = s.kubernetes.namespace_tenants
    fields: dict[str, Any] = {"restartPending": pending}
    if route_gate:
        # While the old process may still exist, use the platform-wide
        # Draining phase so every wake source (EPP, scheduler, connector and
        # Operator) recognizes the gate. Once the physical zero boundary has
        # been crossed, Zero safely permits the one intended wake.
        phase = (
            "Draining"
            if pending and pending.get("stage") == _RESTART_DRAINING
            else "Zero"
        )
        fields.update(phase=phase, podIP=None, readyReplicas=0)
    try:
        _custom().patch_namespaced_custom_object_status(
            GROUP, VERSION, ns, PLURAL, account_id, {"status": fields},
        )
    except client.ApiException as exc:
        if exc.status == 404:
            return False
        raise
    return True


async def _alive(pod_ip: str, port: int) -> bool:
    """Bounded, fail-open liveness probe of a warm pod's ``/health``.

    A fast "is anything listening there" guard so a dead/replaced pod behind a stale
    status.podIP can't become a permanent black hole (#1/#2). Any error/timeout => treat
    as not-alive so we fall through to a re-wake; a probe error never crashes the EPP.

    NOTE: k8s reuses pod-CIDR IPs, so a 200 here could be a *different* account's pod —
    this is only a liveness hint. The authoritative freshness source stays the
    operator-healed status.podIP (the operator self-heals it against pod reality).
    """
    try:
        async with httpx.AsyncClient(trust_env=False) as cx:
            r = await cx.get(f"http://{pod_ip}:{port}/health", timeout=1.0)
        return r.status_code < 500
    except Exception:
        return False


async def _drive_wake(account_id: str) -> str | None:
    """Patch spec.wake once, then poll status until Running+podIP or ``wake_hold_seconds``.

    Shared by all concurrent cold requests for one account (coalescing). Bounded by the
    EPP hold (fast-503): on expiry it returns None — the caller 503s "waking, retry" while
    the operator keeps driving the wake (its own wake_timeout_seconds), so the SPA's retry
    lands warm. Blocking kube calls are off-loaded to threads so the event loop stays free.
    """
    s = get_settings()
    port = s.kubernetes.runner_service_port
    try:
        status = await asyncio.to_thread(_status, account_id)
        if _restart_pending(status):
            return None
        if status.get("phase") in {"Draining", "DrainingLegacy"}:
            return None
        await asyncio.to_thread(_patch_wake, account_id)
    except client.ApiException as exc:
        logger.warning("wake patch failed account={}: {}", account_id, exc)
        return None

    deadline = time.monotonic() + float(s.kubernetes.wake_hold_seconds)
    while time.monotonic() < deadline:
        st = await asyncio.to_thread(_status, account_id)
        if _restart_pending(st):
            return None
        if st.get("phase") == "Running" and st.get("podIP"):
            return f"{st['podIP']}:{port}"
        await asyncio.sleep(0.5)
    return None


async def wake_and_wait(account_id: str) -> str | None:
    """Ensure the account's pod is awake; return the steer endpoint ``ip:port`` or None.

    Warm path trusts the operator-healed status.podIP but guards it with a fail-open
    liveness probe; on probe failure (dead/replaced pod) it falls through to a coalesced
    re-wake that returns the operator-healed fresh IP. None => the caller returns a
    fast-503 "waking, retry".
    """
    s = get_settings()
    port = s.kubernetes.runner_service_port

    # Warm path: trust status, but verify the pod is actually answering (#1/#2).
    st = await asyncio.to_thread(_status, account_id)
    if _restart_pending(st):
        return None
    if st.get("phase") in {"Draining", "DrainingLegacy"}:
        return None
    if st.get("phase") == "Running" and st.get("podIP"):
        candidate_ip = st["podIP"]
        if await _alive(candidate_ip, port):
            # The liveness request itself gives a concurrent idle timer enough
            # time to enter Draining. Re-read the routing authority before
            # returning the endpoint; otherwise a warm-path request can be sent
            # to a pod whose admission gate just closed.
            fresh = await asyncio.to_thread(_status, account_id)
            if _restart_pending(fresh):
                return None
            if (
                fresh.get("phase") == "Running"
                and fresh.get("podIP") == candidate_ip
            ):
                return f"{candidate_ip}:{port}"
            if fresh.get("phase") in {"Draining", "DrainingLegacy"}:
                return None
        logger.info("warm-path liveness probe failed account={}; re-waking", account_id)

    # Cold / dead path: coalesce concurrent wakes for the same account onto one Task.
    task = _wake_tasks.get(account_id)
    if task is None or task.done():
        task = asyncio.ensure_future(_drive_wake(account_id))
        _wake_tasks[account_id] = task

        # Clear our own entry when the wake finishes (success or failure) so the next cold
        # request starts a fresh wake. The guard avoids clobbering a newer task.
        def _cleanup(t: "asyncio.Task[str | None]", aid: str = account_id) -> None:
            if _wake_tasks.get(aid) is t:
                _wake_tasks.pop(aid, None)

        task.add_done_callback(_cleanup)

    # shield: a cancelled awaiter (e.g. the gateway dropped the stream) must not cancel the
    # shared wake out from under the other coalesced callers.
    return await asyncio.shield(task)


def _patch_terminal_wake(account_id: str) -> None:
    s = get_settings()
    ns = s.kubernetes.namespace_tenants
    _custom().patch_namespaced_custom_object(
        GROUP, VERSION, ns, PLURAL, account_id,
        {"spec": {"terminalWake": {"requestedAt": _now_iso()}}})


async def _drive_terminal_wake(account_id: str) -> str | None:
    s = get_settings()
    port = s.kubernetes.terminal_service_port
    try:
        terminal = (
            await asyncio.to_thread(_status, account_id)
        ).get("terminal") or {}
        if terminal.get("phase") in {
            "PendingRunnerRestart", "Draining", "DrainingLegacy",
        }:
            return None
        await asyncio.to_thread(_patch_terminal_wake, account_id)
    except client.ApiException as exc:
        logger.warning("terminal wake patch failed account={}: {}", account_id, exc)
        return None
    deadline = time.monotonic() + float(s.kubernetes.wake_hold_seconds)
    while time.monotonic() < deadline:
        terminal = (await asyncio.to_thread(_status, account_id)).get("terminal") or {}
        if terminal.get("phase") == "Running" and terminal.get("podIP"):
            return f"{terminal['podIP']}:{port}"
        await asyncio.sleep(0.5)
    return None


async def wake_terminal_and_wait(account_id: str) -> str | None:
    """Wake/resolve only the account's independent Terminal pod."""
    s = get_settings()
    port = s.kubernetes.terminal_service_port
    terminal = (await asyncio.to_thread(_status, account_id)).get("terminal") or {}
    # The Operator has intentionally deferred this allocation until the live Runner
    # restarts with the split cgroup budget. Do not patch terminalWake and spend the
    # full EPP hold polling a state that cannot converge yet.
    if terminal.get("phase") in {
        "PendingRunnerRestart", "Draining", "DrainingLegacy",
    }:
        return None
    if terminal.get("phase") == "Running" and terminal.get("podIP"):
        candidate_ip = terminal["podIP"]
        if await _alive(candidate_ip, port):
            fresh = (
                await asyncio.to_thread(_status, account_id)
            ).get("terminal") or {}
            if (
                fresh.get("phase") == "Running"
                and fresh.get("podIP") == candidate_ip
            ):
                return f"{candidate_ip}:{port}"
            if fresh.get("phase") in {
                "PendingRunnerRestart", "Draining", "DrainingLegacy",
            }:
                return None

    task = _terminal_wake_tasks.get(account_id)
    if task is None or task.done():
        task = asyncio.ensure_future(_drive_terminal_wake(account_id))
        _terminal_wake_tasks[account_id] = task

        def _cleanup(t: "asyncio.Task[str | None]", aid: str = account_id) -> None:
            if _terminal_wake_tasks.get(aid) is t:
                _terminal_wake_tasks.pop(aid, None)

        task.add_done_callback(_cleanup)
    return await asyncio.shield(task)


async def push_account_credentials(
    account_id: str, username: str, env: dict, *, wake_attempts: int = 6
) -> None:
    """Forward BYOK creds (the 6 ``ANTHROPIC_*`` keys) to the account's agent-runner
    so the pod persists them in its OWN ``settings.json`` — the single cred home.

    Wakes the pod first (``wake_and_wait`` drives the operator's 0->1 scale and
    returns the steer endpoint), then PUTs ``/api/sandbox/credentials`` directly to
    the pod, authorized by a freshly-minted runner token (the same trust primitive
    the EPP hands the gateway). control-panel→podIP is the same hop the fleet
    /health probes already use. Raises on failure so callers can log; the
    admin/setup callers treat it as best-effort — a slow cold pod must never fail
    user creation, and the user can always set creds via the SPA.
    """
    from priva_common.runner_token import mint

    endpoint = None
    for _ in range(max(1, wake_attempts)):
        endpoint = await wake_and_wait(account_id)
        if endpoint:
            break
        await asyncio.sleep(1.0)
    if not endpoint:
        raise RuntimeError(f"agent-runner for account {account_id} did not wake in time")

    # trust_env=False: internal pod hop must not route through a host proxy.
    async with httpx.AsyncClient(trust_env=False) as cx:
        r = await cx.put(
            f"http://{endpoint}/api/sandbox/credentials",
            json=env,
            headers={"X-Priva-Runner-Token": mint(account_id, username)},
            timeout=15.0,
        )
    r.raise_for_status()
    logger.info("pushed creds to agent-runner account={} keys={}", account_id, sorted(env.keys()))


def _mark_status_zero(account_id: str) -> None:
    """Flip the CR status not-routable (phase=Zero, podIP cleared, readyReplicas=0) via the
    status subresource, so the fleet view + EPP stop trusting the doomed pod the instant we
    scale it away. Mirrors the operator idle-sweep's ordering (``set_cr_status`` BEFORE the
    scale). 404 (CR gone) is a no-op. control-panel holds ``agenttenants/status`` patch RBAC."""
    s = get_settings()
    ns = s.kubernetes.namespace_tenants
    try:
        _custom().patch_namespaced_custom_object_status(
            GROUP, VERSION, ns, PLURAL, account_id,
            {"status": {"phase": "Zero", "podIP": None, "readyReplicas": 0}})
    except client.ApiException as exc:
        if exc.status != 404:
            raise


def shutdown_runner(account_id: str) -> int:
    """Admin: shut the account's runner down NOW — flip its CR status not-routable, then scale
    the Deployment to zero. Status-first (like the operator's idle sweep) shrinks the window
    where the EPP/fleet hand out a doomed endpoint. The operator is the sole scale-*up* path
    (spec.wake.requestedAt), so scaling to 0 here never fights it — the next user request
    re-wakes the pod. Returns the replica count that was running (0 if already asleep / no
    Deployment). Blocking kube calls — invoke via ``asyncio.to_thread``."""
    s = get_settings()
    ns = s.kubernetes.namespace_tenants
    name = f"ar-{account_id}"
    try:
        dep = _apps().read_namespaced_deployment(name, ns)
    except client.ApiException as exc:
        if exc.status == 404:
            return 0  # never provisioned / already torn down
        raise
    running = int(getattr(dep.spec, "replicas", 0) or 0)
    _mark_status_zero(account_id)
    try:
        _apps().patch_namespaced_deployment_scale(name, ns, {"spec": {"replicas": 0}})
    except client.ApiException as exc:
        if exc.status != 404:
            raise
    return running


def force_restart_pod(account_id: str) -> int:
    """Admin: force a converging, retry-safe restart of an awake runner.

    Deleting a Pod alone only recreates it from the Deployment's existing template.
    That is insufficient after an admin changes inherited resources or the
    Runner/Terminal allocation percentage: the replacement would still use the old
    template. Use the same safe lifecycle as a cold wake instead:

    ``status.restartPending`` is the durable state machine:

    * ``draining``: route-gated, scale to zero, wait for *every* selected Pod
      (including Terminating) to disappear;
    * ``waking``: zero boundary crossed; re-patch the marker's stable wake token
      and clear the marker only after that patch succeeds.

    Persisting ``waking`` before the wake closes both crash windows: a retry after
    scale-to-zero continues even though replicas is already 0, while a retry after
    wake/failed-clear never scales the replacement Pod down again.
    """
    s = get_settings()
    ns = s.kubernetes.namespace_tenants
    name = f"ar-{account_id}"
    pending = _restart_pending(_status(account_id))

    if pending is None:
        try:
            dep = _apps().read_namespaced_deployment(name, ns)
        except client.ApiException as exc:
            if exc.status == 404:
                return 0
            raise
        running = int(getattr(dep.spec, "replicas", 0) or 0)
        if running <= 0:
            return 0
        pending = {
            "requestedAt": _now_iso(),
            "stage": _RESTART_DRAINING,
            "oldPods": running,
        }
        # The marker and route gate must commit before touching the Deployment.
        if not _patch_restart_status(account_id, pending, route_gate=True):
            return 0

    stage = pending.get("stage")
    old_pods = max(0, int(pending.get("oldPods") or 0))
    requested_at = str(pending.get("requestedAt") or "")
    if not requested_at:
        raise RuntimeError(f"invalid restartPending marker for {account_id}: no requestedAt")

    if stage == _RESTART_DRAINING:
        try:
            _apps().patch_namespaced_deployment_scale(
                name, ns, {"spec": {"replicas": 0}})
        except client.ApiException as exc:
            if exc.status != 404:
                raise

        # Do not fire wake while any old Pod remains. Kubernetes keeps a
        # Terminating Pod in this list, so checking list emptiness (rather than
        # deletionTimestamp) proves a real zero boundary.
        selector = f"app=agent-runner,priva.io/account-id={account_id}"
        deadline = time.monotonic() + _RESTART_POD_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                remaining = _core().list_namespaced_pod(
                    ns, label_selector=selector).items
            except client.ApiException as exc:
                if exc.status == 404:
                    remaining = []
                else:
                    raise
            if not remaining:
                break
            time.sleep(_RESTART_POD_POLL_SECONDS)
        else:
            raise RuntimeError(
                f"timed out waiting for old agent-runner Pod to stop: {account_id}")

        # Checkpoint before issuing wake. If wake succeeds and the subsequent
        # clear fails, retries start here and cannot kill the replacement Pod.
        pending = {**pending, "stage": _RESTART_WAKING}
        if not _patch_restart_status(account_id, pending, route_gate=True):
            return old_pods
        stage = _RESTART_WAKING

    if stage != _RESTART_WAKING:
        raise RuntimeError(
            f"invalid restartPending stage for {account_id}: {stage!r}")

    _patch_wake(account_id, requested_at)
    _patch_restart_status(account_id, None, route_gate=False)
    return old_pods
