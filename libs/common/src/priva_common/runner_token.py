"""Short-TTL signed token the control-panel mints and the agent-runner verifies.

Dev seam (code-split §13 local mode A): an HS256 shared-secret JWT carrying
``{account_id, username, exp}``. The control-panel (dev edge) mints one per
proxied request from the browser's already-validated platform session; the
agent-runner verifies it and pins the call to its single account. Swappable to
JWKS/mTLS in prod behind the same ``mint``/``verify`` surface.

Secret resolution: ``settings.dataspine.api_key_hmac_secret`` if set, else
``settings.auth.jwt_secret`` (the same HS256 secret the platform JWT already
uses, so a dev needs no extra config).
"""

from __future__ import annotations

import time

from jose import JWTError, jwt

from .config import get_settings

_ALGO = "HS256"
DEFAULT_TTL_SECONDS = 60


def _secret() -> str:
    s = get_settings()
    return s.dataspine.api_key_hmac_secret or s.auth.jwt_secret


def mint(account_id: str, username: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """Mint a short-TTL runner token (control-panel side)."""
    payload = {
        "account_id": account_id,
        "username": username,
        "exp": int(time.time()) + ttl_seconds,
    }
    return jwt.encode(payload, _secret(), algorithm=_ALGO)


def verify(token: str) -> dict:
    """Verify a runner token and return its claims (agent-runner side).

    Raises ``ValueError`` on any failure (expired, bad signature, malformed).
    """
    try:
        claims = jwt.decode(token, _secret(), algorithms=[_ALGO])
    except JWTError as exc:
        raise ValueError(f"invalid runner token: {exc}") from exc
    if "account_id" not in claims or "username" not in claims:
        raise ValueError("runner token missing account_id/username")
    return claims
