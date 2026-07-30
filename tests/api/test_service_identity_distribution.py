"""Verify-only workloads must be given a key to verify WITH."""
from __future__ import annotations
import pytest
from priva_common import service_identity as si
from priva_common.config import get_settings


@pytest.fixture
def identity():
    s = get_settings().service_identity
    saved = (s.private_key, s.public_key, list(s.additional_public_keys))
    yield s
    s.private_key, s.public_key, s.additional_public_keys = saved


def test_assert_configured_refuses_an_ephemeral_only_pod(identity, monkeypatch):
    """data-spine given neither key fell back to an ephemeral keypair: it then
    rejected every peer's token while TCP readiness stayed green.

    Production posture — the suite sets PRIVA_ALLOW_EPHEMERAL_IDENTITY so
    single-process runs work, so this test must drop it to exercise the gate the
    deployment actually relies on.
    """
    monkeypatch.delenv("PRIVA_ALLOW_EPHEMERAL_IDENTITY", raising=False)
    identity.private_key, identity.public_key = None, None
    with pytest.raises(RuntimeError, match="No service identity configured"):
        si.assert_configured()


def test_a_verify_only_pod_accepts_control_plane_tokens(identity):
    priv = si._generate_ephemeral()[0]
    identity.private_key, identity.public_key = priv, None
    token = si.sign({"svc": "control-panel"}, typ="service", ttl_seconds=60)
    pub = si.public_key()

    identity.private_key, identity.public_key = None, pub   # now be data-spine
    si.assert_configured()
    assert si.verify(token, typ="service")["svc"] == "control-panel"


def test_a_certificate_pem_works_as_the_public_half(identity):
    """Helm ships the public key inside a genSelfSignedCert Cert."""
    import os
    import subprocess
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as f:
        key = subprocess.run(["openssl", "genpkey", "-algorithm", "RSA",
                              "-pkeyopt", "rsa_keygen_bits:2048"],
                             capture_output=True, text=True).stdout
        f.write(key)
        path = f.name
    cert = subprocess.run(["openssl", "req", "-new", "-x509", "-key", path,
                           "-days", "1", "-subj", "/CN=priva"],
                          capture_output=True, text=True).stdout
    os.unlink(path)

    identity.private_key, identity.public_key = key, None
    token = si.sign({"svc": "operator"}, typ="service", ttl_seconds=60)

    identity.private_key, identity.public_key = None, cert
    assert "BEGIN CERTIFICATE" in cert
    assert si.verify(token, typ="service")["svc"] == "operator"


def test_verifier_overlap_accepts_a_token_from_the_previous_signer(identity):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    def pair():
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        public = key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        return private, public

    old_private, old_public = pair()
    _, new_public = pair()
    identity.private_key, identity.public_key = old_private, None
    token = si.sign({"svc": "agent-runner"}, typ="service", ttl_seconds=None)

    identity.private_key = None
    identity.public_key = new_public
    identity.additional_public_keys = [old_public]

    assert si.verify(token, typ="service")["svc"] == "agent-runner"
    assert len(si.verification_keys()) == 2


def test_signing_workload_refuses_a_mismatched_private_public_pair(identity):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    identity.private_key = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    identity.public_key = other.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    with pytest.raises(RuntimeError, match="does not match"):
        si.assert_configured(signing=True)


def test_the_gate_is_wired_into_the_services_that_mint(identity, monkeypatch):
    """`assert_configured` existing is not the same as it being CALLED. Reverting
    the wiring must fail something — previously the whole suite stayed green."""
    import inspect

    from priva_control_panel.app import create_app as cp_create_app
    from priva_channel_connector.api import create_app as connector_create_app
    from priva_operator.entry import main as operator_main
    from priva_scheduler.api import create_app as sched_create_app
    from priva_data_spine.server import serve as ds_serve

    for fn, label in (
        (cp_create_app, "control-panel"),
        (connector_create_app, "channel-connector"),
        (operator_main, "operator"),
        (sched_create_app, "scheduler"),
        (ds_serve, "data-spine"),
    ):
        src = inspect.getsource(fn)
        assert "assert_configured" in src or "assert_service_identity_configured" in src, (
            f"{label} does not gate its service identity at startup")


@pytest.fixture
def declared_name():
    """Save/restore the workload's declared role (conftest sets one globally)."""
    s = get_settings().service_identity
    saved = s.service_name
    yield s
    s.service_name = saved


@pytest.mark.parametrize("name", ["", "   ", "agent-runner", "not-a-role"])
def test_a_signer_must_declare_a_real_control_plane_role(declared_name, name):
    """data-spine keys CONTROL_PLANE_ACL on this name, so an unconfigured signer
    would inherit whichever role the default happened to spell.

    Measured on the dev cluster: the scheduler and channel-connector Deployments
    never received PRIVA_SERVICE_IDENTITY__SERVICE_NAME, both presented as
    "control-panel", and the per-workload ACL split was inert for every
    control-plane workload. `agent-runner` is rejected too: it is a known role,
    but a tenant one, and it is never a signer.
    """
    declared_name.service_name = name
    with pytest.raises(RuntimeError, match="SERVICE_NAME"):
        si.assert_configured(signing=True)


def test_the_declared_role_gate_does_not_fire_for_verify_only_workloads(declared_name):
    """data-spine verifies but never mints, so it has no role to declare."""
    declared_name.service_name = ""
    si.assert_configured()  # must not raise


@pytest.mark.parametrize("role", ["control-panel", "operator", "scheduler",
                                  "channel-connector"])
def test_every_control_plane_role_is_accepted(declared_name, role):
    declared_name.service_name = role
    si.assert_configured(signing=True)


def test_minting_an_outbound_token_refuses_an_undeclared_role(declared_name):
    """Second gate, for any process that reaches an outbound call without the
    boot check. It must raise, never silently pick a role."""
    from priva_common import service_token

    service_token.reset_cache()
    declared_name.service_name = ""
    settings = get_settings()
    saved = settings.dataspine.service_token
    settings.dataspine.service_token = ""   # no operator-injected runner token
    try:
        with pytest.raises(RuntimeError, match="SERVICE_NAME"):
            service_token.current_token()
    finally:
        settings.dataspine.service_token = saved
        service_token.reset_cache()


def test_the_shipped_default_role_is_empty():
    """The regression guard for the defect itself.

    Every other test here sets service_name explicitly, so none of them notices
    if the field's DEFAULT goes back to naming a real role — which is exactly
    what made two Deployments silently authenticate as "control-panel".
    """
    from priva_common.config import ServiceIdentitySettings
    from priva_common.service_token import CONTROL_PLANE_ROLES, TENANT_ROLES

    default = ServiceIdentitySettings().service_name
    assert default == "", (
        f"service_name defaults to {default!r}; an unconfigured pod must have no "
        "identity at all, not a borrowed one"
    )
    assert default not in (CONTROL_PLANE_ROLES | TENANT_ROLES)
