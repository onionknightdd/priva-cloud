"""on_wake reality-based guard (#4): when the Deployment is already scaled to 1, the wake
does NOT re-scale — it resolves the real Ready pod IP and writes it. The cold path scales.
(Creds are no longer materialized by the operator — they live in the pod's settings.json.)"""

from __future__ import annotations

import priva_operator.reconcile as R


def test_warm_wake_skips_scale(monkeypatch, patch_obj, stub_logger):
    called = {"scale": 0}
    monkeypatch.setattr(R.kube, "get_replicas", lambda ns, aid: 1)
    monkeypatch.setattr(R.kube, "current_ready_pod_ip", lambda ns, aid: "10.0.0.7")
    monkeypatch.setattr(R.kube, "scale",
                        lambda *a, **k: called.__setitem__("scale", called["scale"] + 1))

    R.on_wake(spec={"accountId": "acct"}, name="acct", namespace="ns", uid="u1",
              status={"podIP": "10.0.0.7", "phase": "Running"}, patch=patch_obj, logger=stub_logger)

    assert called == {"scale": 0}
    assert patch_obj.status["phase"] == "Running"
    assert patch_obj.status["podIP"] == "10.0.0.7"
    # IP unchanged -> don't reset the anti-thrash clock.
    assert "startedAt" not in patch_obj.status


def test_warm_wake_changed_ip_resets_started_at(monkeypatch, patch_obj, stub_logger):
    monkeypatch.setattr(R.kube, "get_replicas", lambda ns, aid: 1)
    monkeypatch.setattr(R.kube, "current_ready_pod_ip", lambda ns, aid: "10.0.0.9")
    monkeypatch.setattr(R.kube, "scale", lambda *a, **k: None)

    R.on_wake(spec={"accountId": "acct"}, name="acct", namespace="ns", uid="u1",
              status={"podIP": "10.0.0.1"}, patch=patch_obj, logger=stub_logger)

    assert patch_obj.status["podIP"] == "10.0.0.9"
    assert "startedAt" in patch_obj.status  # replacement pod -> fresh min_alive window


def test_cold_wake_scales(monkeypatch, patch_obj, stub_logger):
    called = {"scale": 0}
    monkeypatch.setattr(R.kube, "get_replicas", lambda ns, aid: 0)
    monkeypatch.setattr(R.kube, "scale",
                        lambda *a, **k: called.__setitem__("scale", called["scale"] + 1))
    monkeypatch.setattr(R.kube, "wait_pod_ready", lambda ns, aid, timeout=0: "10.0.0.2")

    R.on_wake(spec={"accountId": "acct"}, name="acct", namespace="ns", uid="u1",
              status={}, patch=patch_obj, logger=stub_logger)

    assert called == {"scale": 1}
    assert patch_obj.status["phase"] == "Running"
    assert patch_obj.status["podIP"] == "10.0.0.2"
    assert "startedAt" in patch_obj.status
