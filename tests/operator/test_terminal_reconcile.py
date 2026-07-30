from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

import priva_operator.reconcile as reconcile
from priva_common import drain_token
from priva_common.config import Settings


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _settings():
    settings = Settings()
    settings.kubernetes.wake_timeout_seconds = 1
    return settings


def _defaults():
    return SimpleNamespace(
        terminal_resource_percent=25,
        terminal_scale_down_grace_seconds=120,
    )


def _run(monkeypatch, patch_obj, stub_logger, *, terminal_replicas, runner_replicas,
         applied_percent, current=None, health=None, pods_gone=True,
         template_stale=False, egress_stale=False, health_requests=None):
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
        reconcile.kube, "terminal_template_hash",
        lambda *args, **kwargs: "desired-terminal-template",
    )
    monkeypatch.setattr(
        reconcile.kube, "applied_allocation_hash",
        lambda namespace, account_id: "desired" if applied_percent == 25 else "old",
    )
    monkeypatch.setattr(
        reconcile.kube,
        "applied_terminal_template_hash",
        lambda namespace, account_id: (
            "old-terminal-template"
            if template_stale
            else "desired-terminal-template"
        ),
    )
    monkeypatch.setattr(
        reconcile.kube,
        "applied_terminal_egress_generation",
        lambda *_args: "old-egress" if egress_stale else "e1:test",
    )
    monkeypatch.setattr(
        reconcile.kube, "current_ready_terminal_pod_ip",
        lambda namespace, account_id: "10.0.0.8" if terminal_replicas == 1 else None,
    )
    monkeypatch.setattr(reconcile.kube, "set_cr_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(reconcile.kube, "scale_terminal", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        reconcile.kube,
        "applied_terminal_drain_token",
        lambda *_args: "terminal-capability",
    )
    monkeypatch.setattr(
        reconcile.kube, "workload_pods_gone", lambda *args: pods_gone)

    if health is not None:
        def get_health(*args, **kwargs):
            if health_requests is not None:
                health_requests.append((args, kwargs))
            return _Response(health)

        monkeypatch.setattr(
            reconcile.httpx, "get", get_health)
        monkeypatch.setattr(
            reconcile, "_begin_terminal_drain", lambda *args, **kwargs: True)

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


def test_terminal_tail_patch_loss_recovers_zero_after_pod_exit(
    monkeypatch, patch_obj, stub_logger,
):
    _run(
        monkeypatch,
        patch_obj,
        stub_logger,
        terminal_replicas=0,
        runner_replicas=1,
        applied_percent=25,
        current={"phase": "Draining", "podIP": "10.0.0.8", "readyReplicas": 1},
    )

    terminal = patch_obj.status["terminal"]
    assert terminal["phase"] == "Zero"
    assert terminal["podIP"] is None
    assert terminal["readyReplicas"] == 0


def test_terminal_zero_keeps_draining_while_old_pod_is_terminating(
    monkeypatch, patch_obj, stub_logger,
):
    _run(
        monkeypatch,
        patch_obj,
        stub_logger,
        terminal_replicas=0,
        runner_replicas=1,
        applied_percent=25,
        current={"phase": "Draining", "podIP": "10.0.0.8"},
        pods_gone=False,
    )

    assert "terminal" not in patch_obj.status


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


def test_terminal_health_uses_only_the_pod_capability(
    monkeypatch, patch_obj, stub_logger,
):
    requests = []
    _run(
        monkeypatch,
        patch_obj,
        stub_logger,
        terminal_replicas=1,
        runner_replicas=1,
        applied_percent=25,
        health={"active_sessions": 1, "last_activity_ts": time.time()},
        health_requests=requests,
    )

    assert len(requests) == 1
    args, kwargs = requests[0]
    assert args == ("http://10.0.0.8:8092/health",)
    assert kwargs["headers"] == {
        drain_token.HEADER: "terminal-capability",
    }
    assert kwargs["timeout"] == 2.0
    assert kwargs["trust_env"] is False


def test_stale_terminal_security_template_drains_without_waiting_for_idle_grace(
    monkeypatch, patch_obj, stub_logger,
):
    _run(
        monkeypatch,
        patch_obj,
        stub_logger,
        terminal_replicas=1,
        runner_replicas=1,
        applied_percent=25,
        current={"phase": "Running", "podIP": "10.0.0.8"},
        health={
            "active_sessions": 0,
            "last_activity_ts": time.time(),
            "session_revision": 7,
        },
        template_stale=True,
    )

    assert patch_obj.status["terminal"]["phase"] == "Draining"


