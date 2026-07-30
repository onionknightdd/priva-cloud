from __future__ import annotations

from types import SimpleNamespace

import pytest

import priva_operator.reconcile as reconcile

SPEC = {"accountId": "acct", "username": "alice", "desiredState": "purge"}
ACTIVE_SPEC = {"accountId": "acct", "username": "alice", "desiredState": "active"}


def _settings():
    return SimpleNamespace(kubernetes=SimpleNamespace(
        storage_backend="nfs_xfs",
        runner_image_pull_policy="IfNotPresent",
    ))


@pytest.fixture(autouse=True)
def _clean_purge_registry():
    """``_purging`` is process-wide and deliberately never pruned in production."""
    reconcile._purging.clear()
    yield
    reconcile._purging.clear()


def _wire(monkeypatch, calls, *, replicas=1, terminal_replicas=1,
          deprovision=None, claim_present=False, pods_gone=True):
    monkeypatch.setattr(reconcile, "get_settings", _settings)
    monkeypatch.setattr(reconcile.kube, "get_replicas", lambda *a: replicas)
    monkeypatch.setattr(reconcile.kube, "get_terminal_replicas", lambda *a: terminal_replicas)
    monkeypatch.setattr(
        reconcile.kube,
        "set_cr_status",
        lambda *a, **k: calls.append(("status", k.get("phase"))),
    )
    monkeypatch.setattr(reconcile.kube, "scale", lambda *a: calls.append(("runner", a[-1])))
    monkeypatch.setattr(
        reconcile.kube, "scale_terminal", lambda *a: calls.append(("terminal", a[-1])))
    monkeypatch.setattr(
        reconcile.kube,
        "wait_account_workload_pods_gone",
        lambda namespace, account_id, **kwargs: (
            calls.append(("wait-pods", account_id)) or pods_gone
        ),
    )
    monkeypatch.setattr(
        reconcile.kube, "delete_export_claim",
        lambda namespace, account_id: calls.append(("claim", account_id)) or claim_present)

    def _deprovision(account_id):
        calls.append(("deprovision", account_id))
        if deprovision is not None:
            raise deprovision

    monkeypatch.setattr(
        reconcile.storage_backend, "get_backend",
        lambda settings: SimpleNamespace(deprovision=_deprovision))


def _wire_converge(monkeypatch, calls):
    """Everything a dormant-tenant tick needs to reach ``ensure_runtime_objects``, so a
    tick the purge record blocked is distinguishable from one that never got that far."""
    monkeypatch.setattr(reconcile, "_render_managed_policy", lambda *a, **k: None)
    isolation = reconcile._network_isolation_cache
    monkeypatch.setattr(
        reconcile, "_render_network_policies", lambda *a, **k: isolation
    )
    monkeypatch.setattr(
        reconcile,
        "_network_isolation_applied_intent",
        reconcile.isolation_intent_digest(isolation, _settings()),
    )
    monkeypatch.setattr(reconcile, "_network_isolation_dirty", False)
    monkeypatch.setattr(
        reconcile.egress_proxy,
        "render_squid_conf",
        lambda *_: "test squid config\n",
    )
    monkeypatch.setattr(
        reconcile, "_runner_defaults",
        lambda spec=None: SimpleNamespace(storage_gb=1, idle_grace_seconds=1800,
                                          min_alive_after_wake_seconds=1800))
    monkeypatch.setattr(reconcile.kube, "allocation_hash", lambda *a, **k: "desired")
    monkeypatch.setattr(reconcile.kube, "applied_allocation_hash", lambda *a: "stale")
    monkeypatch.setattr(reconcile.kube, "resolve_storage_gb", lambda *a: 1)
    monkeypatch.setattr(reconcile.kube, "resolve_image", lambda *a: "runner:test")
    monkeypatch.setattr(
        reconcile.kube, "ensure_runtime_objects",
        lambda namespace, account_id, *a: calls.append(("ensure", account_id)))


