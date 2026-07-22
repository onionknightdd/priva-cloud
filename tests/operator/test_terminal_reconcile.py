from __future__ import annotations

import time
from types import SimpleNamespace

import priva_operator.reconcile as reconcile


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _settings():
    return SimpleNamespace(kubernetes=SimpleNamespace(
        terminal_service_port=8092,
        terminal_scale_down_grace_seconds=120,
        runner_image_pull_policy="IfNotPresent",
    ))


def _defaults():
    return SimpleNamespace(
        terminal_resource_percent=25,
        terminal_scale_down_grace_seconds=120,
    )


def _run(monkeypatch, patch_obj, stub_logger, *, terminal_replicas, runner_replicas,
         applied_percent, current=None, health=None):
    monkeypatch.setattr(reconcile, "get_settings", _settings)
    monkeypatch.setattr(reconcile, "_runner_defaults", lambda spec=None: _defaults())
    monkeypatch.setattr(
        reconcile.kube, "resolve_terminal_percent", lambda settings, defaults: 25)
    monkeypatch.setattr(
        reconcile.kube, "get_terminal_replicas", lambda namespace, account_id: terminal_replicas)
    monkeypatch.setattr(
        reconcile.kube, "get_replicas", lambda namespace, account_id: runner_replicas)
    monkeypatch.setattr(
        reconcile.kube, "applied_terminal_percent", lambda namespace, account_id: applied_percent)
    monkeypatch.setattr(reconcile.kube, "allocation_hash", lambda *args, **kwargs: "desired")
    monkeypatch.setattr(
        reconcile.kube, "applied_allocation_hash",
        lambda namespace, account_id: "desired" if applied_percent == 25 else "old",
    )
    monkeypatch.setattr(
        reconcile.kube, "current_ready_terminal_pod_ip",
        lambda namespace, account_id: "10.0.0.8" if terminal_replicas == 1 else None,
    )
    monkeypatch.setattr(reconcile.kube, "set_cr_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(reconcile.kube, "scale_terminal", lambda *args, **kwargs: None)

    if health is not None:
        monkeypatch.setattr(
            reconcile.httpx, "get", lambda *args, **kwargs: _Response(health))

    reconcile.reconcile_terminal(
        spec={"accountId": "acct-1", "username": "alice", "desiredState": "active"},
        name="acct-1",
        namespace="tenants",
        uid="uid-1",
        status={"terminal": current or {}},
        patch=patch_obj,
        logger=stub_logger,
    )


def test_pending_restart_is_stable_while_runner_is_live(
    monkeypatch, patch_obj, stub_logger,
):
    _run(
        monkeypatch,
        patch_obj,
        stub_logger,
        terminal_replicas=0,
        runner_replicas=1,
        applied_percent=0,
        current={"phase": "PendingRunnerRestart", "resourcePercent": 0},
    )
    assert patch_obj.status["terminal"]["phase"] == "PendingRunnerRestart"
    assert patch_obj.status["terminal"]["resourcePercent"] == 0


def test_dormant_terminal_returns_to_zero_after_runner_converges(
    monkeypatch, patch_obj, stub_logger,
):
    _run(
        monkeypatch,
        patch_obj,
        stub_logger,
        terminal_replicas=0,
        runner_replicas=1,
        applied_percent=25,
        current={"phase": "PendingRunnerRestart", "resourcePercent": 0},
    )
    assert patch_obj.status["terminal"]["phase"] == "Zero"
    assert patch_obj.status["terminal"]["resourcePercent"] == 25


def test_running_terminal_reports_the_applied_allocation(
    monkeypatch, patch_obj, stub_logger,
):
    _run(
        monkeypatch,
        patch_obj,
        stub_logger,
        terminal_replicas=1,
        runner_replicas=1,
        applied_percent=20,
        health={"active_sessions": 1, "last_activity_ts": time.time()},
    )
    status = patch_obj.status["terminal"]
    assert status["phase"] == "Running"
    assert status["resourcePercent"] == 20
    assert status["activeSessions"] == 1


def test_idle_terminal_drains_revision_before_scale(monkeypatch, patch_obj, stub_logger):
    calls = []
    monkeypatch.setattr(reconcile, "get_settings", _settings)
    monkeypatch.setattr(reconcile, "_runner_defaults", lambda spec=None: _defaults())
    monkeypatch.setattr(reconcile.kube, "resolve_terminal_percent", lambda *a: 25)
    monkeypatch.setattr(reconcile.kube, "allocation_hash", lambda *a, **k: "desired")
    monkeypatch.setattr(reconcile.kube, "applied_allocation_hash", lambda *a: "desired")
    monkeypatch.setattr(reconcile.kube, "applied_terminal_percent", lambda *a: 25)
    monkeypatch.setattr(reconcile.kube, "get_replicas", lambda *a: 1)
    monkeypatch.setattr(reconcile.kube, "get_terminal_replicas", lambda *a: 1)
    monkeypatch.setattr(reconcile.kube, "current_ready_terminal_pod_ip", lambda *a: "10.0.0.8")
    monkeypatch.setattr(
        reconcile.httpx, "get",
        lambda *a, **k: _Response({
            "active_sessions": 0, "last_activity_ts": 0.0, "session_revision": 7}),
    )
    monkeypatch.setattr(
        reconcile, "_begin_terminal_drain",
        lambda ip, port, revision, logger: calls.append(("drain", revision)) or True,
    )
    monkeypatch.setattr(
        reconcile.kube, "set_cr_status",
        lambda *a, **k: calls.append(("status", k["terminal"]["phase"])),
    )
    monkeypatch.setattr(
        reconcile.kube, "scale_terminal",
        lambda *a: calls.append(("scale", a[-1])),
    )

    reconcile.reconcile_terminal(
        spec={"accountId": "acct-1", "username": "alice", "desiredState": "active"},
        name="acct-1", namespace="tenants", uid="uid-1", status={"terminal": {}},
        patch=patch_obj, logger=stub_logger,
    )

    assert calls == [("drain", 7), ("status", "Draining"), ("scale", 0)]
    assert patch_obj.status["terminal"]["phase"] == "Zero"


def _run_legacy_idle(monkeypatch, patch_obj, stub_logger, *, phase):
    calls = []
    monkeypatch.setattr(reconcile, "get_settings", _settings)
    monkeypatch.setattr(reconcile, "_runner_defaults", lambda spec=None: _defaults())
    monkeypatch.setattr(reconcile.kube, "resolve_terminal_percent", lambda *a: 25)
    monkeypatch.setattr(reconcile.kube, "allocation_hash", lambda *a, **k: "desired")
    monkeypatch.setattr(reconcile.kube, "applied_allocation_hash", lambda *a: "desired")
    monkeypatch.setattr(reconcile.kube, "applied_terminal_percent", lambda *a: 25)
    monkeypatch.setattr(reconcile.kube, "get_replicas", lambda *a: 1)
    monkeypatch.setattr(reconcile.kube, "get_terminal_replicas", lambda *a: 1)
    monkeypatch.setattr(reconcile.kube, "current_ready_terminal_pod_ip", lambda *a: "10.0.0.8")
    monkeypatch.setattr(
        reconcile.httpx, "get",
        lambda *a, **k: _Response({"active_sessions": 0, "last_activity_ts": 0.0}),
    )
    monkeypatch.setattr(
        reconcile, "_begin_terminal_drain",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("legacy terminald has no revision drain endpoint")),
    )
    monkeypatch.setattr(
        reconcile.kube, "set_cr_status",
        lambda *a, **k: calls.append(("status", k["terminal"]["phase"])),
    )
    monkeypatch.setattr(
        reconcile.kube, "scale_terminal",
        lambda *a: calls.append(("scale", a[-1])),
    )

    reconcile.reconcile_terminal(
        spec={"accountId": "acct-1", "username": "alice", "desiredState": "active"},
        name="acct-1", namespace="tenants", uid="uid-1",
        status={"terminal": {"phase": phase} if phase else {}},
        patch=patch_obj, logger=stub_logger,
    )
    return calls


def test_legacy_terminal_closes_admission_one_tick_before_scale(
    monkeypatch, patch_obj, stub_logger,
):
    calls = _run_legacy_idle(
        monkeypatch, patch_obj, stub_logger, phase=None)

    assert calls == [("status", "DrainingLegacy")]
    assert patch_obj.status["terminal"]["phase"] == "DrainingLegacy"


def test_legacy_terminal_scales_only_after_draining_recheck(
    monkeypatch, patch_obj, stub_logger,
):
    calls = _run_legacy_idle(
        monkeypatch, patch_obj, stub_logger, phase="DrainingLegacy")

    assert calls == [("scale", 0)]
    assert patch_obj.status["terminal"]["phase"] == "Zero"
