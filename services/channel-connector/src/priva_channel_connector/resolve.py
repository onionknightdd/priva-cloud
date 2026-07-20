"""Resolve an interactive-card answer back into the account's running agent-runner.

When a run calls AskUserQuestion (or a gated tool), the SDK ``can_use_tool`` blocks on a
``PermissionCoordinator`` Future inside the pod; the coordinator is registered there by
``session_id`` and emitted a ``permission_request`` over the SSE the connector is already
streaming. The user's tap on the card is answered here: mint a runner token and POST the
decision to the pod's ``/permission/respond`` — the same seam ``dial.py`` uses to reach
the pod, minus the wake (the run is already live and blocked).

``updated_input`` for an AskUserQuestion allow is ``{questions, answer}`` where ``answer``
is the locked one-line-per-question format the pod's ``_askuser_answers_map`` parses:
``- {header|question} -> {values}`` (see cards.build_answer).
"""

from __future__ import annotations

import httpx

from priva_common.config import get_settings
from priva_common.logging import get_app_logger
from priva_common.runner_token import mint

logger = get_app_logger(__name__)


def _url(account_id: str) -> str:
    s = get_settings()
    ns = s.kubernetes.namespace_tenants
    port = s.kubernetes.runner_service_port
    return f"http://ar-{account_id}.{ns}.svc:{port}/api/sandbox/agent/permission/respond"


async def resolve_permission(
    *,
    account_id: str,
    username: str | None,
    session_id: str,
    request_id: str,
    decision: str,                      # "allow" | "deny"
    updated_input: dict | None = None,
    message: str = "",
) -> bool:
    """POST the decision to the pod's coordinator. Returns True on a 200. Best-effort:
    a 404 means the request already resolved/timed out (harmless race); transport errors
    are logged and swallowed so a card tap never raises into the WS thread."""
    body: dict = {"session_id": session_id, "request_id": request_id, "decision": decision}
    if message:
        body["message"] = message
    if updated_input is not None:
        body["updated_input"] = updated_input
    headers = {"X-Priva-Runner-Token": mint(account_id, username or "")}
    try:
        async with httpx.AsyncClient(
            trust_env=False, timeout=httpx.Timeout(10.0, connect=10.0)
        ) as cx:
            resp = await cx.post(_url(account_id), json=body, headers=headers)
        if resp.status_code != 200:
            detail = resp.text[:200]
            logger.warning("permission resolve {} rid={} -> {}: {}",
                           account_id, request_id, resp.status_code, detail)
            return False
        logger.info("permission resolved account={} rid={} decision={}",
                    account_id, request_id, decision)
        return True
    except (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError) as exc:
        logger.warning("permission resolve transport error account={} rid={}: {}",
                       account_id, request_id, exc)
        return False
