"""The runner pod must not carry platform signing secrets.

Regression cover for the pre-launch finding: the runner's container did
``envFrom: secretRef: priva-shared-secret``, which holds the platform JWT
signing secret and the api-key HMAC secret. The runner is the pod that executes
untrusted tenant code, so the tenant's own agent could read them straight out of
its process environment (plain ``env``, or /proc/self/environ via the file API)
and sign ``{"sub": "admin"}`` — the platform authenticates that as the real
admin account, since role is re-read from the DB but the subject is trusted.

The terminal pod had always omitted this Secret on purpose; the runner was the
un-fixed twin.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import priva_operator.kube as kube
from priva_common import service_identity, service_token


def _settings():
    return SimpleNamespace(kubernetes=SimpleNamespace(
        runner_service_port=8091, terminal_service_port=8092,
        runner_uid=10001, runner_gid=10001,
        runner_image="priva/agent-runner:test", runner_image_pull_secret="",
        runner_cpu_cores=2.0, runner_memory_mb=2048, runner_storage_gb=10,
        terminal_resource_percent=0, terminal_max_sessions=2,
        terminal_idle_timeout_seconds=1800, terminal_max_lifetime_seconds=14400,
        terminal_output_rate_limit_bytes_per_sec=262144,
        terminal_output_burst_bytes=1048576,
        terminal_output_buffer_bytes=1048576,
        terminal_tmp_size_limit="256Mi",
    ))


@pytest.fixture
def runner_container():
    body = kube._deployment_body(
        namespace="priva-cloud", account_id="acc-tenant", username="tenant",
        image="priva/agent-runner:test", pull_policy="IfNotPresent",
        settings=_settings(), owner={"uid": "x"},
        spec={}, mount_info=kube.MountInfo(
            kind="shared_pvc_subpath", claim="priva-export", sub_path="acc-tenant"),
        defaults=SimpleNamespace(cpu_cores=2.0, memory_mb=2048, storage_gb=10,
                                 terminal_resource_percent=0, terminal_max_sessions=2,
                                 terminal_idle_timeout_seconds=1800,
                                 terminal_max_lifetime_seconds=14400),
    )
    return body["spec"]["template"]["spec"]["containers"][0]


def test_runner_does_not_mount_the_shared_platform_secret(runner_container):
    sources = runner_container.get("envFrom", [])
    assert not [s for s in sources if "secretRef" in s], (
        "the runner must not envFrom any Secret — priva-shared-secret carries the "
        "platform JWT signing key and the api-key HMAC secret"
    )
    # the non-secret bootstrap ConfigMap is still expected
    assert [s for s in sources if "configMapRef" in s]


def test_runner_env_carries_no_signing_material(runner_container):
    env = {e["name"]: e.get("value", "") for e in runner_container["env"]}
    assert "PRIVA_AUTH__JWT_SECRET" not in env
    assert "PRIVA_DATASPINE__API_KEY_HMAC_SECRET" not in env
    assert "PRIVA_SERVICE_IDENTITY__PRIVATE_KEY" not in env
    # what it DOES get: the public half only
    assert "PUBLIC KEY" in env["PRIVA_SERVICE_IDENTITY__PUBLIC_KEY"]
    assert "PRIVATE" not in env["PRIVA_SERVICE_IDENTITY__PUBLIC_KEY"]


def test_enforced_hook_policy_mount_is_required():
    """The managed-policy ConfigMap was mounted optional:True, so a data-spine
    blip during the operator's render left it absent and the pod booted with an
    empty policy dir — every enforced admin hook silently stopped firing while
    the runner still reported Ready."""
    body = kube._deployment_body(
        namespace="priva-cloud", account_id="acc-tenant", username="tenant",
        image="priva/agent-runner:test", pull_policy="IfNotPresent",
        settings=_settings(), owner={"uid": "x"}, spec={},
        mount_info=kube.MountInfo(kind="shared_pvc_subpath", claim="c", sub_path="acc-tenant"),
        defaults=SimpleNamespace(cpu_cores=2.0, memory_mb=2048, storage_gb=10,
                                 terminal_resource_percent=0, terminal_max_sessions=2,
                                 terminal_idle_timeout_seconds=1800,
                                 terminal_max_lifetime_seconds=14400),
    )
    volumes = body["spec"]["template"]["spec"]["volumes"]
    policy = next(v for v in volumes if v["name"] == kube.MANAGED_POLICY_VOLUME)
    assert policy["configMap"]["optional"] is False


def test_runner_service_token_is_scoped_to_its_own_account(runner_container):
    env = {e["name"]: e.get("value", "") for e in runner_container["env"]}
    principal = service_token.verify_service(env["PRIVA_DATASPINE__SERVICE_TOKEN"])
    assert principal.svc == "agent-runner"
    assert principal.account_id == "acc-tenant"
    assert not principal.is_control_plane


def test_runner_token_is_deterministic_so_converge_does_not_restart_pods(runner_container):
    """ensure_runtime_objects re-renders the template on every converge and
    replaces the Deployment; a token that changed each render would restart the
    pod on every reconcile."""
    again = service_token.mint("agent-runner", account_id="acc-tenant")
    env = {e["name"]: e.get("value", "") for e in runner_container["env"]}
    assert env["PRIVA_DATASPINE__SERVICE_TOKEN"] == again


def test_public_key_alone_cannot_mint(monkeypatch):
    """The reason the runner token had to become asymmetric: under HS256 the
    key that verifies is the key that signs, so shipping a verify key to the
    runner shipped the ability to forge tokens for any account."""
    from priva_common.config import get_settings

    pub = service_identity.public_key()
    settings = get_settings().service_identity
    saved = (settings.private_key, settings.public_key)
    try:
        # a runner pod: public key only, no signing material
        settings.private_key, settings.public_key = None, pub
        assert not service_identity.has_private_key()
        assert service_identity.public_key() == pub  # can still verify
        with pytest.raises(RuntimeError, match="no signing key"):
            service_token.mint("control-panel")
        with pytest.raises(RuntimeError, match="no signing key"):
            from priva_common import runner_token
            runner_token.mint("any-account", "any-user")
    finally:
        settings.private_key, settings.public_key = saved
        service_token.reset_cache()
