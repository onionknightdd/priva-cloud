from __future__ import annotations

from types import SimpleNamespace

import priva_operator.reconcile as R


def _defaults(storage_gb=1):
    return SimpleNamespace(
        idle_grace_seconds=1800,
        min_alive_after_wake_seconds=1800,
        cpu_cores=1.0,
        memory_mb=2048,
        storage_gb=storage_gb,
        runner_image="runner:test",
        terminal_resource_percent=25,
        terminal_max_sessions=2,
        terminal_idle_timeout_seconds=1800,
        terminal_max_lifetime_seconds=14400,
        terminal_scale_down_grace_seconds=120,
    )


def test_missing_username_blocks_all_runtime_creation(monkeypatch, patch_obj, stub_logger):
    monkeypatch.setattr(
        R.kube, "ensure_runtime_objects",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not create runtime")),
    )

    R.ensure(
        spec={"accountId": "acct"}, name="acct", namespace="ns", uid="uid",
        status={}, patch=patch_obj, logger=stub_logger,
    )

    condition = next(c for c in patch_obj.status["conditions"] if c["type"] == "IdentityReady")
    assert condition["status"] == "False"
    assert condition["reason"] == "IdentityIncomplete"


def test_missing_account_id_blocks_all_runtime_creation(monkeypatch, patch_obj, stub_logger):
    monkeypatch.setattr(
        R.kube, "ensure_runtime_objects",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not create runtime")),
    )

    R.ensure(
        spec={"username": "alice"}, name="acct", namespace="ns", uid="uid",
        status={}, patch=patch_obj, logger=stub_logger,
    )

    condition = next(c for c in patch_obj.status["conditions"] if c["type"] == "IdentityReady")
    assert condition["status"] == "False"
    assert condition["reason"] == "IdentityIncomplete"


def test_unavailable_defaults_are_never_interpreted_as_zero(monkeypatch, patch_obj, stub_logger):
    monkeypatch.setattr(R, "_runner_defaults", lambda spec=None: None)
    monkeypatch.setattr(
        R.kube, "ensure_runtime_objects",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must retain current state")),
    )
    monkeypatch.setattr(
        R.kube, "scale_terminal",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not scale Terminal")),
    )

    R.ensure(
        spec={"accountId": "acct", "username": "alice"}, name="acct",
        namespace="ns", uid="uid", status={}, patch=patch_obj, logger=stub_logger,
    )

    condition = next(
        c for c in patch_obj.status["conditions"] if c["type"] == "ConfigurationReady")
    assert condition["status"] == "False"
    assert condition["reason"] == "DefaultsUnavailable"


def test_offboarding_closes_runner_and_terminal_state_loop(monkeypatch, patch_obj, stub_logger):
    calls = []
    monkeypatch.setattr(
        R, "_runner_defaults",
        lambda spec=None: (_ for _ in ()).throw(
            AssertionError("offboarding must not depend on data-spine defaults")),
    )
    monkeypatch.setattr(R.kube, "allocation_hash", lambda *a, **k: "desired")
    monkeypatch.setattr(R.kube, "get_replicas", lambda *a: 1)
    monkeypatch.setattr(R.kube, "get_terminal_replicas", lambda *a: 1)
    monkeypatch.setattr(R.kube, "set_cr_status", lambda *a, **k: calls.append(("status", k)))
    monkeypatch.setattr(R.kube, "scale", lambda *a: calls.append(("runner", a[-1])))
    monkeypatch.setattr(R.kube, "scale_terminal", lambda *a: calls.append(("terminal", a[-1])))

    R.reconcile_runtime(
        spec={"accountId": "acct", "username": "alice", "desiredState": "offboarding"},
        name="acct", namespace="ns", uid="uid", status={}, patch=patch_obj,
        logger=stub_logger,
    )

    assert ("runner", 0) in calls
    assert ("terminal", 0) in calls
    assert patch_obj.status["phase"] == "Offboarding"
    assert patch_obj.status["podIP"] is None


def test_rejected_quota_is_not_retried_for_same_desired_value(
    monkeypatch, patch_obj, stub_logger,
):
    monkeypatch.setattr(R, "_runner_defaults", lambda spec=None: _defaults(storage_gb=2))
    monkeypatch.setattr(R, "_render_managed_policy", lambda *a, **k: None)
    monkeypatch.setattr(R.kube, "allocation_hash", lambda *a, **k: "desired")
    monkeypatch.setattr(R.kube, "applied_allocation_hash", lambda *a: "desired")
    monkeypatch.setattr(R.kube, "get_replicas", lambda *a: 0)
    monkeypatch.setattr(R.kube, "resolve_storage_gb", lambda *a: 2)
    monkeypatch.setattr(
        R.storage_backend, "get_backend",
        lambda *a: (_ for _ in ()).throw(AssertionError("rejected quota must not retry")),
    )

    R.reconcile_runtime(
        spec={"accountId": "acct", "username": "alice"}, name="acct",
        namespace="ns", uid="uid",
        status={"storageGb": 1, "storageRejectedGb": 2},
        patch=patch_obj, logger=stub_logger,
    )

    assert "storageWarning" not in patch_obj.status
