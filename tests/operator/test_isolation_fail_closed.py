from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import grpc
import kopf
import pytest

from priva_common.config import Settings
from priva_operator import egress_proxy, kube, reconcile

_REAL_CNI_GATE = kube.network_policy_enforced
_REAL_PROXY_READY = kube.egress_proxy_ready


def _iso(mode="unrestricted"):
    return SimpleNamespace(
        runner_deny_internal=False,
        terminal_deny_internal=False,
        deny_tenant_peers=False,
        egress_mode=mode,
        egress_allowlist=[],
    )


def test_missing_first_isolation_snapshot_fails_closed(monkeypatch):
    monkeypatch.setattr(reconcile, "_network_isolation_cache", None)
    monkeypatch.setattr(reconcile, "_network_isolation_last_attempt", 0.0)
    monkeypatch.setattr(
        "priva_common.dataplane.get_client",
        lambda: (_ for _ in ()).throw(RuntimeError("data-spine down")),
    )
    monkeypatch.setattr(kube, "load_isolation_snapshot", lambda *_: None)

    with pytest.raises(RuntimeError, match="refusing tenant pod mutation"):
        reconcile._network_isolation(force=True)


def test_verified_persisted_snapshot_survives_operator_restart(monkeypatch):
    class DeadlineExceeded(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.DEADLINE_EXCEEDED

    settings = Settings()
    recovered = _iso("allowlist")
    calls = []
    monkeypatch.setattr(reconcile, "get_settings", lambda: settings)
    monkeypatch.setattr(reconcile, "_network_isolation_cache", None)
    monkeypatch.setattr(reconcile, "_network_isolation_last_attempt", 0.0)
    monkeypatch.setattr(reconcile, "_network_isolation_applied_intent", None)
    monkeypatch.setattr(reconcile, "_network_isolation_dirty", True)
    monkeypatch.setattr(
        "priva_common.dataplane.get_client",
        lambda: SimpleNamespace(
            network_isolation=SimpleNamespace(
                get=lambda: (_ for _ in ()).throw(DeadlineExceeded())
            )
        ),
    )
    monkeypatch.setattr(
        kube,
        "load_isolation_snapshot",
        lambda namespace, supplied: (
            calls.append(("load", namespace, supplied)),
            recovered,
        )[1],
    )
    monkeypatch.setattr(
        kube,
        "isolation_snapshot_resources_ready",
        lambda namespace, iso, supplied: (
            calls.append(("ready", namespace, iso, supplied)),
            True,
        )[1],
    )

    assert reconcile._network_isolation(force=True) is recovered
    assert reconcile._network_isolation_dirty is False
    assert reconcile._network_isolation_applied_intent == (
        reconcile.isolation_intent_digest(recovered, settings)
    )
    assert [call[0] for call in calls] == ["load", "ready"]


def test_unverified_persisted_snapshot_fails_closed(monkeypatch):
    settings = Settings()
    recovered = _iso("allowlist")
    monkeypatch.setattr(reconcile, "get_settings", lambda: settings)
    monkeypatch.setattr(reconcile, "_network_isolation_cache", None)
    monkeypatch.setattr(reconcile, "_network_isolation_last_attempt", 0.0)
    monkeypatch.setattr(
        "priva_common.dataplane.get_client",
        lambda: (_ for _ in ()).throw(RuntimeError("data-spine down")),
    )
    monkeypatch.setattr(
        kube, "load_isolation_snapshot", lambda *_: recovered
    )
    monkeypatch.setattr(
        kube, "isolation_snapshot_resources_ready", lambda *_: False
    )

    with pytest.raises(RuntimeError, match="no verified persisted boundary"):
        reconcile._network_isolation(force=True)


def test_cached_isolation_snapshot_survives_data_spine_outage(monkeypatch):
    cached = _iso("allowlist")
    monkeypatch.setattr(reconcile, "_network_isolation_cache", cached)
    monkeypatch.setattr(reconcile, "_network_isolation_last_attempt", 0.0)
    monkeypatch.setattr(
        "priva_common.dataplane.get_client",
        lambda: (_ for _ in ()).throw(RuntimeError("data-spine down")),
    )

    assert reconcile._network_isolation(force=True) is cached


def test_isolation_converge_serializes_snapshot_throttle_and_apply(
    monkeypatch, stub_logger,
):
    """A waiter cannot apply the old cache while a forced tighten is in flight.

    The observed lock exposes the second thread's acquisition attempt, so the
    interleaving is event-driven rather than dependent on sleeps or scheduling.
    """

    class ObservedLock:
        def __init__(self):
            self._lock = threading.Lock()
            self._meta_lock = threading.Lock()
            self._attempts = 0
            self.second_attempt = threading.Event()

        def __enter__(self):
            with self._meta_lock:
                self._attempts += 1
                if self._attempts == 2:
                    self.second_attempt.set()
            self._lock.acquire()
            return self

        def __exit__(self, *_exc):
            self._lock.release()

    old = _iso("unrestricted")
    tightened = _iso("allowlist")
    lock = ObservedLock()
    fetch_started = threading.Event()
    release_fetch = threading.Event()
    fetch_count = 0

    def fetch():
        nonlocal fetch_count
        fetch_count += 1
        fetch_started.set()
        assert release_fetch.wait(timeout=2)
        return tightened

    monkeypatch.setattr(
        "priva_common.dataplane.get_client",
        lambda: SimpleNamespace(
            network_isolation=SimpleNamespace(get=fetch),
        ),
    )
    monkeypatch.setattr(reconcile, "_network_isolation_cache", old)
    monkeypatch.setattr(reconcile, "_network_isolation_last_attempt", 0.0)
    monkeypatch.setattr(reconcile, "_network_policy_last_render", 0.0)
    monkeypatch.setattr(reconcile, "_network_isolation_converge_lock", lock)
    monkeypatch.setattr(reconcile, "get_settings", lambda: Settings())

    applied = []
    monkeypatch.setattr(
        kube,
        "ensure_isolation",
        lambda _namespace, **kwargs: applied.append(kwargs["iso"]),
    )
    persisted = []
    monkeypatch.setattr(
        kube,
        "persist_isolation_snapshot",
        lambda _namespace, iso, _settings: persisted.append(iso),
    )

    results = {}
    errors = []

    def run(key, *, force):
        try:
            results[key] = reconcile._render_network_policies(
                "ns", stub_logger, force=force
            )
        except BaseException as exc:  # surfaced in the main pytest thread below
            errors.append(exc)

    forced = threading.Thread(target=run, args=("forced",), kwargs={"force": True})
    waiting = threading.Thread(target=run, args=("waiting",), kwargs={"force": False})
    forced.start()
    assert fetch_started.wait(timeout=2)
    waiting.start()

    # The non-force path reaches the SAME lock while the forced fetch is held.
    # Release only after observing that attempt, making the overlap deterministic.
    saw_waiter = lock.second_attempt.wait(timeout=2)
    release_fetch.set()
    forced.join(timeout=2)
    waiting.join(timeout=2)

    assert saw_waiter
    assert not forced.is_alive()
    assert not waiting.is_alive()
    assert errors == []
    assert results["forced"] is tightened
    assert results["waiting"] is tightened
    assert fetch_count == 1
    assert applied == [tightened]
    assert persisted == [tightened]


def test_failed_isolation_apply_stays_dirty_and_bypasses_throttle(
    monkeypatch, stub_logger,
):
    tightened = _iso("allowlist")
    settings = Settings()
    monkeypatch.setattr(reconcile, "_network_isolation_cache", _iso("unrestricted"))
    monkeypatch.setattr(reconcile, "_network_isolation_last_attempt", 0.0)
    monkeypatch.setattr(reconcile, "_network_policy_last_render", 0.0)
    monkeypatch.setattr(reconcile, "_network_isolation_dirty", False)
    monkeypatch.setattr(
        reconcile,
        "_network_isolation_applied_intent",
        reconcile.isolation_intent_digest(_iso("unrestricted"), settings),
    )
    monkeypatch.setattr(reconcile, "get_settings", lambda: settings)
    monkeypatch.setattr(
        "priva_common.dataplane.get_client",
        lambda: SimpleNamespace(
            network_isolation=SimpleNamespace(get=lambda: tightened)
        ),
    )
    calls = []

    def apply(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("deployment update failed")

    monkeypatch.setattr(kube, "ensure_isolation", apply)
    monkeypatch.setattr(
        kube, "persist_isolation_snapshot", lambda *_args, **_kwargs: None
    )

    with pytest.raises(RuntimeError, match="deployment update failed"):
        reconcile._render_network_policies("ns", stub_logger, force=True)
    assert reconcile._network_isolation_dirty is True

    # The desired snapshot is cached and the fetch/render timestamps are fresh,
    # but a failed generation must be retried rather than admitted by throttle.
    assert (
        reconcile._render_network_policies("ns", stub_logger, force=False)
        is tightened
    )
    assert len(calls) == 2
    assert reconcile._network_isolation_dirty is False


def test_snapshot_persist_failure_does_not_advance_applied_generation(
    monkeypatch, stub_logger,
):
    tightened = _iso("allowlist")
    settings = Settings()
    old_intent = reconcile.isolation_intent_digest(
        _iso("unrestricted"), settings
    )
    monkeypatch.setattr(reconcile, "_network_isolation_cache", tightened)
    monkeypatch.setattr(reconcile, "_network_isolation_last_attempt", 0.0)
    monkeypatch.setattr(reconcile, "_network_policy_last_render", 0.0)
    monkeypatch.setattr(reconcile, "_network_isolation_dirty", True)
    monkeypatch.setattr(
        reconcile, "_network_isolation_applied_intent", old_intent
    )
    monkeypatch.setattr(reconcile, "get_settings", lambda: settings)
    monkeypatch.setattr(
        "priva_common.dataplane.get_client",
        lambda: SimpleNamespace(
            network_isolation=SimpleNamespace(get=lambda: tightened)
        ),
    )
    monkeypatch.setattr(kube, "ensure_isolation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        kube,
        "persist_isolation_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("snapshot write lost a race")
        ),
    )

    with pytest.raises(RuntimeError, match="snapshot write lost a race"):
        reconcile._render_network_policies("ns", stub_logger, force=True)
    assert reconcile._network_isolation_dirty is True
    assert reconcile._network_isolation_applied_intent == old_intent


def test_workload_gate_rejects_unverified_cni(monkeypatch, stub_logger):
    settings = Settings()
    monkeypatch.setattr(reconcile, "get_settings", lambda: settings)
    monkeypatch.setattr(reconcile, "_render_network_policies", lambda *a, **k: _iso())
    monkeypatch.setattr(kube, "network_policy_enforced", lambda *a: False)
    monkeypatch.setattr(
        kube,
        "wait_egress_proxy_ready",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("proxy readiness is irrelevant until CNI is verified")
        ),
    )

    with pytest.raises(kopf.TemporaryError, match="CNI ingress\\+egress"):
        reconcile._workload_isolation("ns", stub_logger, wait=True)


