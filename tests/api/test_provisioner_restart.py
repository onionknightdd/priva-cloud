"""The admin restart must cross a real zero boundary before operator wake.

Deleting a Pod under an unchanged Deployment silently preserves a stale resource
template, which prevents a newly enabled Terminal allocation from taking effect.
"""

from __future__ import annotations

from types import SimpleNamespace

from priva_control_panel import provisioner as P


class _Apps:
    def __init__(self, replicas: int, events: list[str]):
        self.replicas = replicas
        self.events = events

    def read_namespaced_deployment(self, name, namespace):
        self.events.append("read")
        return SimpleNamespace(spec=SimpleNamespace(replicas=self.replicas))

    def patch_namespaced_deployment_scale(self, name, namespace, body):
        assert body == {"spec": {"replicas": 0}}
        self.events.append("scale-zero")


class _Core:
    def __init__(self, pod_lists, events: list[str]):
        self.pod_lists = iter(pod_lists)
        self.events = events

    def list_namespaced_pod(self, namespace, label_selector):
        self.events.append("list-pods")
        return SimpleNamespace(items=next(self.pod_lists))


class _Custom:
    def __init__(self, events: list[str]):
        self.events = events
        self.body = None

    def patch_namespaced_custom_object(self, group, version, namespace, plural, name, body):
        self.events.append("wake")
        self.body = body


def _settings():
    return SimpleNamespace(kubernetes=SimpleNamespace(namespace_tenants="tenants"))


def test_force_restart_scales_to_zero_then_requests_operator_wake(monkeypatch):
    events: list[str] = []
    pod = SimpleNamespace(metadata=SimpleNamespace(deletion_timestamp=None))
    apps = _Apps(1, events)
    core = _Core([[pod], []], events)
    custom = _Custom(events)

    monkeypatch.setattr(P, "get_settings", _settings)
    monkeypatch.setattr(P, "_apps", lambda: apps)
    monkeypatch.setattr(P, "_core", lambda: core)
    monkeypatch.setattr(P, "_custom", lambda: custom)
    monkeypatch.setattr(P, "_mark_status_zero", lambda account_id: events.append("status-zero"))

    restarted = P.force_restart_pod("acct")

    assert restarted == 1
    assert events == [
        "read", "list-pods", "status-zero", "scale-zero", "list-pods", "wake"]
    assert custom.body["spec"]["wake"]["requestedAt"]


def test_force_restart_is_noop_for_dormant_runner(monkeypatch):
    events: list[str] = []
    apps = _Apps(0, events)

    monkeypatch.setattr(P, "get_settings", _settings)
    monkeypatch.setattr(P, "_apps", lambda: apps)
    monkeypatch.setattr(
        P, "_core", lambda: (_ for _ in ()).throw(AssertionError("core must not be called")))

    assert P.force_restart_pod("acct") == 0
    assert events == ["read"]
