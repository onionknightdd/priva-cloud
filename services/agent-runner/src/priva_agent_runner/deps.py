"""Single-account auth seam for the agent-runner.

Every runner route trusts a signed ``X-Priva-Runner-Token`` minted by the
control-panel (dev edge) instead of a browser credential. ``require_account``
verifies it, asserts it matches the one account this process is pinned to
(``ACCOUNT_ID`` env, set at boot, §E), and resolves the ``UserRecord``.

The legacy FastAPI dependencies (``get_current_user`` / ``require_user`` /
``require_admin``) are aliased to ``require_account`` so the moved route
signatures keep working unchanged — alpha is single-user, so "the current
user", "a user", and "an admin" all resolve to the pinned account (role
enforced below where it mattered).
"""

from __future__ import annotations

import os

from fastapi import Header, HTTPException, WebSocket

from priva_common.models.auth import UserRecord
from priva_common.user_store import get_user_store
from priva_common.workspace import get_user_workspace  # re-exported for routers

from .signed_header import verify

RUNNER_TOKEN_HEADER = "X-Priva-Runner-Token"


def pinned_account_id() -> str | None:
    return os.environ.get("ACCOUNT_ID")


def _resolve(token: str | None) -> UserRecord:
    if not token:
        raise HTTPException(401, "Missing runner token")
    try:
        claims = verify(token)
    except ValueError as exc:
        raise HTTPException(401, f"Invalid runner token: {exc}") from exc

    expected = pinned_account_id()
    if expected and claims.get("account_id") != expected:
        raise HTTPException(403, "Runner token account mismatch")

    user = get_user_store().get_user(claims["username"])
    if user is None:
        raise HTTPException(403, "Unknown account")
    if expected and getattr(user, "account_id", None) not in (None, expected):
        raise HTTPException(403, "Runner token account mismatch")
    return user


async def require_account(
    x_priva_runner_token: str | None = Header(default=None),
) -> UserRecord:
    """HTTP dependency: verify the signed header and return the pinned account."""
    return _resolve(x_priva_runner_token)


def account_from_ws(websocket: WebSocket) -> UserRecord:
    """WebSocket auth: read the CP-injected header off the handshake."""
    token = websocket.headers.get(RUNNER_TOKEN_HEADER.lower())
    return _resolve(token)


WS_SUBPROTOCOL = "priva.ws.v1"


def negotiated_subprotocol(websocket: WebSocket) -> str | None:
    """The SPA offers ``priva.ws.v1`` + ``priva.token.<jwt>`` on the handshake
    (the JWT rides the subprotocol so the edge can auth a body-less upgrade). If
    the client offered our sentinel, echo it back in ``accept()`` — otherwise the
    browser fails the connection (a server must select one of the offered
    subprotocols). Returns the sentinel, or None when nothing was offered."""
    offered = websocket.headers.get("sec-websocket-protocol", "")
    parts = [p.strip() for p in offered.split(",")]
    return WS_SUBPROTOCOL if WS_SUBPROTOCOL in parts else None


# --- Back-compat aliases so moved route signatures keep working (single-account) ---
require_user = require_account
require_admin = require_account
get_current_user = require_account

__all__ = [
    "require_account",
    "require_user",
    "require_admin",
    "get_current_user",
    "account_from_ws",
    "negotiated_subprotocol",
    "WS_SUBPROTOCOL",
    "get_user_workspace",
    "pinned_account_id",
    "RUNNER_TOKEN_HEADER",
]