def test_workload_gate_rejects_an_unready_proxy(monkeypatch, stub_logger):
    settings = Settings()
    monkeypatch.setattr(reconcile, "get_settings", lambda: settings)
    monkeypatch.setattr(reconcile, "_render_network_policies", lambda *a, **k: _iso())
    monkeypatch.setattr(kube, "network_policy_enforced", lambda *a: True)
    monkeypatch.setattr(kube, "wait_egress_proxy_ready", lambda *a, **k: False)

    with pytest.raises(kopf.TemporaryError, match="egress proxy is not Ready"):
        reconcile._workload_isolation("ns", stub_logger, wait=True)


def test_network_failure_quiesce_closes_routes_and_scales_both_workloads(
    monkeypatch, patch_obj, stub_logger,
):
    calls = []
    monkeypatch.setattr(
        kube,
        "set_cr_status",
        lambda namespace, account_id, **fields: calls.append(
            ("status", namespace, account_id, fields)
        ),
    )
    monkeypatch.setattr(
        reconcile,
        "_force_close_account_admission",
        lambda namespace, account_id, *_: calls.append(
            ("admission", namespace, account_id)
        ),
    )
    monkeypatch.setattr(kube, "get_replicas", lambda *_: 1)
    monkeypatch.setattr(kube, "get_terminal_replicas", lambda *_: 1)
    monkeypatch.setattr(
        kube, "scale", lambda namespace, account_id, replicas: calls.append(
            ("runner", namespace, account_id, replicas)
        )
    )
    monkeypatch.setattr(
        kube,
        "scale_terminal",
        lambda namespace, account_id, replicas: calls.append(
            ("terminal", namespace, account_id, replicas)
        ),
    )

    reconcile._quiesce_for_network_failure(
        namespace="ns",
        account_id="acct-1",
        status={
            "phase": "Running",
            "podIP": "10.0.0.1",
            "readyReplicas": 1,
            "terminal": {
                "phase": "Running",
                "podIP": "10.0.0.2",
                "readyReplicas": 1,
            },
        },
        patch=patch_obj,
        logger=stub_logger,
        reason="CNI fact expired",
    )

    assert patch_obj.status["phase"] == "IsolationBlocked"
    assert patch_obj.status["podIP"] is None
    assert patch_obj.status["terminal"]["phase"] == "IsolationBlocked"
    assert any(call[0] == "status" for call in calls)
    assert ("admission", "ns", "acct-1") in calls
    assert ("runner", "ns", "acct-1", 0) in calls
    assert ("terminal", "ns", "acct-1", 0) in calls
    condition = next(
        item
        for item in patch_obj.status["conditions"]
        if item["type"] == "NetworkIsolationReady"
    )
    assert condition["status"] == "False"
    assert condition["message"] == "CNI fact expired"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (
            {
                "networkPolicyEnforced": "true",
                "networkPolicyIngressEnforced": "true",
                "networkPolicyEgressEnforced": "true",
                "networkPolicyProbeVersion": "3",
                "networkPolicyAddressFamily": "ipv4",
                "networkPolicyClusterUid": "cluster-1",
                "networkPolicyCheckedAt": datetime.now(timezone.utc).isoformat(),
            },
            True,
        ),
        (
            {
                "networkPolicyEnforced": "true",
                "networkPolicyIngressEnforced": "true",
                "networkPolicyEgressEnforced": "true",
            },
            False,
        ),
        (
            {
                "networkPolicyEnforced": "true",
                "networkPolicyIngressEnforced": "true",
            },
            False,
        ),
        (
            {
                "networkPolicyEnforced": "false",
                "networkPolicyIngressEnforced": "true",
                "networkPolicyEgressEnforced": "false",
            },
            False,
        ),
    ],
)
def test_cni_gate_requires_both_measured_directions(monkeypatch, data, expected):
    fake = SimpleNamespace(
        read_namespaced_config_map=lambda *a, **_k: SimpleNamespace(data=data),
        read_namespace=lambda *_a, **_k: SimpleNamespace(
            metadata=SimpleNamespace(uid="cluster-1")
        ),
    )
    monkeypatch.setattr(kube, "core", lambda: fake)
    assert _REAL_CNI_GATE("ns", Settings()) is expected


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        (
            {
                "networkPolicyCheckedAt": (
                    datetime.now(timezone.utc) - timedelta(days=8)
                ).isoformat()
            },
            False,
        ),
        (
            {
                "networkPolicyCheckedAt": (
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat()
            },
            False,
        ),
        ({"networkPolicyAddressFamily": "dual-stack"}, False),
        ({"networkPolicyClusterUid": "old-cluster"}, False),
    ],
)
def test_cni_gate_rejects_stale_future_or_wrong_cluster_facts(
    monkeypatch, override, expected,
):
    data = {
        "networkPolicyEnforced": "true",
        "networkPolicyIngressEnforced": "true",
        "networkPolicyEgressEnforced": "true",
        "networkPolicyProbeVersion": "3",
        "networkPolicyAddressFamily": "ipv4",
        "networkPolicyClusterUid": "cluster-1",
        "networkPolicyCheckedAt": datetime.now(timezone.utc).isoformat(),
        **override,
    }
    fake = SimpleNamespace(
        read_namespaced_config_map=lambda *_a, **_k: SimpleNamespace(data=data),
        read_namespace=lambda *_a, **_k: SimpleNamespace(
            metadata=SimpleNamespace(uid="cluster-1")
        ),
    )
    monkeypatch.setattr(kube, "core", lambda: fake)
    assert _REAL_CNI_GATE("ns", Settings()) is expected


