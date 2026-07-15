"""Best-effort low-latency reconcile ping to the channel-connector.

After a Feishu config edit (admin disable / user cred change), POST the connector's
``/internal/reconcile/{account_id}`` so the change lands in <1s instead of waiting for
the connector's ≤poll_seconds loop. The poll-list-diff is the GUARANTEED convergence
path, so this is pure latency reduction.

Fired as a DETACHED background task so the config edit never waits on (nor fails from)
connector reachability — a connector outage or unset URL just falls back to the poll.
Auth reuses the runner-token seam: the connector verifies ``mint(account_id, username)``
and that the claim's account_id matches the path, so one tenant can't nudge another.
"""

from __future__ import annotations

import asyncio
import os

import httpx

from priva_common.logging import get_app_logger
from priva_common.runner_token import mint

logger = get_app_logger(__name__)

# Keep strong refs to in-flight nudges so they aren't GC'd mid-flight.
_pending: set[asyncio.Task] = set()


def _connector_url() -> str:
    return os.environ.get("CONNECTOR_URL", "http://channel-connector:8083").rstrip("/")


async def _post(account_id: str, username: str) -> None:
    try:
        url = f"{_connector_url()}/internal/reconcile/{account_id}"
        headers = {"X-Priva-Runner-Token": mint(account_id, username or "connector-nudge")}
        async with httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(3.0, connect=1.5)) as cx:
            resp = await cx.post(url, headers=headers)
        if resp.status_code >= 400:
            logger.debug("connector nudge {} -> {} (poll is the backstop)", account_id, resp.status_code)
    except Exception as exc:
        logger.debug("connector nudge failed for {} (poll is the backstop): {}", account_id, exc)


async def nudge_reconcile(account_id: str, username: str = "") -> None:
    """Fire-and-forget reconcile hint. Returns immediately; MUST never raise — a
    connector outage cannot fail the config edit (the connector poll is the backstop)."""
    try:
        task = asyncio.create_task(_post(account_id, username))
    except RuntimeError:
        return  # no running loop — nothing to nudge, poll covers it
    _pending.add(task)
    task.add_done_callback(_pending.discard)
