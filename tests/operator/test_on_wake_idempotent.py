"""on_wake reality-based guard (#4): when the Deployment is already scaled to 1, the wake
does NOT re-scale — it resolves the real Ready pod IP and writes it. The cold path scales.
(Creds are no longer materialized by the operator — they live in the pod's settings.json.)"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import priva_operator.reconcile as R


def test_warm_wake_skips_scale(monkeypatch, patch_obj, stub_logger):
    called = {"scale": 0}
    monkeypatch.setattr(R.kube, "get_replicas", lambda ns, aid: 1)
    monkeypatch.setattr(R.kube, "current_ready_pod_ip", lambda ns, aid: "10.0.0.7")
    monkeypatch.setattr(R.kube, "scale",
                        lambda *a, **k: called.__setitem__("scale", called["scale"] + 1))

    R.on_wake(spec={"accountId": "acct", "username": "alice"}, name="acct", namespace="ns", uid="u1",
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

    R.on_wake(spec={"accountId": "acct", "username": "alice"}, name="acct", namespace="ns", uid="u1",
              status={"podIP": "10.0.0.1"}, patch=patch_obj, logger=stub_logger)

    assert patch_obj.status["podIP"] == "10.0.0.9"
    assert "startedAt" in patch_obj.status  # replacement pod -> fresh min_alive window


def test_cold_wake_converges_template_then_scales(monkeypatch, patch_obj, stub_logger):
    # The cold path must converge the FULL template (ensure_runtime_objects) before
    # scaling, so a tenant born under an older operator picks up template additions
    # (e.g. the managed-policy mount) on wake.
    called = {"scale": 0, "converge": 0}
    monkeypatch.setattr(
        R, "_runner_defaults",
        lambda spec=None: SimpleNamespace(),
    )
    monkeypatch.setattr(R.kube, "get_replicas", lambda ns, aid: 0)
    monkeypatch.setattr(R.kube, "ensure_runtime_objects",
                        lambda *a, **k: called.__setitem__("converge", called["converge"] + 1))
    monkeypatch.setattr(R.kube, "scale",
                        lambda *a, **k: called.__setitem__("scale", called["scale"] + 1))
    monkeypatch.setattr(R.kube, "wait_pod_ready", lambda ns, aid, timeout=0: "10.0.0.2")

    R.on_wake(spec={"accountId": "acct", "username": "alice"}, name="acct", namespace="ns", uid="u1",
              status={}, patch=patch_obj, logger=stub_logger)

    assert called == {"scale": 1, "converge": 1}
    assert patch_obj.status["phase"] == "Running"
    assert patch_obj.status["podIP"] == "10.0.0.2"
    assert "startedAt" in patch_obj.status


def test_cold_wake_replace_conflict_never_scales(
    monkeypatch, patch_obj, stub_logger,
):
    monkeypatch.setattr(R, "_runner_defaults", lambda spec=None: SimpleNamespace())
    monkeypatch.setattr(R.kube, "get_replicas", lambda *a: 0)
    monkeypatch.setattr(
        R.kube,
        "ensure_runtime_objects",
        lambda *a, **k: (_ for _ in ()).throw(
            R.kube.client.ApiException(status=409)
        ),
    )
    monkeypatch.setattr(
        R.kube,
        "scale",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("unverified stale template must not be scaled")
        ),
    )

    with pytest.raises(R.kube.client.ApiException) as conflict:
        R.on_wake(
            spec={"accountId": "acct", "username": "alice"},
            name="acct",
            namespace="ns",
            uid="u1",
            status={},
            patch=patch_obj,
            logger=stub_logger,
        )

    assert conflict.value.status == 409


def test_lifecycle_change_during_readiness_wait_cannot_reopen_route(
    monkeypatch, patch_obj, stub_logger,
):
    checks = 0

    def teardown_started(*_args, **_kwargs):
        nonlocal checks
        checks += 1
        return checks >= 6

    monkeypatch.setattr(R.kube, "agenttenant_teardown_started", teardown_started)
    monkeypatch.setattr(R, "_runner_defaults", lambda spec=None: SimpleNamespace())
    monkeypatch.setattr(R.kube, "get_replicas", lambda *_args: 0)
    monkeypatch.setattr(R.kube, "ensure_runtime_objects", lambda *_a, **_k: None)
    monkeypatch.setattr(R.kube, "scale", lambda *_a, **_k: None)
    monkeypatch.setattr(
        R.kube, "wait_pod_ready", lambda *_a, **_k: "10.0.0.2"
    )

    R.on_wake(
        spec={
            "accountId": "acct",
            "username": "alice",
            "desiredState": "active",
        },
        name="acct",
        namespace="ns",
        uid="u1",
        status={},
        patch=patch_obj,
        logger=stub_logger,
    )

    assert checks >= 6
    assert "phase" not in patch_obj.status
    assert "podIP" not in patch_obj.status
    assert "readyReplicas" not in patch_obj.status
