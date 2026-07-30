"""ensure_runtime_objects create-or-converge: a Deployment that already exists is
replaced with the current template — but ONLY while scaled to 0 (strategy=Recreate
means a template write restarts a running pod; the policy is apply-on-next-restart).
Create-only (the old behavior) stranded tenants born under an older operator without
later template additions such as the managed-policy mount. The kube client and the
storage backend are faked, so no cluster is touched."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import priva_operator.kube as kube


class _FakeApps:
    def __init__(self, existing=None, replace_error=None, create_error=None):
        self.existing = existing
        self.replace_error = replace_error
        self.create_error = create_error
        self.created = []
        self.replaced = []

    def read_namespaced_deployment(self, name, namespace, **_kwargs):
        if self.existing is None:
            raise kube.client.ApiException(status=404)
        return self.existing

    def create_namespaced_deployment(self, namespace, body):
        self.created.append(body)
        if self.create_error is not None:
            raise self.create_error

    def replace_namespaced_deployment(self, name, namespace, body):
        self.replaced.append(body)
        if self.replace_error is not None:
            raise self.replace_error


class _FakeCore:
    def create_namespaced_service(self, namespace, body):
        pass


def _existing(replicas: int, *, drain_token: str | None = None):
    containers = []
    if drain_token:
        containers = [
            SimpleNamespace(
                name="agent-runner",
                env=[
                    SimpleNamespace(
                        name="PRIVA_INTERNAL_DRAIN_TOKEN",
                        value=drain_token,
                    )
                ],
            )
        ]
    return SimpleNamespace(
        spec=SimpleNamespace(
            replicas=replicas,
            template=SimpleNamespace(
                spec=SimpleNamespace(containers=containers)
            ),
        ),
        metadata=SimpleNamespace(resource_version="42", annotations={
            "priva.io/terminal-resource-percent": "0",
        }),
    )


def _settings():
    return SimpleNamespace(kubernetes=SimpleNamespace(
        runner_service_port=8080, runner_uid=10001, runner_gid=10001,
        runner_image_pull_secret="", runner_storage_gb=1))


def _run(monkeypatch, apps: _FakeApps):
    monkeypatch.setattr(kube, "apps", lambda: apps)
    monkeypatch.setattr(kube, "core", lambda: _FakeCore())
    monkeypatch.setattr(kube, "get_backend", lambda s: SimpleNamespace(
        provision=lambda aid, gb: kube.MountInfo(
            kind="shared_pvc_subpath", claim="data", sub_path=aid)))
    monkeypatch.setattr(kube, "resolve_resources", lambda spec, s, d=None: {})
    monkeypatch.setattr(kube, "allocation_hash", lambda *a, **k: "v1:test")
    owner = {"apiVersion": "v1", "kind": "T", "name": "acct", "uid": "u1"}
    kube.ensure_runtime_objects("ns", "acct", "user", "img:dev", "IfNotPresent",
                                _settings(), owner, spec={})
    return apps


def test_creates_when_absent(monkeypatch):
    apps = _run(monkeypatch, _FakeApps(existing=None))
    assert len(apps.created) == 1 and not apps.replaced
    mounts = apps.created[0]["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]
    assert any(m["mountPath"] == kube.MANAGED_POLICY_MOUNT for m in mounts)


def test_converges_existing_while_at_zero(monkeypatch):
    apps = _run(monkeypatch, _FakeApps(existing=_existing(replicas=0)))
    assert not apps.created and len(apps.replaced) == 1
    body = apps.replaced[0]
    # Replace must carry the live resourceVersion and preserve replicas=0.
    assert body["metadata"]["resourceVersion"] == "42"
    assert body["spec"]["replicas"] == 0
    mounts = body["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]
    assert any(m["mountPath"] == kube.MANAGED_POLICY_MOUNT for m in mounts)


def test_dormant_converge_reuses_the_applied_drain_capability(monkeypatch):
    apps = _run(
        monkeypatch,
        _FakeApps(existing=_existing(replicas=0, drain_token="stable-capability")),
    )

    env = {
        item["name"]: item.get("value")
        for item in apps.replaced[0]["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env["PRIVA_INTERNAL_DRAIN_TOKEN"] == "stable-capability"
    assert kube._deployment_env_value(
        apps.existing,
        "agent-runner",
        "PRIVA_INTERNAL_DRAIN_TOKEN",
    ) == "stable-capability"


def test_never_touches_a_running_deployment(monkeypatch):
    # replicas 1 = a live (possibly mid-session) pod — reconcile must not restart it.
    apps = _run(monkeypatch, _FakeApps(existing=_existing(replicas=1)))
    assert not apps.created and not apps.replaced


def test_replace_conflict_is_not_treated_as_converged(monkeypatch):
    conflict = kube.client.ApiException(status=409)
    apps = _FakeApps(existing=_existing(replicas=0), replace_error=conflict)

    with pytest.raises(kube.client.ApiException) as raised:
        _run(monkeypatch, apps)

    assert raised.value.status == 409
    assert len(apps.replaced) == 1


def test_create_conflict_is_not_treated_as_converged(monkeypatch):
    conflict = kube.client.ApiException(status=409)
    apps = _FakeApps(existing=None, create_error=conflict)

    with pytest.raises(kube.client.ApiException) as raised:
        _run(monkeypatch, apps)

    assert raised.value.status == 409
    assert len(apps.created) == 1


def test_terminal_create_conflict_is_not_treated_as_converged(monkeypatch):
    conflict = kube.client.ApiException(status=409)
    apps = _FakeApps(existing=None, create_error=conflict)
    settings = SimpleNamespace(kubernetes=SimpleNamespace(
        terminal_service_port=8092,
        terminal_resource_percent=25,
        runner_storage_gb=1,
    ))
    monkeypatch.setattr(kube, "apps", lambda: apps)
    monkeypatch.setattr(kube, "core", lambda: _FakeCore())
    monkeypatch.setattr(kube, "get_backend", lambda _settings: SimpleNamespace(
        provision=lambda account_id, _gb: kube.MountInfo(
            kind="shared_pvc_subpath",
            claim="data",
            sub_path=account_id,
        )
    ))
    monkeypatch.setattr(
        kube,
        "_terminal_deployment_body",
        lambda *_args, **_kwargs: {
            "metadata": {"name": "term-acct"},
            "spec": {"replicas": 0},
        },
    )
    owner = {"apiVersion": "v1", "kind": "T", "name": "acct", "uid": "u1"}

    with pytest.raises(kube.client.ApiException) as raised:
        kube.ensure_terminal_objects(
            "ns",
            "acct",
            "user",
            "img:dev",
            "IfNotPresent",
            settings,
            owner,
            spec={},
        )

    assert raised.value.status == 409
    assert len(apps.created) == 1


class _RunnerDormantTerminalRunningApps(_FakeApps):
    def __init__(self, *, mismatch=False):
        super().__init__(_existing(replicas=0))
        self.terminal = _existing(replicas=1)
        self.existing.metadata.annotations["priva.io/terminal-resource-percent"] = "25"
        self.terminal.metadata.annotations["priva.io/terminal-resource-percent"] = (
            "50" if mismatch else "25")

    def read_namespaced_deployment(self, name, namespace, **_kwargs):
        return self.terminal if name.startswith("term-") else self.existing


def test_dormant_runner_keeps_matching_allocation_while_terminal_runs(monkeypatch):
    apps = _run(monkeypatch, _RunnerDormantTerminalRunningApps())
    assert not apps.created and not apps.replaced


def test_mismatched_live_allocation_fails_instead_of_overcommitting(monkeypatch):
    apps = _RunnerDormantTerminalRunningApps(mismatch=True)
    with pytest.raises(RuntimeError, match="allocation generation mismatch"):
        _run(monkeypatch, apps)
