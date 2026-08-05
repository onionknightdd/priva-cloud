"""Best-effort delivery of scheduled-run callbacks.

The agent-runner owns the terminal outcome, but it deliberately does not own
Feishu credentials or message formatting.  It posts a typed result to the
always-on channel-connector, authenticated with the runner pod's injected,
account-scoped service token.  Delivery failure is observable on the run's
event record and never changes the task outcome.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from priva_common.logging import get_app_logger
from priva_common.scheduler_callback_token import HEADER as CALLBACK_TOKEN_HEADER
from priva_common.service_token import auth_header

logger = get_app_logger(__name__)

_CALLBACK_TIMEOUT = httpx.Timeout(10.0, connect=3.0)
_ERROR_MESSAGE_CHARS = 2000


def _connector_url() -> str:
    # Keep this seam aligned with the control-panel's reconcile nudge.
    return os.environ.get("CONNECTOR_URL", "http://channel-connector:8083").rstrip("/")


def is_feishu_enabled(job_config: Any) -> bool:
    """Tolerate both the pydantic callback model and dict-shaped test inputs."""
    callback = getattr(job_config, "callback", None)
    if isinstance(callback, dict):
        return callback.get("type") == "feishu"
    return getattr(callback, "type", None) == "feishu"


async def deliver_feishu(
    *,
    account_id: str | None,
    payload: dict[str, Any],
    record: Any,
    callback_token: str | None,
) -> None:
    """POST one callback and turn every delivery failure into a run event."""
    if not account_id:
        _record_failure(record, "missing account_id")
        return
    if not callback_token:
        _record_failure(record, "missing scheduler callback capability")
        return

    url = f"{_connector_url()}/internal/scheduler-callback/{account_id}"
    try:
        headers = {**auth_header(), CALLBACK_TOKEN_HEADER: callback_token}
        async with httpx.AsyncClient(trust_env=False, timeout=_CALLBACK_TIMEOUT) as client:
            response = await client.post(url, json=payload, headers=headers)
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"channel-connector returned HTTP {response.status_code}")
    except Exception as exc:  # noqa: BLE001 — callback is explicitly best-effort
        message = (str(exc) or type(exc).__name__)[:_ERROR_MESSAGE_CHARS]
        _record_failure(record, message)
        logger.warning(
            "[SCHED] Feishu callback failed run_id={} account_id={}: {}",
            payload.get("run_id"), account_id, message,
        )


def _record_failure(record: Any, message: str) -> None:
    try:
        record.record_event(
            "callback_failed", {"channel": "feishu", "message": message},
        )
    except Exception:  # noqa: BLE001 — observability must not break the task outcome
        logger.warning("[SCHED] could not record callback_failed event", exc_info=True)
