"""Short-lived capability for one WebSocket handshake into one Terminal pod.

The Control Panel EPP mints this only after it has authenticated the platform
session, resolved the target account and selected the concrete ready pod.  The
Terminal receives only the public verification key, never signing material.
"""

from __future__ import annotations

from .service_identity import sign, verify as _verify

TOKEN_TYPE = "terminal"
AUDIENCE = "priva-terminal"
DEFAULT_TTL_SECONDS = 30


def mint(account_id: str, pod: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """Mint a capability bound to one account and one concrete pod address."""
    account_id = str(account_id or "").strip()
    pod = str(pod or "").strip()
    if not account_id:
        raise ValueError("terminal token requires account_id")
    if not pod:
        raise ValueError("terminal token requires pod")
    if int(ttl_seconds) <= 0:
        raise ValueError("terminal token TTL must be positive")
    return sign(
        {
            "account_id": account_id,
            "pod": pod,
            "aud": AUDIENCE,
        },
        typ=TOKEN_TYPE,
        ttl_seconds=int(ttl_seconds),
    )


def verify(token: str) -> dict:
    """Verify signature, type, audience, expiry and required binding claims."""
    claims = _verify(token, typ=TOKEN_TYPE, audience=AUDIENCE)
    if not isinstance(claims.get("account_id"), str) or not claims["account_id"]:
        raise ValueError("terminal token missing account_id")
    if not isinstance(claims.get("pod"), str) or not claims["pod"]:
        raise ValueError("terminal token missing pod")
    if "exp" not in claims:
        raise ValueError("terminal token missing exp")
    return claims


__all__ = [
    "AUDIENCE",
    "DEFAULT_TTL_SECONDS",
    "TOKEN_TYPE",
    "mint",
    "verify",
]
