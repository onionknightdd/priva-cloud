"""Workload identity presented to data-spine and the scheduler internal API.

A ``service`` token answers "which workload is calling, and on whose behalf":

    {"typ": "service", "svc": "control-panel"}              # full control plane
    {"typ": "service", "svc": "agent-runner", "account_id": "acc_123"}

Control-plane pods hold the private key and mint their own token on demand.
The agent-runner holds NO private key: the operator mints an account-scoped
token at provision time and injects it as ``PRIVA_DATASPINE__SERVICE_TOKEN``.
A tenant can read that token out of their own pod env — and gain nothing,
because it names their own account and data-spine's ACL grants it only the
narrow, own-tenant method set the runner legitimately uses.

That asymmetry is the whole point: capability-scoped credential, never a
signing key.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from .config import get_settings
from .service_identity import has_private_key, sign, verify

TOKEN_TYPE = "service"

# Signing control-plane workloads. Membership is asserted by the signature (only
# control-plane pods can sign), while data-spine still applies a separate,
# default-deny method allowlist for each workload.
CONTROL_PLANE_ROLES = frozenset({
    "control-panel",
    "operator",
    "scheduler",
    "channel-connector",
})
# Workloads that run untrusted tenant code. Always account-scoped.
TENANT_ROLES = frozenset({"agent-runner"})

_cached: tuple[str, float] | None = None
_lock = threading.Lock()


@dataclass(frozen=True)
class ServicePrincipal:
    svc: str
    account_id: str | None = None

    @property
    def is_control_plane(self) -> bool:
        return self.svc in CONTROL_PLANE_ROLES

    def __str__(self) -> str:  # log-friendly
        return f"{self.svc}[{self.account_id}]" if self.account_id else self.svc


def mint(svc: str, account_id: str | None = None, ttl_seconds: int | None = None) -> str:
    """Mint a service token. ``ttl_seconds=None`` => non-expiring (see sign())."""
    claims: dict[str, object] = {"svc": svc}
    if account_id:
        claims["account_id"] = account_id
    return sign(claims, typ=TOKEN_TYPE, ttl_seconds=ttl_seconds)


def verify_service(token: str) -> ServicePrincipal:
    """Verify a service token. Raises ``ValueError`` on any failure."""
    claims = verify(token, typ=TOKEN_TYPE)
    svc = claims.get("svc")
    if not svc or not isinstance(svc, str):
        raise ValueError("service token missing svc")
    if svc not in CONTROL_PLANE_ROLES and svc not in TENANT_ROLES:
        raise ValueError(f"unknown service role: {svc!r}")
    account_id = claims.get("account_id") or None
    # A tenant role without a scope would be indistinguishable from control
    # plane at the ACL layer — refuse rather than fail open.
    if svc in TENANT_ROLES and not account_id:
        raise ValueError(f"tenant role {svc!r} requires an account_id scope")
    return ServicePrincipal(svc=svc, account_id=account_id)


def current_token() -> str:
    """This pod's own outbound identity.

    Runner path: the operator-injected, account-scoped token (no private key
    here, so nothing else is possible). Control-plane path: minted on demand
    from the private key and cached until shortly before expiry.
    """
    settings = get_settings()
    injected = (settings.dataspine.service_token or "").strip()
    if injected:
        return injected

    svc = (settings.service_identity.service_name or "").strip()
    if svc not in CONTROL_PLANE_ROLES:
        # Second gate behind assert_configured, for any process that reaches an
        # outbound call without the boot check. Never fall back to a role name:
        # data-spine's method allowlist is keyed on it, so a guess grants that
        # workload's surface.
        raise RuntimeError(
            f"PRIVA_SERVICE_IDENTITY__SERVICE_NAME is {svc or 'unset'!r}; cannot mint "
            f"an outbound service token. Set it to one of {sorted(CONTROL_PLANE_ROLES)}."
        )

    global _cached
    ttl = settings.service_identity.service_token_ttl_seconds
    now = time.time()
    with _lock:
        if _cached is not None and _cached[1] > now:
            return _cached[0]
        token = mint(svc, ttl_seconds=ttl)
        # Refresh at 2/3 of TTL so a long call never rides an expiring token.
        _cached = (token, now + ttl * 2 / 3)
        return token


HEADER = "X-Priva-Service-Token"


def auth_header() -> dict[str, str]:
    """Outbound HTTP identity, for internal control-plane endpoints (the
    scheduler's /internal/trigger)."""
    return {HEADER: current_token()}


def reset_cache() -> None:
    """Test seam — drop the memoised outbound token."""
    global _cached
    with _lock:
        _cached = None


def describe_self() -> str:
    settings = get_settings()
    if (settings.dataspine.service_token or "").strip():
        return "operator-injected service token"
    kind = "private key" if has_private_key() else "EPHEMERAL key"
    svc = (settings.service_identity.service_name or "").strip() or "UNSET service_name"
    return f"{svc} ({kind})"


__all__ = [
    "CONTROL_PLANE_ROLES",
    "HEADER",
    "TENANT_ROLES",
    "ServicePrincipal",
    "auth_header",
    "current_token",
    "describe_self",
    "mint",
    "reset_cache",
    "verify_service",
]