def test_egress_proxy_uses_the_supplied_snapshot_without_rereading(monkeypatch):
    settings = Settings()
    iso = _iso("deny_all")
    created = []

    class Apps:
        def read_namespaced_deployment(self, *_args, **_kwargs):
            raise kube.client.ApiException(status=404)

        def create_namespaced_deployment(self, namespace, body):
            created.append((namespace, body))

    monkeypatch.setattr(kube, "apps", Apps)
    monkeypatch.setattr(kube, "_apply_cm", lambda *a: True)
    monkeypatch.setattr(
        kube, "_verified_config_map_revision", lambda *a: "revision-1"
    )
    monkeypatch.setattr(kube, "_apply_service", lambda *a: True)
    monkeypatch.setattr(
        "priva_common.dataplane.get_client",
        lambda: (_ for _ in ()).throw(
            AssertionError("a supplied isolation snapshot must not be re-read")
        ),
    )

    assert kube.ensure_egress_proxy(
        "ns", strict=True, iso=iso, settings=settings) is True
    assert created[0][1]["metadata"]["name"] == egress_proxy.NAME
    conf = egress_proxy.render_squid_conf(iso, settings)
    assert "http_access allow all" not in conf


def test_proxy_readiness_requires_the_observed_rollout(monkeypatch):
    deployment = SimpleNamespace(
        metadata=SimpleNamespace(generation=4),
        spec=SimpleNamespace(replicas=2),
        status=SimpleNamespace(
            observed_generation=3,
            updated_replicas=2,
            ready_replicas=2,
            available_replicas=2,
        ),
    )
    fake = SimpleNamespace(
        read_namespaced_deployment=lambda *a, **_k: deployment
    )
    monkeypatch.setattr(kube, "apps", lambda: fake)
    assert _REAL_PROXY_READY("ns") is False
    deployment.status.observed_generation = 4
    assert _REAL_PROXY_READY("ns") is True
    deployment.status.updated_replicas = 1
    deployment.status.ready_replicas = 1
    deployment.status.available_replicas = 1
    assert _REAL_PROXY_READY("ns") is False
    assert _REAL_PROXY_READY("ns", require_all_replicas=False) is True


