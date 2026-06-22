"""reconcile_runtime self-heal (#1): a stale status.podIP heals to the real pod IP (with
startedAt reset on change), the replacement gap flips the CR not-routable and returns
BEFORE the idle probe, and a matched IP proceeds to the idle logic. kube/secrets/httpx
are faked."""

from __future__ import annotations

import time

import priva_operator.reconcile as R


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


def _run_reconcile(monkeypatch, patch_obj, stub_logger, *, replicas, real_ip,
                   status, spec=None, health=None):
    monkeypatch.setattr(R.kube, "get_replicas", lambda ns, aid: replicas)
    monkeypatch.setattr(R.kube, "current_ready_pod_ip", lambda ns, aid: real_ip)
    monkeypatch.setattr(R.kube, "scale", lambda *a, **k: None)
    monkeypatch.setattr(R.kube, "set_cr_status", lambda *a, **k: None)
    monkeypatch.setattr(R.secrets, "delete", lambda *a, **k: None)
    monkeypatch.setattr(R.secrets, "exists", lambda *a, **k: False)

    def _get(url, **k):
        if health is None:  # the test asserts the idle probe is NOT reached
            raise AssertionError("idle probe should not run in this scenario")
        return _Resp(health)

    monkeypatch.setattr(R.httpx, "get", _get)

    R.reconcile_runtime(
        spec=spec or {"accountId": "acct"},
        name="acct",
        namespace="ns",
        status=status,
        patch=patch_obj,
        logger=stub_logger,
    )


def test_stale_pod_ip_heals_and_resets_started_at(monkeypatch, patch_obj, stub_logger):
    _run_reconcile(monkeypatch, patch_obj, stub_logger,
                   replicas=1, real_ip="10.0.0.9",
                   status={"podIP": "10.0.0.1", "phase": "Running", "startedAt": 1.0})
    assert patch_obj.status["podIP"] == "10.0.0.9"
    assert patch_obj.status["phase"] == "Running"
    assert patch_obj.status["readyReplicas"] == 1
    # changed IP == replacement pod -> fresh min_alive window (don't inherit dead clock)
    assert patch_obj.status["startedAt"] != 1.0


def test_replacement_gap_flips_not_routable_and_returns_early(monkeypatch, patch_obj, stub_logger):
    # replicas==1 but no Ready, non-terminating pod -> flip not-routable; the AssertionError
    # in _get would fire if the idle probe ran (it must not).
    _run_reconcile(monkeypatch, patch_obj, stub_logger,
                   replicas=1, real_ip=None,
                   status={"podIP": "10.0.0.1", "phase": "Running", "startedAt": 1.0})
    assert patch_obj.status["phase"] == "Waking"
    assert patch_obj.status["podIP"] is None
    assert patch_obj.status["readyReplicas"] == 0


def test_matched_ip_within_min_alive_is_noop(monkeypatch, patch_obj, stub_logger):
    # IP matches reality and the pod is younger than min_alive -> nothing changes, and the
    # idle probe is not reached (default min_alive 1800s).
    _run_reconcile(monkeypatch, patch_obj, stub_logger,
                   replicas=1, real_ip="10.0.0.1",
                   status={"podIP": "10.0.0.1", "phase": "Running", "startedAt": time.time()})
    assert patch_obj.status == {}


def test_matched_ip_active_does_not_sleep(monkeypatch, patch_obj, stub_logger):
    # Old enough to be sweepable, but the pod reports an active run -> stays up.
    _run_reconcile(
        monkeypatch, patch_obj, stub_logger,
        replicas=1, real_ip="10.0.0.1",
        status={"podIP": "10.0.0.1", "phase": "Running", "startedAt": 1.0},
        spec={"accountId": "acct", "idle": {"graceSeconds": 0, "minAliveAfterWakeSeconds": 0}},
        health={"active_runs": 1, "last_activity_ts": 1.0},
    )
    assert "idleSince" not in patch_obj.status
    assert patch_obj.status.get("phase") != "Zero"
