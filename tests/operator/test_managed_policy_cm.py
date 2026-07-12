"""Operator renders the global managed-policy ConfigMap (rev-5 D2).

Verifies the digest-guarded create/skip/replace logic + enforced-only filtering
without a real cluster, by faking CoreV1Api and the data-spine client.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from kubernetes import client as k8s

import priva_operator.kube as kube


def _policy(pid, enforced=True, hook_type="command", body="print('x')\n", h="a1b2c3d4"):
    return SimpleNamespace(
        id=pid, hook_type=hook_type, events=["PreToolUse"], matcher="Bash",
        interpreter="python3", script_body=body, content_hash=h,
        timeout_seconds=10, allowed_env_vars=[], enforced=enforced, enabled=True, url="",
    )


class _FakeCore:
    def __init__(self, existing=None):
        self.store = {}
        if existing is not None:
            self.store["claude-managed-policy"] = existing
        self.creates = 0
        self.replaces = 0

    def read_namespaced_config_map(self, name, ns):
        if name not in self.store:
            raise k8s.ApiException(status=404)
        return self.store[name]

    def create_namespaced_config_map(self, ns, body):
        self.creates += 1
        self.store[body["metadata"]["name"]] = _cm_obj(body)
        return None

    def replace_namespaced_config_map(self, name, ns, body):
        self.replaces += 1
        self.store[name] = _cm_obj(body)
        return None


def _cm_obj(body):
    return SimpleNamespace(
        data=body["data"],
        metadata=SimpleNamespace(
            annotations=body["metadata"].get("annotations", {}),
            resource_version="1",
        ),
    )


@pytest.fixture()
def wire(monkeypatch):
    def _install(policies, existing=None):
        fake = _FakeCore(existing)
        monkeypatch.setattr(kube, "core", lambda: fake)
        client = SimpleNamespace(hook_policies=SimpleNamespace(list=lambda enabled_only=True: policies))
        import priva_common.dataplane as dp
        monkeypatch.setattr(dp, "get_client", lambda: client)
        return fake
    return _install


def test_creates_when_absent_and_filters_enforced(wire):
    fake = wire([_policy("block-dangerous-bash"), _policy("lint", enforced=False)])
    changed = kube.ensure_managed_policy_configmap("ns")
    assert changed is True and fake.creates == 1
    data = fake.store["claude-managed-policy"].data
    assert any("block-dangerous-bash" in k for k in data)
    assert not any("lint" in k for k in data)  # non-enforced excluded
    assert "managed-settings.json" in data and "_wrapper.py" in data


def test_skips_when_digest_unchanged(wire):
    fake = wire([_policy("block-dangerous-bash")])
    assert kube.ensure_managed_policy_configmap("ns") is True   # create
    assert kube.ensure_managed_policy_configmap("ns") is False  # digest match → skip
    assert fake.creates == 1 and fake.replaces == 0


def test_replaces_and_keeps_prior_generation_on_change(wire, monkeypatch):
    fake = wire([_policy("block-dangerous-bash", h="aaaa0000", body="v1\n")])
    kube.ensure_managed_policy_configmap("ns")  # create gen A

    # policy edited → new hash/body
    client = SimpleNamespace(hook_policies=SimpleNamespace(
        list=lambda enabled_only=True: [_policy("block-dangerous-bash", h="bbbb1111", body="v2\n")]))
    import priva_common.dataplane as dp
    monkeypatch.setattr(dp, "get_client", lambda: client)

    assert kube.ensure_managed_policy_configmap("ns") is True
    assert fake.replaces == 1
    data = fake.store["claude-managed-policy"].data
    assert any("bbbb1111" in k for k in data)  # new generation
    assert any("aaaa0000" in k for k in data)  # prior generation retained (grace)


def test_no_enforced_rows_renders_empty_hooks(wire):
    fake = wire([_policy("lint", enforced=False)])
    assert kube.ensure_managed_policy_configmap("ns") is True
    import json
    settings = json.loads(fake.store["claude-managed-policy"].data["managed-settings.json"])
    assert settings.get("hooks", {}) == {}
