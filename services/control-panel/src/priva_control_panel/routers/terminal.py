from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from priva_common.dataplane import get_client
from priva_common.user_store import UserRecord

from .. import provisioner
from ..services.auth import require_active_account

router = APIRouter(prefix="/api/terminal", tags=["terminal-control"])

_DEFAULTS_CACHE_SECONDS = 15.0
_STATUS_CACHE_SECONDS = 10.0
_defaults_cache: tuple[float, object] | None = None
_status_cache: dict[str, tuple[float, dict]] = {}
_CONNECTABLE_PHASES = frozenset({"Zero", "Waking", "Running"})


class TerminalCapability(BaseModel):
    enabled: bool = False
    resource_percent: int = 0
    max_sessions: int = 2
    idle_timeout_seconds: int = 1800
    max_lifetime_seconds: int = 14400
    phase: str = "Disabled"
    active_sessions: int = 0


def clear_terminal_capability_cache() -> None:
    """Invalidate local discovery caches after an in-process admin policy edit."""
    global _defaults_cache
    _defaults_cache = None
    _status_cache.clear()


async def _runner_defaults_cached():
    global _defaults_cache
    now = time.monotonic()
    if _defaults_cache is not None and now - _defaults_cache[0] < _DEFAULTS_CACHE_SECONDS:
        return _defaults_cache[1]
    defaults = await asyncio.to_thread(get_client().runner_defaults.get)
    _defaults_cache = (time.monotonic(), defaults)
    return defaults


async def _terminal_status_cached(account_id: str) -> dict:
    now = time.monotonic()
    cached = _status_cache.get(account_id)
    if cached is not None and now - cached[0] < _STATUS_CACHE_SECONDS:
        return cached[1]
    status = await asyncio.to_thread(provisioner._status, account_id)
    _status_cache[account_id] = (time.monotonic(), status)
    return status


@router.get("/capability", response_model=TerminalCapability)
async def terminal_capability(user: UserRecord = Depends(require_active_account)):
    """Wake-free feature discovery for the Agent UI.

    This exact path is routed to Control Panel, while /api/terminal/* WebSocket
    traffic goes through the Terminal InferencePool.
    """
    defaults = await _runner_defaults_cached()
    desired_percent = int(defaults.terminal_resource_percent)
    if desired_percent <= 0:
        # The common disabled case needs no Kubernetes read. This endpoint is polled
        # by every signed-in Agent UI, so avoid a per-account CR lookup at 0%.
        return TerminalCapability(
            resource_percent=0,
            max_sessions=defaults.terminal_max_sessions,
            idle_timeout_seconds=defaults.terminal_idle_timeout_seconds,
            max_lifetime_seconds=defaults.terminal_max_lifetime_seconds,
        )

    status = await _terminal_status_cached(user.account_id)
    terminal = status.get("terminal") or {}
    phase = terminal.get("phase") or "Pending"
    effective_percent = int(terminal.get("resourcePercent") or 0)
    # Zero is connectable: the Terminal InferencePool/EPP will wake that pod at the
    # WebSocket upgrade. PendingRunnerRestart is deliberately excluded, otherwise
    # the UI offers a terminal that the EPP can only answer with 503.
    if phase == "Running":
        enabled = effective_percent > 0
    else:
        enabled = phase in _CONNECTABLE_PHASES and effective_percent == desired_percent
    return TerminalCapability(
        enabled=enabled,
        resource_percent=desired_percent,
        max_sessions=defaults.terminal_max_sessions,
        idle_timeout_seconds=defaults.terminal_idle_timeout_seconds,
        max_lifetime_seconds=defaults.terminal_max_lifetime_seconds,
        phase=phase,
        active_sessions=int(terminal.get("activeSessions") or 0),
    )
