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

import httpx
from kubernetes import client, config

from priva_common.config import get_settings
from priva_common.logging import get_app_logger

logger = get_app_logger(__name__)

# Per-account in-flight wake Tasks (coalescing). Concurrent cold requests for one account
# await a single shared wake (one spec.wake patch, one poll loop) instead of each firing
# their own. In-process / per-replica only: prod runs N EPP replicas, so this dedupes
# within a replica — the operator's idempotent on_wake is the real cross-replica safety net.
_wake_tasks: dict[str, "asyncio.Task[str | None]"] = {}

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


def _core() -> "client.CoreV1Api":
    _load()
    return client.CoreV1Api()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_tenant(account_id: str, username: str, *, runner_type: str | None = None,
                  cpu: float | None = None, memory_mb: int | None = None,
                  storage_gb: int | None = None) -> None:
    """Create the AgentTenant CR for a new account (idempotent).

    The runner type + resource spec default to the cluster settings when omitted;
    the approval path passes the user-requested values so the CR is born correct
    (operator builds the right-sized pod, persistent scales to 1 in `ensure`)."""
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
            "agentRunnerType": runner_type or "auto_scale",
            "resources": {
                "cpu": float(cpu if cpu is not None else s.kubernetes.runner_cpu_cores),
                "memoryMb": int(memory_mb if memory_mb is not None else s.kubernetes.runner_memory_mb),
            },
            "storageGb": int(storage_gb if storage_gb is not None else s.kubernetes.runner_storage_gb),
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


def _patch_wake(account_id: str) -> None:
    """Patch the only scale-up trigger (spec.wake.requestedAt); the operator does the rest."""
    s = get_settings()
    ns = s.kubernetes.namespace_tenants
    _custom().patch_namespaced_custom_object(
        GROUP, VERSION, ns, PLURAL, account_id, {"spec": {"wake": {"requestedAt": _now_iso()}}})


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
        await asyncio.to_thread(_patch_wake, account_id)
    except client.ApiException as exc:
        logger.warning("wake patch failed account={}: {}", account_id, exc)
        return None

    deadline = time.monotonic() + float(s.kubernetes.wake_hold_seconds)
    while time.monotonic() < deadline:
        st = await asyncio.to_thread(_status, account_id)
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
    if st.get("phase") == "Running" and st.get("podIP"):
        if await _alive(st["podIP"], port):
            return f"{st['podIP']}:{port}"
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
