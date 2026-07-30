"""Idle-sleep ordering (#2): the CR is flipped not-routable (direct set_cr_status) BEFORE
the deployment is scaled to 0, so the EPP can't hand out a doomed endpoint during
teardown."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import priva_operator.reconcile as R


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


def test_set_not_routable_before_teardown(monkeypatch, patch_obj, stub_logger):
    calls: list[str] = []
    monkeypatch.setattr(
        R, "_runner_defaults",
        lambda spec=None: SimpleNamespace(
            idle_grace_seconds=0, min_alive_after_wake_seconds=0),
    )
    monkeypatch.setattr(R.kube, "get_replicas", lambda ns, aid: 1)
    monkeypatch.setattr(R.kube, "resolve_storage_gb", lambda *a, **k: 1)
    monkeypatch.setattr(R.kube, "current_ready_pod_ip", lambda ns, aid: "10.0.0.1")
    monkeypatch.setattr(R.kube, "allocation_hash", lambda *a, **k: "desired")
    monkeypatch.setattr(R.kube, "applied_allocation_hash", lambda ns, aid: "desired")
    monkeypatch.setattr(R.kube, "set_cr_status", lambda *a, **k: calls.append("set_cr_status"))
    monkeypatch.setattr(R.kube, "scale", lambda *a, **k: calls.append("scale"))
    monkeypatch.setattr(
        R,
        "_begin_runner_drain",
        lambda *a, **k: calls.append("drain") or 7,
    )
    monkeypatch.setattr(R.httpx, "get",
                        lambda *a, **k: _Resp({
                            "active_runs": 0,
                            "last_activity_ts": 0.0,
                            "activity_revision": 7,
                        }))

    R.reconcile_runtime(
        spec={"accountId": "acct", "username": "alice",
              "idle": {"graceSeconds": 0, "minAliveAfterWakeSeconds": 0}},
        name="acct", namespace="ns",
        status={"podIP": "10.0.0.1", "phase": "Running", "startedAt": 1.0,
                "storageGb": 1},
        patch=patch_obj, logger=stub_logger,
    )

    # The not-routable flip must precede the scale-down teardown step.
    assert calls == ["drain", "set_cr_status", "drain", "scale"]
    assert calls.index("set_cr_status") < calls.index("scale")
    # Keep the wake/routing gate closed until a later tick observes that the
    # old Pod has physically disappeared.
    assert patch_obj.status["phase"] == "Draining"
    assert patch_obj.status["podIP"] == "10.0.0.1"
    assert patch_obj.status["readyReplicas"] == 1
    assert patch_obj.status["idleSince"] is not None


def test_revision_conflict_never_scales(monkeypatch, patch_obj, stub_logger):
    calls: list[str] = []
    monkeypatch.setattr(
        R, "_runner_defaults",
        lambda spec=None: SimpleNamespace(
            idle_grace_seconds=0, min_alive_after_wake_seconds=0
        ),
    )
    monkeypatch.setattr(R.kube, "get_replicas", lambda *a: 1)
    monkeypatch.setattr(R.kube, "resolve_storage_gb", lambda *a, **k: 1)
    monkeypatch.setattr(R.kube, "current_ready_pod_ip", lambda *a: "10.0.0.1")
    monkeypatch.setattr(R.kube, "allocation_hash", lambda *a, **k: "desired")
    monkeypatch.setattr(R.kube, "applied_allocation_hash", lambda *a: "desired")
    monkeypatch.setattr(R.kube, "scale", lambda *a: calls.append("scale"))
    monkeypatch.setattr(
        R.kube, "set_cr_status", lambda *a, **k: calls.append("status")
    )
    monkeypatch.setattr(R, "_begin_runner_drain", lambda *a, **k: None)
    monkeypatch.setattr(
        R.httpx,
        "get",
        lambda *a, **k: _Resp({
            "active_runs": 0,
            "last_activity_ts": 0.0,
            "activity_revision": 11,
        }),
    )

    R.reconcile_runtime(
        spec={
            "accountId": "acct",
            "username": "alice",
            "idle": {"graceSeconds": 0, "minAliveAfterWakeSeconds": 0},
        },
        name="acct",
        namespace="ns",
        status={
            "podIP": "10.0.0.1",
            "phase": "Running",
            "startedAt": 1.0,
            "storageGb": 1,
        },
        patch=patch_obj,
        logger=stub_logger,
    )
    assert calls == []


@pytest.mark.parametrize(
    ("phase", "expected"),
    [
        ("Running", []),
        ("DrainingLegacy", ["status"]),
    ],
)
def test_legacy_runner_never_auto_scales_without_atomic_drain(
    monkeypatch, patch_obj, stub_logger, phase, expected
):
    calls: list[str] = []
    monkeypatch.setattr(
        R, "_runner_defaults",
        lambda spec=None: SimpleNamespace(
            idle_grace_seconds=0, min_alive_after_wake_seconds=0
        ),
    )
    monkeypatch.setattr(R.kube, "get_replicas", lambda *a: 1)
    monkeypatch.setattr(R.kube, "resolve_storage_gb", lambda *a, **k: 1)
    monkeypatch.setattr(R.kube, "current_ready_pod_ip", lambda *a: "10.0.0.1")
    monkeypatch.setattr(R.kube, "allocation_hash", lambda *a, **k: "desired")
    monkeypatch.setattr(R.kube, "applied_allocation_hash", lambda *a: "desired")
    monkeypatch.setattr(
        R.kube, "set_cr_status", lambda *a, **k: calls.append("status")
    )
    monkeypatch.setattr(R.kube, "scale", lambda *a: calls.append("scale"))
    monkeypatch.setattr(
        R,
        "_begin_runner_drain",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("legacy Runner has no drain endpoint")
        ),
    )
    monkeypatch.setattr(
        R.httpx,
        "get",
        lambda *a, **k: _Resp({
            "active_runs": 0,
            "last_activity_ts": 0.0,
        }),
    )

    R.reconcile_runtime(
        spec={
            "accountId": "acct",
            "username": "alice",
            "idle": {"graceSeconds": 0, "minAliveAfterWakeSeconds": 0},
        },
        name="acct",
        namespace="ns",
        status={
            "podIP": "10.0.0.1",
            "phase": phase,
            "startedAt": 1.0,
            "storageGb": 1,
        },
        patch=patch_obj,
        logger=stub_logger,
    )
    assert calls == expected
    assert patch_obj.status["phase"] == "Running"
    unsupported = next(
        c for c in patch_obj.status["conditions"]
        if c["type"] == "DrainUnsupported"
    )
    assert unsupported["status"] == "True"
    assert unsupported["reason"] == "UpgradeRequired"


@pytest.mark.parametrize("phase", ["Draining", "DrainingLegacy"])
def test_scale_applied_but_tail_patch_lost_recovers_zero_after_pod_exit(
    monkeypatch, patch_obj, stub_logger, phase,
):
    direct = []
    monkeypatch.setattr(R.kube, "get_replicas", lambda *a: 0)
    monkeypatch.setattr(
        R.kube, "workload_pods_gone",
        lambda namespace, account_id, app: app == "agent-runner",
    )
    monkeypatch.setattr(
        R.kube, "set_cr_status",
        lambda *a, **k: direct.append(k),
    )
    monkeypatch.setattr(
        R, "_runner_defaults",
        lambda spec=None: (_ for _ in ()).throw(
            AssertionError("drain recovery must not depend on data-spine defaults")
        ),
    )

    R.reconcile_runtime(
        spec={"accountId": "acct", "username": "alice"},
        name="acct",
        namespace="ns",
        status={
            "phase": phase,
            "podIP": "10.0.0.1",
            "readyReplicas": 1,
        },
        patch=patch_obj,
        logger=stub_logger,
    )

    assert direct == [{
        "phase": "Zero",
        "podIP": None,
        "readyReplicas": 0,
    }]
    assert patch_obj.status["phase"] == "Zero"
    assert patch_obj.status["podIP"] is None
    assert patch_obj.status["readyReplicas"] == 0


def test_zero_replicas_keeps_draining_while_old_pod_is_terminating(
    monkeypatch, patch_obj, stub_logger,
):
    monkeypatch.setattr(R.kube, "get_replicas", lambda *a: 0)
    monkeypatch.setattr(R.kube, "workload_pods_gone", lambda *a: False)
    monkeypatch.setattr(
        R.kube, "set_cr_status",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("must retain the durable drain gate")
        ),
    )

    R.reconcile_runtime(
        spec={"accountId": "acct", "username": "alice"},
        name="acct",
        namespace="ns",
        status={"phase": "Draining", "podIP": "10.0.0.1"},
        patch=patch_obj,
        logger=stub_logger,
    )

    assert "phase" not in patch_obj.status