def test_purge_scales_both_deployments_then_reclaims_the_volume(
    monkeypatch, stub_logger,
):
    calls = []
    _wire(monkeypatch, calls)
    monkeypatch.setattr(
        reconcile,
        "_force_close_account_admission",
        lambda *a, **k: calls.append(("force-drain", "acct")),
    )

    reconcile.purge(spec=SPEC, name="acct", namespace="ns", logger=stub_logger)

    assert calls == [
        ("status", "Draining"),
        ("force-drain", "acct"),
        ("runner", 0),
        ("terminal", 0),
        ("wait-pods", "acct"),
        ("deprovision", "acct"),
        ("claim", "acct"),
    ]


def test_purge_tolerates_a_teardown_that_already_ran(monkeypatch, stub_logger):
    calls = []
    _wire(monkeypatch, calls, replicas=-1, terminal_replicas=-1)

    reconcile.purge(spec=SPEC, name="acct", namespace="ns", logger=stub_logger)

    assert calls == [
        ("status", "Draining"),
        ("wait-pods", "acct"),
        ("deprovision", "acct"),
        ("claim", "acct"),
    ]


def test_delete_without_explicit_purge_intent_preserves_storage(
    monkeypatch, stub_logger,
):
    calls = []
    _wire(monkeypatch, calls, replicas=-1, terminal_replicas=-1)

    reconcile.purge(spec={}, name="acct", namespace="ns", logger=stub_logger)

    assert ("status", "Draining") in calls
    assert ("wait-pods", "acct") in calls
    assert ("deprovision", "acct") not in calls
    assert ("claim", "acct") not in calls


def test_purge_retries_while_the_attempt_budget_remains(monkeypatch, stub_logger):
    calls = []
    _wire(monkeypatch, calls, deprovision=RuntimeError("quota-manager unreachable"))

    with pytest.raises(RuntimeError):
        reconcile.purge(
            spec=SPEC, name="acct", namespace="ns", logger=stub_logger,
            retry=reconcile._PURGE_MAX_ATTEMPTS - 2)


def test_purge_releases_the_finalizer_on_the_last_attempt(monkeypatch, stub_logger):
    calls = []
    _wire(monkeypatch, calls, deprovision=RuntimeError("quota-manager unreachable"))

    reconcile.purge(
        spec=SPEC, name="acct", namespace="ns", logger=stub_logger,
        retry=reconcile._PURGE_MAX_ATTEMPTS - 1)

    assert ("deprovision", "acct") in calls


def test_purge_reclaims_an_export_claim_the_backend_left_behind(monkeypatch, stub_logger):
    calls = []
    _wire(monkeypatch, calls, replicas=0, terminal_replicas=0, claim_present=True)

    reconcile.purge(spec=SPEC, name="acct", namespace="ns", logger=stub_logger)

    assert calls.index(("deprovision", "acct")) < calls.index(("claim", "acct"))


def test_purge_retries_without_reclaim_while_any_account_pod_remains(
    monkeypatch, stub_logger,
):
    calls = []
    _wire(monkeypatch, calls, pods_gone=False)

    with pytest.raises(RuntimeError, match="did not terminate"):
        reconcile.purge(
            spec=SPEC,
            name="acct",
            namespace="ns",
            logger=stub_logger,
            retry=0,
        )

    assert ("wait-pods", "acct") in calls
    assert ("deprovision", "acct") not in calls
    assert ("claim", "acct") not in calls


def test_purge_blocks_a_racing_timer_from_reprovisioning(
    monkeypatch, patch_obj, stub_logger,
):
    calls = []
    _wire(monkeypatch, calls, replicas=0, terminal_replicas=0)
    reconcile.purge(spec=SPEC, name="acct", namespace="ns", logger=stub_logger)

    monkeypatch.setattr(
        reconcile.kube, "ensure_runtime_objects",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not re-provision")),
    )
    monkeypatch.setattr(
        reconcile.kube, "ensure_terminal_objects",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not re-provision")),
    )

    reconcile.reconcile_runtime(
        spec=SPEC, name="acct", namespace="ns", status={}, patch=patch_obj,
        logger=stub_logger, uid="uid",
    )
    reconcile.reconcile_terminal(
        spec=SPEC, name="acct", namespace="ns", uid="uid", status={}, patch=patch_obj,
        logger=stub_logger,
    )


