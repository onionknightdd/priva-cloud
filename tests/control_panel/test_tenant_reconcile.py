from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from priva_control_panel import provisioner as P


def _settings():
    return SimpleNamespace(kubernetes=SimpleNamespace(
        namespace_tenants="tenants", max_concurrent_sessions=3))


def _defaults() -> dict:
    return {
        "idleGraceSeconds": 1800,
        "minAliveAfterWakeSeconds": 1800,
        "cpuCores": 1.0,
        "memoryMb": 2048,
        "storageGb": 10,
        "runnerImage": "runner:test",
        "terminal": {
            "resourcePercent": 25,
            "maxSessions": 2,
            "idleTimeoutSeconds": 1800,
            "maxLifetimeSeconds": 14400,
            "scaleDownGraceSeconds": 120,
        },
    }


class _ExistingCustom:
    def __init__(self, spec):
        self.spec = spec
        self.patches = []

    def create_namespaced_custom_object(self, *args):
        raise P.client.ApiException(status=409)

    def get_namespaced_custom_object(self, *args):
        return {"spec": self.spec}

    def patch_namespaced_custom_object(self, *args):
        self.patches.append(args[-1])


def test_ensure_tenant_repairs_identity_after_already_exists(monkeypatch):
    custom = _ExistingCustom({
        "accountId": "acct-1",
        "resources": {"cpu": 4},
        "desiredState": "offboarding",
    })
    monkeypatch.setattr(P, "get_settings", _settings)
    monkeypatch.setattr(P, "_custom", lambda: custom)

    P.ensure_tenant("acct-1", "alice", runtime_defaults=_defaults())

    assert custom.patches == [{"spec": {
        "accountId": "acct-1",
        "username": "alice",
        "runtimeDefaults": _defaults(),
    }}]
    # The selective patch must not reset lifecycle or resource overrides.
    assert "desiredState" not in custom.patches[0]["spec"]
    assert "resources" not in custom.patches[0]["spec"]


def test_ensure_tenant_does_not_patch_complete_object(monkeypatch):
    spec = {"accountId": "acct-1", "username": "alice", "runtimeDefaults": _defaults()}
    custom = _ExistingCustom(spec)
    monkeypatch.setattr(P, "get_settings", _settings)
    monkeypatch.setattr(P, "_custom", lambda: custom)

    P.ensure_tenant("acct-1", "alice", runtime_defaults=_defaults())

    assert custom.patches == []


def test_periodic_sync_recreates_deleted_cr_with_username(monkeypatch):
    import priva_common.dataplane as dataplane
    import priva_common.user_store as user_store

    defaults = SimpleNamespace(
        idle_grace_seconds=1800, min_alive_after_wake_seconds=1800,
        cpu_cores=1.0, memory_mb=2048, storage_gb=10, runner_image="runner:test",
        terminal_resource_percent=25, terminal_max_sessions=2,
        terminal_idle_timeout_seconds=1800, terminal_max_lifetime_seconds=14400,
        terminal_scale_down_grace_seconds=120,
    )
    dp = SimpleNamespace(
        runner_defaults=SimpleNamespace(get=lambda: defaults),
        resource_specs=SimpleNamespace(list=lambda: []),
    )
    store = SimpleNamespace(list_users=lambda: [SimpleNamespace(
        account_id="acct-1", username="alice", status="active",
        agent_runner_type="auto_scale",
    )])
    created = []
    monkeypatch.setattr(dataplane, "get_client", lambda: dp)
    monkeypatch.setattr(user_store, "get_user_store", lambda: store)
    monkeypatch.setattr(P, "list_tenants", lambda: [])
    monkeypatch.setattr(P, "ensure_tenant", lambda *a, **k: created.append((a, k)))

    result = P.sync_all_tenants()

    assert result["created"] == 1
    assert created[0][0] == ("acct-1", "alice")
    assert created[0][1]["runtime_defaults"]["runnerImage"] == "runner:test"


def test_crd_requires_username_in_raw_and_helm_manifests():
    root = Path(__file__).resolve().parents[2]
    for path in (
        root / "deploy/crds/agenttenant.yaml",
        root / "deploy/helm/priva-cloud/templates/crd-agenttenant.yaml",
    ):
        text = path.read_text()
        assert "required: [accountId, username]" in text
        assert "rule: self.accountId == oldSelf.accountId" in text
