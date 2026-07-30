from __future__ import annotations

from types import SimpleNamespace

from priva_common.config import Settings
from priva_common.dataplane import EgressAllowEntryRecord, NetworkIsolationRecord
from priva_operator import kube


def _record() -> NetworkIsolationRecord:
    return NetworkIsolationRecord(
        runner_deny_internal=True,
        terminal_deny_internal=True,
        deny_tenant_peers=True,
        egress_mode="allowlist",
        egress_allowlist=[
            EgressAllowEntryRecord(host="api.example.com", port=443),
        ],
    )


class _ConfigMaps:
    def __init__(self) -> None:
        self.value = None

    def read_namespaced_config_map(self, _name, _namespace, **kwargs):
        assert kwargs["_request_timeout"] == kube._KUBE_REQUEST_TIMEOUT
        if self.value is None:
            raise kube.client.ApiException(status=404)
        return self.value

    def create_namespaced_config_map(self, _namespace, body):
        self.value = SimpleNamespace(
            data=dict(body["data"]),
            metadata=SimpleNamespace(
                resource_version="1",
                labels=dict(body["metadata"]["labels"]),
                annotations=dict(body["metadata"]["annotations"]),
            ),
        )

    def replace_namespaced_config_map(self, _name, _namespace, body):
        self.create_namespaced_config_map(_namespace, body)
        self.value.metadata.resource_version = "2"


def test_isolation_snapshot_round_trip_is_bound_to_topology(monkeypatch):
    api = _ConfigMaps()
    settings = Settings()
    record = _record()
    monkeypatch.setattr(kube, "core", lambda: api)

    assert kube.persist_isolation_snapshot("priva-cloud", record, settings)
    restored = kube.load_isolation_snapshot("priva-cloud", settings)
    assert restored is not None
    assert restored.model_dump() == record.model_dump()

    # A snapshot rendered for another pod/node topology must not be replayed
    # after an Operator configuration change.
    changed = Settings()
    changed.kubernetes.cluster_node_cidrs = ["203.0.113.0/24"]
    assert kube.load_isolation_snapshot("priva-cloud", changed) is None


def test_isolation_snapshot_rejects_data_or_intent_tampering(monkeypatch):
    api = _ConfigMaps()
    settings = Settings()
    monkeypatch.setattr(kube, "core", lambda: api)
    kube.persist_isolation_snapshot("priva-cloud", _record(), settings)

    api.value.data[kube.ISOLATION_SNAPSHOT_KEY] = '{"version":1,"record":{}}'
    assert kube.load_isolation_snapshot("priva-cloud", settings) is None
    api.value.data[kube.ISOLATION_SNAPSHOT_KEY] = "[]"
    assert kube.load_isolation_snapshot("priva-cloud", settings) is None


def test_snapshot_resource_gate_requires_every_boundary_layer(monkeypatch):
    settings = Settings()
    record = _record()
    monkeypatch.setattr(kube, "network_policy_enforced", lambda *_: True)
    monkeypatch.setattr(kube, "network_policies_ready", lambda *_: True)
    calls = []

    def proxy_ready(*_args, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(kube, "egress_proxy_ready", proxy_ready)
    assert kube.isolation_snapshot_resources_ready(
        "priva-cloud", record, settings
    )
    assert calls[-1]["require_all_replicas"] is False

    monkeypatch.setattr(kube, "network_policies_ready", lambda *_: False)
    assert not kube.isolation_snapshot_resources_ready(
        "priva-cloud", record, settings
    )
