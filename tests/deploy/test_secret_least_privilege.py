"""Each workload may hold only the secrets its own code reads.

There used to be one `priva-shared-secret` holding the platform JWT secret and
the api-key HMAC secret, envFrom'd by every service — and by every tenant
agent-runner, which is what put platform signing material inside the pod that
executes untrusted tenant code.

This renders the chart and asserts the split holds, so re-widening a secret
fails here rather than in a review.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[2] / "deploy" / "helm" / "priva-cloud"

# workload -> exactly the secret keys its source reads
EXPECTED: dict[str, set[str]] = {
    # signs platform login JWTs (services/auth.py) + mints runner tokens
    "control-panel": {"PRIVA_AUTH__JWT_SECRET", "PRIVA_SERVICE_IDENTITY__PRIVATE_KEY",
                      "PRIVA_SERVICE_IDENTITY__PUBLIC_KEY",
                      "PRIVA_SERVICE_IDENTITY__ADDITIONAL_PUBLIC_KEYS"},
    # the only process that indexes api keys or encrypts stored credentials.
    # Verifies service tokens with the PUBLIC half — and MUST actually be given
    # it: an earlier revision of this split handed data-spine neither key, so it
    # fell back to an ephemeral in-process keypair and rejected every caller
    # while its TCP readiness probe stayed green.
    "data-spine": {"PRIVA_DATASPINE__API_KEY_HMAC_SECRET", "PRIVA_FERNET_KEY",
                   "PRIVA_SERVICE_IDENTITY__PUBLIC_KEY",
                   "PRIVA_SERVICE_IDENTITY__ADDITIONAL_PUBLIC_KEYS"},
    # token minters (the public half rides along in the same Secret)
    "operator": {"PRIVA_SERVICE_IDENTITY__PRIVATE_KEY", "PRIVA_SERVICE_IDENTITY__PUBLIC_KEY",
                 "PRIVA_SERVICE_IDENTITY__ADDITIONAL_PUBLIC_KEYS"},
    "scheduler": {"PRIVA_SERVICE_IDENTITY__PRIVATE_KEY", "PRIVA_SERVICE_IDENTITY__PUBLIC_KEY",
                  "PRIVA_SERVICE_IDENTITY__ADDITIONAL_PUBLIC_KEYS"},
    "channel-connector": {"PRIVA_SERVICE_IDENTITY__PRIVATE_KEY",
                          "PRIVA_SERVICE_IDENTITY__PUBLIC_KEY",
                          "PRIVA_SERVICE_IDENTITY__ADDITIONAL_PUBLIC_KEYS"},
    "postgres": set(),
}

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


@pytest.fixture(scope="module")
def rendered():
    out = subprocess.run(
        ["helm", "template", "priva", str(CHART)],
        capture_output=True, text=True, check=True,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def _secret_keys(rendered) -> dict[str, set[str]]:
    return {
        d["metadata"]["name"]: set(d.get("stringData", {}))
        for d in rendered if d.get("kind") == "Secret"
    }


def _secret_keys_values(rendered) -> dict[str, dict[str, str]]:
    return {
        d["metadata"]["name"]: d.get("stringData", {})
        for d in rendered if d.get("kind") == "Secret"
    }


def _exposure(rendered) -> dict[str, set[str]]:
    keys = _secret_keys(rendered)
    out: dict[str, set[str]] = {}
    for d in rendered:
        if d.get("kind") != "Deployment":
            continue
        held: set[str] = set()
        for c in d["spec"]["template"]["spec"]["containers"]:
            for src in c.get("envFrom") or []:
                if "secretRef" in src:
                    held |= keys.get(src["secretRef"]["name"], set())
            for e in c.get("env") or []:
                ref = (e.get("valueFrom") or {}).get("secretKeyRef")
                if ref:
                    held.add(ref["key"])
        out[d["metadata"]["name"]] = held
    return out


def test_each_workload_holds_only_what_it_reads(rendered):
    actual = _exposure(rendered)
    for name, expected in EXPECTED.items():
        got = {k for k in actual.get(name, set()) if k.startswith("PRIVA_")}
        assert got == expected, f"{name} secret exposure drifted: {got} != {expected}"


def test_the_signing_key_never_reaches_data_spine(rendered):
    """data-spine only verifies service tokens. Holding the signing key would let
    a data-spine compromise mint control-plane identities."""
    assert "PRIVA_SERVICE_IDENTITY__PRIVATE_KEY" not in _exposure(rendered)["data-spine"]


def test_data_spine_can_actually_verify_control_plane_tokens(rendered):
    """The release-blocking half of the split: least privilege is only correct if
    the verifier is still given something to verify WITH. Sign with the rendered
    private key, verify with the key data-spine is rendered."""
    import jwt
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    secrets = _secret_keys_values(rendered)
    private = secrets["priva-shared-secret"]["PRIVA_SERVICE_IDENTITY__PRIVATE_KEY"]
    public = secrets["priva-data-spine-secret"]["PRIVA_SERVICE_IDENTITY__PUBLIC_KEY"]

    if "BEGIN CERTIFICATE" in public:  # sprig ships the public half inside a cert
        public = x509.load_pem_x509_certificate(public.encode()).public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    token = jwt.encode({"typ": "service", "svc": "control-panel"}, private, algorithm="RS256")
    assert jwt.decode(token, public, algorithms=["RS256"])["svc"] == "control-panel"


def test_the_keypair_halves_are_a_matched_set(rendered):
    """Preserving one half across an upgrade while regenerating the other would
    silently break every gRPC call in the cluster."""
    secrets = _secret_keys_values(rendered)
    shared = secrets["priva-shared-secret"]
    assert shared["PRIVA_SERVICE_IDENTITY__PUBLIC_KEY"] == \
        secrets["priva-data-spine-secret"]["PRIVA_SERVICE_IDENTITY__PUBLIC_KEY"]
    assert shared["PRIVA_SERVICE_IDENTITY__ADDITIONAL_PUBLIC_KEYS"] == \
        secrets["priva-data-spine-secret"]["PRIVA_SERVICE_IDENTITY__ADDITIONAL_PUBLIC_KEYS"]


def test_the_fernet_key_reaches_only_data_spine(rendered):
    """The connector deliberately never decrypts — data-spine hands it the
    plaintext app_secret over gRPC instead."""
    holders = [n for n, keys in _exposure(rendered).items() if "PRIVA_FERNET_KEY" in keys]
    assert holders == ["data-spine"]


def test_login_jwt_secret_reaches_only_the_control_panel(rendered):
    holders = [n for n, keys in _exposure(rendered).items() if "PRIVA_AUTH__JWT_SECRET" in keys]
    assert holders == ["control-panel"]
