"""Idle-sleep ordering (#2): the CR is flipped not-routable (direct set_cr_status) BEFORE
the deployment is scaled to 0, so the EPP can't hand out a doomed endpoint during
teardown."""

from __future__ import annotations

from types import SimpleNamespace

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
    monkeypatch.setattr(R.httpx, "get",
                        lambda *a, **k: _Resp({"active_runs": 0, "last_activity_ts": 0.0}))

    R.reconcile_runtime(
        spec={"accountId": "acct", "username": "alice",
              "idle": {"graceSeconds": 0, "minAliveAfterWakeSeconds": 0}},
        name="acct", namespace="ns",
        status={"podIP": "10.0.0.1", "phase": "Running", "startedAt": 1.0,
                "storageGb": 1},
        patch=patch_obj, logger=stub_logger,
    )

    # The not-routable flip must precede the scale-down teardown step.
    assert calls == ["set_cr_status", "scale"]
    assert calls.index("set_cr_status") < calls.index("scale")
    # Trailing kopf patch re-asserts Zero + records idleSince.
    assert patch_obj.status["phase"] == "Zero"
    assert patch_obj.status["podIP"] is None
    assert patch_obj.status["idleSince"] is not None
