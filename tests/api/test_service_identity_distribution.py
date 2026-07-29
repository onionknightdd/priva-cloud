"""Verify-only workloads must be given a key to verify WITH."""
from __future__ import annotations
import pytest
from priva_common import service_identity as si
from priva_common.config import get_settings


@pytest.fixture
def identity():
    s = get_settings().service_identity
    saved = (s.private_key, s.public_key)
    yield s
    s.private_key, s.public_key = saved


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


def test_the_gate_is_wired_into_the_services_that_mint(identity, monkeypatch):
    """`assert_configured` existing is not the same as it being CALLED. Reverting
    the wiring must fail something — previously the whole suite stayed green."""
    import inspect

    from priva_control_panel.app import create_app as cp_create_app
    from priva_scheduler.api import create_app as sched_create_app
    from priva_data_spine.server import serve as ds_serve

    for fn, label in ((cp_create_app, "control-panel"), (sched_create_app, "scheduler"),
                      (ds_serve, "data-spine")):
        src = inspect.getsource(fn)
        assert "assert_configured" in src or "assert_service_identity_configured" in src, (
            f"{label} does not gate its service identity at startup")