def test_proxy_service_exposure_drift_is_closed_without_replacing_cluster_ip(
    monkeypatch,
):
    existing = kube.client.V1Service(
        metadata=kube.client.V1ObjectMeta(
            name=egress_proxy.NAME,
            labels={
                "app": "egress-proxy",
                "app.kubernetes.io/managed-by": "priva-operator",
            },
        ),
        spec=kube.client.V1ServiceSpec(
            cluster_ip="10.96.0.55",
            type="LoadBalancer",
            external_ips=["203.0.113.10"],
            selector={"app": "egress-proxy"},
            ports=[kube.client.V1ServicePort(
                name="proxy",
                port=9999,
                target_port=9999,
                protocol="TCP",
                node_port=32000,
            )],
        ),
    )
    patches = []
    fake = SimpleNamespace(
        read_namespaced_service=lambda *a, **_k: existing,
        patch_namespaced_service=lambda *a, **kw: patches.append((a, kw)),
    )
    monkeypatch.setattr(kube, "core", lambda: fake)

    body = egress_proxy.service_body("ns", Settings())
    assert kube._apply_service("ns", body) is True
    args, kwargs = patches[0]
    spec = args[2]["spec"]
    assert spec["type"] == "ClusterIP"
    assert spec["externalIPs"] == []
    assert spec["ports"] == [{
        "port": 3128,
        "targetPort": 3128,
        "name": "proxy",
        "protocol": "TCP",
        "nodePort": None,
    }]
    assert "clusterIP" not in spec
    assert kwargs["_content_type"] == "application/merge-patch+json"