def test_stale_terminal_egress_has_an_explicit_pending_condition(
    monkeypatch, patch_obj, stub_logger,
):
    _run(
        monkeypatch,
        patch_obj,
        stub_logger,
        terminal_replicas=1,
        runner_replicas=1,
        applied_percent=25,
        current={"phase": "Running", "podIP": "10.0.0.8"},
        health={
            "active_sessions": 1,
            "last_activity_ts": time.time(),
            "session_revision": 3,
        },
        egress_stale=True,
    )

    condition = next(
        item
        for item in patch_obj.status["conditions"]
        if item["type"] == "TerminalEgressReady"
    )
    assert condition["status"] == "False"
    assert condition["reason"] == "PendingTerminalRestart"
    assert patch_obj.status["terminal"]["phase"] == "Running"


def test_idle_terminal_drains_revision_before_scale(monkeypatch, patch_obj, stub_logger):
    calls = []
    monkeypatch.setattr(reconcile, "get_settings", _settings)
    monkeypatch.setattr(reconcile, "_runner_defaults", lambda spec=None: _defaults())
    monkeypatch.setattr(reconcile.kube, "resolve_terminal_percent", lambda *a: 25)
    monkeypatch.setattr(reconcile.kube, "allocation_hash", lambda *a, **k: "desired")
    monkeypatch.setattr(
        reconcile.kube, "terminal_template_hash", lambda *a, **k: "terminal-template")
    monkeypatch.setattr(reconcile.kube, "applied_allocation_hash", lambda *a: "desired")
    monkeypatch.setattr(
        reconcile.kube,
        "applied_terminal_template_hash",
        lambda *a: "terminal-template",
    )
    monkeypatch.setattr(reconcile.kube, "applied_terminal_percent", lambda *a: 25)
    monkeypatch.setattr(reconcile.kube, "get_replicas", lambda *a: 1)
    monkeypatch.setattr(reconcile.kube, "get_terminal_replicas", lambda *a: 1)
    monkeypatch.setattr(reconcile.kube, "current_ready_terminal_pod_ip", lambda *a: "10.0.0.8")
    monkeypatch.setattr(
        reconcile.kube,
        "applied_terminal_drain_token",
        lambda *_args: "terminal-capability",
    )
    monkeypatch.setattr(
        reconcile.httpx, "get",
        lambda *a, **k: _Response({
            "active_sessions": 0, "last_activity_ts": 0.0, "session_revision": 7}),
    )
    monkeypatch.setattr(
        reconcile, "_begin_terminal_drain",
        lambda ip, port, revision, logger, **kwargs: (
            calls.append(("drain", revision)) or True
        ),
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

    assert calls == [
        ("drain", 7),
        ("status", "Draining"),
        ("drain", 7),
        ("scale", 0),
    ]
    assert patch_obj.status["terminal"]["phase"] == "Draining"
    assert patch_obj.status["terminal"]["podIP"] == "10.0.0.8"
    assert patch_obj.status["terminal"]["readyReplicas"] == 1


def _run_legacy_idle(monkeypatch, patch_obj, stub_logger, *, phase):
    calls = []
    monkeypatch.setattr(reconcile, "get_settings", _settings)
    monkeypatch.setattr(reconcile, "_runner_defaults", lambda spec=None: _defaults())
    monkeypatch.setattr(reconcile.kube, "resolve_terminal_percent", lambda *a: 25)
    monkeypatch.setattr(reconcile.kube, "allocation_hash", lambda *a, **k: "desired")
    monkeypatch.setattr(
        reconcile.kube, "terminal_template_hash", lambda *a, **k: "terminal-template")
    monkeypatch.setattr(reconcile.kube, "applied_allocation_hash", lambda *a: "desired")
    monkeypatch.setattr(
        reconcile.kube,
        "applied_terminal_template_hash",
        lambda *a: "terminal-template",
    )
    monkeypatch.setattr(reconcile.kube, "applied_terminal_percent", lambda *a: 25)
    monkeypatch.setattr(reconcile.kube, "get_replicas", lambda *a: 1)
    monkeypatch.setattr(reconcile.kube, "get_terminal_replicas", lambda *a: 1)
    monkeypatch.setattr(reconcile.kube, "current_ready_terminal_pod_ip", lambda *a: "10.0.0.8")
    monkeypatch.setattr(
        reconcile.kube,
        "applied_terminal_drain_token",
        lambda *_args: "terminal-capability",
    )
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


def test_legacy_terminal_stays_running_and_reports_upgrade_required(
    monkeypatch, patch_obj, stub_logger,
):
    calls = _run_legacy_idle(
        monkeypatch, patch_obj, stub_logger, phase=None)

    assert calls == []
    assert patch_obj.status["terminal"]["phase"] == "Running"
    assert patch_obj.status["terminal"]["drainCondition"] == "DrainUnsupported"
    assert patch_obj.status["terminal"]["drainReason"] == "UpgradeRequired"


def test_existing_legacy_terminal_gate_is_healed_without_scaling(
    monkeypatch, patch_obj, stub_logger,
):
    calls = _run_legacy_idle(
        monkeypatch, patch_obj, stub_logger, phase="DrainingLegacy")

    assert calls == [("status", "Running")]
    assert patch_obj.status["terminal"]["phase"] == "Running"
    assert not any(kind == "scale" for kind, _ in calls)
    unsupported = next(
        c for c in patch_obj.status["conditions"]
        if c["type"] == "TerminalDrainUnsupported"
    )
    assert unsupported["status"] == "True"
    assert unsupported["reason"] == "UpgradeRequired"


def test_terminal_wake_live_drain_phase_wins_over_stale_snapshot(
    monkeypatch, patch_obj, stub_logger,
):
    monkeypatch.setattr(reconcile, "get_settings", _settings)
    monkeypatch.setattr(reconcile, "_runner_defaults", lambda spec=None: _defaults())
    monkeypatch.setattr(reconcile.kube, "resolve_terminal_percent", lambda *a: 25)
    monkeypatch.setattr(reconcile.kube, "allocation_hash", lambda *a, **k: "desired")
    monkeypatch.setattr(
        reconcile.kube, "current_cr_terminal_phase", lambda *a: "Draining")
    monkeypatch.setattr(
        reconcile.kube, "ensure_terminal_objects",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("stale wake must not converge a draining Terminal")),
    )
    monkeypatch.setattr(
        reconcile.kube, "scale_terminal",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("stale wake must not scale a draining Terminal")),
    )

    reconcile.on_terminal_wake(
        spec={"accountId": "acct-1", "username": "alice"},
        name="acct-1",
        namespace="tenants",
        uid="uid-1",
        status={"terminal": {"phase": "Zero"}},
        patch=patch_obj,
        logger=stub_logger,
    )

    assert "terminal" not in patch_obj.status


