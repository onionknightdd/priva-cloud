from __future__ import annotations

import priva_operator.kube as kube


class _Apps:
    def __init__(self):
        self.calls = []

    def patch_namespaced_deployment_scale(
        self, name, namespace, body, **kwargs,
    ):
        self.calls.append((name, namespace, body, kwargs))


def test_runner_and_terminal_scale_calls_have_strict_request_timeout(monkeypatch):
    api = _Apps()
    monkeypatch.setattr(kube, "apps", lambda: api)

    kube.scale("ns", "acct", 0)
    kube.scale_terminal("ns", "acct", 1)

    assert len(api.calls) == 2
    for _, _, _, kwargs in api.calls:
        assert kwargs["_request_timeout"] == kube._KUBE_REQUEST_TIMEOUT
