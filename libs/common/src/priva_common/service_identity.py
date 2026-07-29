"""Asymmetric workload identity — the control plane signs, everyone verifies.

Replaces the HS256 shared-secret seam in which *verifying* a token required the
same secret that *mints* one, so every pod holding ``priva-shared-secret``
(including tenant agent-runners) could forge a token for any account.

One RSA keypair backs two token types, tagged by the ``typ`` claim:

* ``runner``  — short-TTL, per-request; control-plane → agent-runner / connector
* ``service`` — workload identity presented to data-spine and the scheduler API

Only control-plane pods are given ``PRIVA_SERVICE_IDENTITY__PRIVATE_KEY``. The
agent-runner receives the public key plus a pre-minted, account-scoped service
token, so a tenant reading their own pod env gains no signing power — only the
capability their own account already has.

Unset keys => an ephemeral in-process keypair and a loud warning. Single-process
dev and the test suite keep working (mint and verify share the pair), while a
mis-provisioned multi-pod deployment fails *closed*: the control-panel's
ephemeral private key does not match the runner's ephemeral public key, so every
cross-pod token is rejected. That is deliberately unlike shipping a fixed default
key, which would verify everywhere and silently look healthy.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from jose import JWTError, jwt

from .config import get_settings
from .logging import get_app_logger

logger = get_app_logger(__name__)

# Pinned, never read from the token header: accepting a caller-chosen algorithm
# is the classic alg-confusion hole (an attacker holding only the PUBLIC key
# signs HS256 with that key as the HMAC secret, and a permissive verifier
# accepts it). Verification pins this list.
ALGORITHM = "RS256"

_ephemeral: tuple[str, str] | None = None
_ephemeral_lock = threading.Lock()
_derived: dict[str, str] = {}  # private PEM -> derived public PEM


def _generate_ephemeral() -> tuple[str, str]:
    """(private_pem, public_pem) generated once per process."""
    global _ephemeral
    with _ephemeral_lock:
        if _ephemeral is not None:
            return _ephemeral
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode().strip()
        public_pem = key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode().strip()
        logger.warning(
            "PRIVA_SERVICE_IDENTITY__{PRIVATE,PUBLIC}_KEY unset — generated an "
            "EPHEMERAL in-process keypair. Fine for local dev and tests; in a "
            "multi-pod deployment every cross-pod token will be rejected until a "
            "real keypair is provisioned."
        )
        _ephemeral = (private_pem, public_pem)
        return _ephemeral


def private_key() -> str:
    """Signing key — control-plane pods only.

    A pod provisioned with a public key but no private key is a verify-only
    workload (the agent-runner). Refuse to fall back to the ephemeral pair
    there: such tokens would fail verification anyway, and a loud error at the
    call site beats a confusing signature mismatch three hops away.
    """
    s = get_settings().service_identity
    configured = (s.private_key or "").strip()
    if configured:
        return configured
    if (s.public_key or "").strip():
        raise RuntimeError(
            "no signing key on this workload: PRIVA_SERVICE_IDENTITY__PUBLIC_KEY is set "
            "without a private key, i.e. this pod is provisioned to verify only."
        )
    return _generate_ephemeral()[0]


def _derive_public(private_pem: str) -> str:
    cached = _derived.get(private_pem)
    if cached is not None:
        return cached
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    pub = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode().strip()  # normalised: the same key must render one exact string,
    # or the operator would rewrite the runner's env (and restart it) each converge.
    _derived[private_pem] = pub
    return pub


def _normalize_public(pem: str) -> str:
    """Accept either a bare public key or an X.509 certificate carrying one.

    Helm's sprig can generate an RSA private key but cannot export its public
    half; ``genSelfSignedCert`` is the only way to get both from one template
    call, and its ``Cert`` is where the public key lives. Normalising here keeps
    that a packaging detail instead of leaking into every verifier.
    """
    if "BEGIN CERTIFICATE" not in pem:
        return pem
    cached = _derived.get(pem)
    if cached is not None:
        return cached
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    cert = x509.load_pem_x509_certificate(pem.encode())
    spki = cert.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode().strip()
    _derived[pem] = spki
    return spki


def public_key() -> str:
    """Verification key — every pod, safe to ship in a ConfigMap or inline env.

    Derived from the private key when only that is configured (signing pods);
    verify-only pods such as data-spine are given the public half explicitly.
    """
    s = get_settings().service_identity
    configured = (s.public_key or "").strip()
    if configured:
        return _normalize_public(configured)
    private = (s.private_key or "").strip()
    if private:
        return _derive_public(private)
    return _generate_ephemeral()[1]


# Opt IN to the ephemeral keypair. Absent => production posture (fail-closed),
# matching PRIVA_ALLOW_DEV_FERNET. Single-process dev and the test suite set it;
# nothing in deploy/ does.
_ALLOW_EPHEMERAL_ENV = "PRIVA_ALLOW_EPHEMERAL_IDENTITY"


def _allow_ephemeral() -> bool:
    import os

    return os.environ.get(_ALLOW_EPHEMERAL_ENV, "").strip().lower() in ("1", "true", "yes")


def assert_configured(*, signing: bool = False) -> None:
    """Boot-time gate for any process that handles service identity.

    ``signing=True`` additionally requires a real private key — for the four
    control-plane services that MINT tokens, where an ephemeral key means every
    token they issue is unverifiable by its recipient.

    Without this, a pod given neither key silently falls back to an ephemeral
    in-process keypair: it then rejects every token any other pod signs, while a
    TCP readiness probe keeps reporting healthy. Fail at startup instead — a
    crash-looping pod is diagnosable, a silently-rejecting one is not.
    """
    s = get_settings().service_identity
    if (not (s.public_key or "").strip() and not (s.private_key or "").strip()
            and not _allow_ephemeral()):
        raise RuntimeError(
            "No service identity configured: set PRIVA_SERVICE_IDENTITY__PUBLIC_KEY "
            "(verify-only workloads such as data-spine) or "
            "PRIVA_SERVICE_IDENTITY__PRIVATE_KEY (control-plane signers). Refusing to "
            "start with an ephemeral keypair, which would reject every peer's token."
        )
    if signing and not has_private_key() and not _allow_ephemeral():
        raise RuntimeError(
            "this workload mints tokens but has no PRIVA_SERVICE_IDENTITY__PRIVATE_KEY; "
            "an ephemeral key would make every token it issues unverifiable."
        )


def has_private_key() -> bool:
    """True when this pod was provisioned as a control-plane (signing) workload."""
    return bool((get_settings().service_identity.private_key or "").strip())


def sign(claims: dict[str, Any], *, typ: str, ttl_seconds: int | None) -> str:
    """Sign a token of type ``typ``.

    ``ttl_seconds=None`` mints a non-expiring token and omits ``iat``, which
    keeps the output byte-identical across calls (RS256 is PKCS#1 v1.5, i.e.
    deterministic). The operator relies on that: a runner's account-scoped
    service token is re-derived on every converge and must not churn the
    Deployment env, which would restart the pod each reconcile.
    """
    payload = {**claims, "typ": typ}
    if ttl_seconds is not None:
        now = int(time.time())
        payload["iat"] = now
        payload["exp"] = now + ttl_seconds
    return jwt.encode(payload, private_key(), algorithm=ALGORITHM)


def verify(token: str, *, typ: str) -> dict[str, Any]:
    """Verify a token and assert its type. Raises ``ValueError`` on any failure.

    The ``typ`` assertion stops cross-use: a short-TTL runner token must never
    be replayed as a data-spine service identity, and vice versa.
    """
    try:
        claims = jwt.decode(token, public_key(), algorithms=[ALGORITHM])
    except JWTError as exc:
        raise ValueError(f"invalid token: {exc}") from exc
    if claims.get("typ") != typ:
        raise ValueError(f"wrong token type: expected {typ!r}, got {claims.get('typ')!r}")
    return claims


__all__ = [
    "ALGORITHM",
    "assert_configured",
    "has_private_key",
    "private_key",
    "public_key",
    "sign",
    "verify",
]