def test_purge_blocks_a_racing_timer_carrying_the_torn_down_uid(
    monkeypatch, patch_obj, stub_logger,
):
    calls = []
    _wire(monkeypatch, calls, replicas=0, terminal_replicas=0)
    reconcile.purge(
        spec=SPEC, name="acct", namespace="ns", logger=stub_logger, uid="uid-1")
    _wire_converge(monkeypatch, calls)

    reconcile.reconcile_runtime(
        spec=ACTIVE_SPEC, name="acct", namespace="ns",
        status={"storageGb": 1}, patch=patch_obj,
        logger=stub_logger, uid="uid-1", meta={"uid": "uid-1"},
    )

    assert ("ensure", "acct") not in calls


def test_a_cr_recreated_after_an_out_of_band_delete_converges_again(
    monkeypatch, patch_obj, stub_logger,
):
    """``kubectl delete agenttenant`` on a still-active account runs the finalizer; the
    control plane re-creates the CR, and that new object must not inherit the block."""
    calls = []
    _wire(monkeypatch, calls, replicas=0, terminal_replicas=0)
    reconcile.purge(
        spec=ACTIVE_SPEC,
        name="acct",
        namespace="ns",
        logger=stub_logger,
        uid="uid-1",
    )
    _wire_converge(monkeypatch, calls)

    reconcile.reconcile_runtime(
        spec=ACTIVE_SPEC, name="acct", namespace="ns",
        status={"storageGb": 1}, patch=patch_obj,
        logger=stub_logger, uid="uid-2", meta={"uid": "uid-2"},
    )

    assert ("ensure", "acct") in calls


def test_a_tick_already_in_flight_when_the_purge_lands_still_bails(
    monkeypatch, patch_obj, stub_logger,
):
    """kopf cannot cancel a sync timer mid-flight, so the entry guard alone is not enough:
    the tick below passed it before the delete arrived and must still refuse to converge."""
    seen = []

    def _teardown_started(account_id, meta=None):
        seen.append(account_id)
        return len(seen) > 1

    monkeypatch.setattr(reconcile, "get_settings", _settings)
    monkeypatch.setattr(reconcile, "_teardown_started", _teardown_started)
    monkeypatch.setattr(reconcile, "_render_managed_policy", lambda *a, **k: None)
    monkeypatch.setattr(
        reconcile, "_runner_defaults",
        lambda spec=None: SimpleNamespace(storage_gb=1, idle_grace_seconds=1800,
                                          min_alive_after_wake_seconds=1800))
    monkeypatch.setattr(reconcile.kube, "get_replicas", lambda *a: 0)
    monkeypatch.setattr(reconcile.kube, "allocation_hash", lambda *a, **k: "desired")
    monkeypatch.setattr(reconcile.kube, "resolve_storage_gb", lambda *a: 1)
    monkeypatch.setattr(
        reconcile.kube, "applied_allocation_hash",
        lambda *a: (_ for _ in ()).throw(AssertionError("must not converge a purged tenant")),
    )
    monkeypatch.setattr(
        reconcile.kube, "ensure_runtime_objects",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not re-provision")),
    )

    reconcile.reconcile_runtime(
        spec={**ACTIVE_SPEC, "agentRunnerType": "persistent"}, name="acct", namespace="ns",
        status={"storageGb": 1}, patch=patch_obj, logger=stub_logger, uid="uid",
    )

    assert len(seen) == 2


def test_deletion_timestamp_blocks_ensure_even_before_purge_runs(
    monkeypatch, patch_obj, stub_logger,
):
    monkeypatch.setattr(
        reconcile.kube, "ensure_runtime_objects",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not re-provision")),
    )
    monkeypatch.setattr(
        reconcile.kube, "scale",
        lambda *a: (_ for _ in ()).throw(AssertionError("must not scale a deleting CR")),
    )

    reconcile.ensure(
        spec=SPEC, name="acct", namespace="ns", uid="uid", status={}, patch=patch_obj,
        logger=stub_logger, meta={"deletionTimestamp": "2026-07-27T00:00:00Z"},
    )

    assert patch_obj.status == {}
