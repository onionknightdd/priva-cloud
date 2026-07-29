"""Short-TTL signed token the control-plane mints and the agent-runner verifies.

Carries ``{account_id, username, exp}``. The control-panel edge mints one per
proxied request from the browser's already-validated platform session; the
scheduler and channel-connector mint one per dispatch. The agent-runner verifies
it and pins the call to its single account.

**Asymmetric by construction.** This used to be an HS256 token keyed on
``dataspine.api_key_hmac_secret or auth.jwt_secret`` — which meant the runner
needed the *platform signing secret* just to verify, and that secret was
injected into every tenant pod. Reading it out of ``/proc/self/environ`` (or
plain ``env`` from the agent's own Bash tool) yielded the ability to forge a
platform login JWT for ``sub: "admin"``, i.e. full admin takeover.

Now: control-plane signs with the private key, the runner verifies with the
public key, and the two fallbacks that collapsed three distinct secrets onto
``auth.jwt_secret`` are gone. See ``priva_common.service_identity``.
"""

from __future__ import annotations

from .config import get_settings
from .service_identity import sign, verify as _verify

TOKEN_TYPE = "runner"

# Back-compat alias: callers used to import DEFAULT_TTL_SECONDS from here.
DEFAULT_TTL_SECONDS = 60


def mint(account_id: str, username: str, ttl_seconds: int | None = None) -> str:
    """Mint a short-TTL runner token (control-plane side; needs the private key)."""
    ttl = ttl_seconds if ttl_seconds is not None else (
        get_settings().service_identity.runner_token_ttl_seconds
    )
    return sign(
        {"account_id": account_id, "username": username},
        typ=TOKEN_TYPE,
        ttl_seconds=ttl,
    )


def verify(token: str) -> dict:
    """Verify a runner token and return its claims (agent-runner side).

    Raises ``ValueError`` on any failure (expired, bad signature, wrong type,
    malformed). Needs only the public key.
    """
    claims = _verify(token, typ=TOKEN_TYPE)
    if "account_id" not in claims or "username" not in claims:
        raise ValueError("runner token missing account_id/username")
    return claims


__all__ = ["DEFAULT_TTL_SECONDS", "TOKEN_TYPE", "mint", "verify"]