@pytest.mark.parametrize(
    ("live_phases", "scaled"),
    [
        ([None, None, "Draining"], False),
        ([None, None, None, "Draining"], True),
    ],
)
def test_terminal_wake_rechecks_drain_around_scale_and_never_reblesses_running(
    monkeypatch, patch_obj, stub_logger, live_phases, scaled,
):
    calls = []
    phases = iter(live_phases)
    monkeypatch.setattr(reconcile, "get_settings", _settings)
    monkeypatch.setattr(reconcile, "_runner_defaults", lambda spec=None: _defaults())
    monkeypatch.setattr(reconcile, "_workload_isolation", lambda *a, **k: object())
    monkeypatch.setattr(reconcile.kube, "resolve_terminal_percent", lambda *a: 25)
    monkeypatch.setattr(reconcile.kube, "allocation_hash", lambda *a, **k: "desired")
    monkeypatch.setattr(reconcile.kube, "get_replicas", lambda *a: 1)
    monkeypatch.setattr(reconcile.kube, "get_terminal_replicas", lambda *a: 0)
    monkeypatch.setattr(reconcile.kube, "applied_allocation_hash", lambda *a: "desired")
    monkeypatch.setattr(reconcile.kube, "applied_terminal_percent", lambda *a: 25)
    monkeypatch.setattr(reconcile.kube, "resolve_image", lambda *a: "runner:test")
    monkeypatch.setattr(
        reconcile.kube, "current_cr_terminal_phase", lambda *a: next(phases))
    monkeypatch.setattr(
        reconcile.kube, "ensure_terminal_objects",
        lambda *a, **k: calls.append("ensure"),
    )
    monkeypatch.setattr(
        reconcile.kube, "scale_terminal",
        lambda *a, **k: calls.append("scale"),
    )
    monkeypatch.setattr(
        reconcile.kube, "wait_terminal_pod_ready",
        lambda *a, **k: "10.0.0.8",
    )

    reconcile.on_terminal_wake(
        spec={"accountId": "acct-1", "username": "alice"},
        name="acct-1",
        namespace="tenants",
        uid="uid-1",
        status={"terminal": {"phase": "Zero"}},
        patch=patch_obj,
        logger=stub_logger,
    )

    assert ("scale" in calls) is scaled
    assert "terminal" not in patch_obj.status
