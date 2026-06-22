"""Creds Secret GC (#7): the reconcile deletes the plaintext Secret when the runtime is
scaled to 0 (and the Secret exists), but NOT during a replacement (replicas==1, no Ready
pod yet) — the new pod still needs it via envFrom."""

from __future__ import annotations

import priva_operator.reconcile as R


def test_deletes_secret_when_zero_replicas(monkeypatch, patch_obj, stub_logger):
    deleted = {"n": 0}
    monkeypatch.setattr(R.kube, "get_replicas", lambda ns, aid: 0)
    monkeypatch.setattr(R.secrets, "exists", lambda ns, aid: True)
    monkeypatch.setattr(R.secrets, "delete", lambda ns, aid: deleted.__setitem__("n", deleted["n"] + 1))

    R.reconcile_runtime(spec={"accountId": "acct"}, name="acct", namespace="ns",
                        status={}, patch=patch_obj, logger=stub_logger)

    assert deleted["n"] == 1


def test_no_delete_when_secret_absent(monkeypatch, patch_obj, stub_logger):
    deleted = {"n": 0}
    monkeypatch.setattr(R.kube, "get_replicas", lambda ns, aid: 0)
    monkeypatch.setattr(R.secrets, "exists", lambda ns, aid: False)
    monkeypatch.setattr(R.secrets, "delete", lambda ns, aid: deleted.__setitem__("n", deleted["n"] + 1))

    R.reconcile_runtime(spec={"accountId": "acct"}, name="acct", namespace="ns",
                        status={}, patch=patch_obj, logger=stub_logger)

    assert deleted["n"] == 0


def test_replacement_keeps_secret(monkeypatch, patch_obj, stub_logger):
    # replicas==1 but no Ready pod yet (replacement gap): deleting the Secret would break
    # the new pod's envFrom, so it must NOT be deleted.
    deleted = {"n": 0}
    monkeypatch.setattr(R.kube, "get_replicas", lambda ns, aid: 1)
    monkeypatch.setattr(R.kube, "current_ready_pod_ip", lambda ns, aid: None)
    monkeypatch.setattr(R.secrets, "exists", lambda ns, aid: True)
    monkeypatch.setattr(R.secrets, "delete", lambda ns, aid: deleted.__setitem__("n", deleted["n"] + 1))

    R.reconcile_runtime(spec={"accountId": "acct"}, name="acct", namespace="ns",
                        status={"podIP": "10.0.0.1"}, patch=patch_obj, logger=stub_logger)

    assert deleted["n"] == 0
    assert patch_obj.status["phase"] == "Waking"
