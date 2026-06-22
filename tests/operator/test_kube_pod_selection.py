"""kube.current_ready_pod_ip (#5): the IP of the one Ready, non-terminating pod for an
account, else None. Pure pod query (never consults status.phase). The kube client is
faked, so no cluster is touched."""

from __future__ import annotations

from types import SimpleNamespace

import priva_operator.kube as kube


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

    def list_namespaced_pod(self, namespace, label_selector=None):
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
