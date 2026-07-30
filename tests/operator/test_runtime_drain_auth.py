from __future__ import annotations

import priva_operator.reconcile as reconcile
from priva_common import drain_token


class _Response:
    status_code = 200

    def json(self):
        return {"activity_revision": 17}


class _Client:
    calls = []

    def __init__(self, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response()


def test_force_drain_uses_per_pod_capability_without_revision(
    monkeypatch, stub_logger,
):
    _Client.calls.clear()
    monkeypatch.setattr(reconcile.httpx, "Client", _Client)

    revision = reconcile._begin_runner_drain(
        "10.0.0.7",
        8091,
        None,
        stub_logger,
        force=True,
        capability="old-pod-capability",
    )

    assert revision == 17
    _, request = _Client.calls[0]
    assert request["params"] == {"force": "true"}
    assert request["headers"] == {
        drain_token.HEADER: "old-pod-capability",
    }


def test_normal_terminal_drain_keeps_revision_cas(monkeypatch, stub_logger):
    _Client.calls.clear()
    monkeypatch.setattr(reconcile.httpx, "Client", _Client)

    assert reconcile._begin_terminal_drain(
        "10.0.0.8",
        8092,
        9,
        stub_logger,
        capability="terminal-capability",
    )

    _, request = _Client.calls[0]
    assert request["params"] == {"revision": 9}
    assert request["headers"] == {
        drain_token.HEADER: "terminal-capability",
    }


def test_drain_without_per_pod_capability_fails_without_sending_request(
    monkeypatch, stub_logger,
):
    _Client.calls.clear()
    monkeypatch.setattr(reconcile.httpx, "Client", _Client)

    assert reconcile._begin_runner_drain(
        "10.0.0.7", 8091, 3, stub_logger, capability=None
    ) is None
    assert not reconcile._begin_terminal_drain(
        "10.0.0.8", 8092, 3, stub_logger, capability=""
    )
    assert _Client.calls == []
