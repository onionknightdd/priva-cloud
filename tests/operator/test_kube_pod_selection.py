"""kube.current_ready_pod_ip (#5): the IP of the one Ready, non-terminating pod for an
account, else None. Pure pod query (never consults status.phase). The kube client is
faked, so no cluster is touched."""

from __future__ import annotations

from types import SimpleNamespace

import priva_operator.kube as kube

_REAL_AGENTTENANT_TEARDOWN_STARTED = kube.agenttenant_teardown_started


def _cond(ready: bool):
    return SimpleNamespace(type="Ready", status="True" if ready else "False")


def _pod(ip, *, ready=True, terminating=False):
    return SimpleNamespace(
        metadata=SimpleNamespace(
            deletion_timestamp="2026-06-22T00:00:00Z" if terminating else None),
        status=SimpleNamespace(conditions=[_cond(ready)], pod_ip=ip),
    )


class _FakeCore:
    def __init__(self, pods):
        self._pods = pods

    def list_namespaced_pod(self, namespace, label_selector=None, **_kwargs):
        return SimpleNamespace(items=list(self._pods))


def _patch_core(monkeypatch, pods):
    monkeypatch.setattr(kube, "core", lambda: _FakeCore(pods))


def test_returns_ready_non_terminating_ip(monkeypatch):
    _patch_core(monkeypatch, [_pod("10.0.0.5")])
    assert kube.current_ready_pod_ip("ns", "acct") == "10.0.0.5"


def test_skips_terminating_pod(monkeypatch):
    # A terminating pod's IP is about to vanish — it must never be handed out (#5).
    _patch_core(monkeypatch, [_pod("10.0.0.5", terminating=True)])
    assert kube.current_ready_pod_ip("ns", "acct") is None


def test_skips_not_ready_pod(monkeypatch):
    _patch_core(monkeypatch, [_pod("10.0.0.5", ready=False)])
    assert kube.current_ready_pod_ip("ns", "acct") is None


def test_prefers_live_pod_over_terminating(monkeypatch):
    # Recreate rollover: an old terminating pod alongside a fresh Ready one — pick live.
    _patch_core(monkeypatch, [_pod("10.0.0.5", terminating=True), _pod("10.0.0.9")])
    assert kube.current_ready_pod_ip("ns", "acct") == "10.0.0.9"


def test_ready_without_ip_is_none(monkeypatch):
    _patch_core(monkeypatch, [_pod(None)])
    assert kube.current_ready_pod_ip("ns", "acct") is None


def test_no_pods_is_none(monkeypatch):
    _patch_core(monkeypatch, [])
    assert kube.current_ready_pod_ip("ns", "acct") is None


class _SelectorCore:
    def __init__(self, by_app):
        self._by_app = by_app

    def list_namespaced_pod(self, namespace, label_selector=None, **kwargs):
        app = "terminal" if "app=terminal" in label_selector else "agent-runner"
        return SimpleNamespace(items=list(self._by_app.get(app, [])))


def test_workload_pods_gone_counts_terminating_pods(monkeypatch):
    monkeypatch.setattr(
        kube,
        "core",
        lambda: _SelectorCore({
            "agent-runner": [_pod("10.0.0.5", terminating=True)],
        }),
    )

    assert kube.workload_pods_gone("ns", "acct", "agent-runner") is False


def test_account_workload_pods_gone_requires_runner_and_terminal_empty(monkeypatch):
    state = {
        "agent-runner": [],
        "terminal": [_pod("10.0.0.8", ready=False, terminating=True)],
    }
    monkeypatch.setattr(kube, "core", lambda: _SelectorCore(state))

    assert kube.account_workload_pods_gone("ns", "acct") is False
    state["terminal"] = []
    assert kube.account_workload_pods_gone("ns", "acct") is True


def test_live_workload_guard_requires_same_active_cr(monkeypatch):
    state = {
        "metadata": {"uid": "uid-1"},
        "spec": {"desiredState": "active"},
    }

    class _Custom:
        def get_namespaced_custom_object(self, *_args):
            return state

    monkeypatch.setattr(kube, "custom", lambda: _Custom())

    guard = _REAL_AGENTTENANT_TEARDOWN_STARTED
    assert guard("ns", "acct", "uid-1") is False
    state["spec"]["desiredState"] = "offboarding"
    assert guard("ns", "acct", "uid-1") is True
    state["spec"]["desiredState"] = "active"
    state["metadata"]["uid"] = "uid-2"
    assert guard("ns", "acct", "uid-1") is True
    state["metadata"]["uid"] = "uid-1"
    state["metadata"]["deletionTimestamp"] = "2026-07-29T00:00:00Z"
    assert guard("ns", "acct", "uid-1") is True
