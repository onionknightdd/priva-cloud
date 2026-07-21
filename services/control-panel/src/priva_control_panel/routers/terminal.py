from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from priva_common.dataplane import get_client
from priva_common.user_store import UserRecord

from .. import provisioner
from ..services.auth import require_user

router = APIRouter(prefix="/api/terminal", tags=["terminal-control"])


class TerminalCapability(BaseModel):
    enabled: bool = False
    resource_percent: int = 0
    max_sessions: int = 2
    idle_timeout_seconds: int = 1800
    max_lifetime_seconds: int = 14400
    phase: str = "Disabled"
    active_sessions: int = 0


@router.get("/capability", response_model=TerminalCapability)
async def terminal_capability(user: UserRecord = Depends(require_user)):
    """Wake-free feature discovery for the Agent UI.

    This exact path is routed to Control Panel, while /api/terminal/* WebSocket
    traffic goes through the Terminal InferencePool.
    """
    defaults = await asyncio.to_thread(get_client().runner_defaults.get)
    status = await asyncio.to_thread(provisioner._status, user.account_id)
    terminal = status.get("terminal") or {}
    enabled = defaults.terminal_resource_percent > 0
    return TerminalCapability(
        enabled=enabled,
        resource_percent=defaults.terminal_resource_percent,
        max_sessions=defaults.terminal_max_sessions,
        idle_timeout_seconds=defaults.terminal_idle_timeout_seconds,
        max_lifetime_seconds=defaults.terminal_max_lifetime_seconds,
        phase=terminal.get("phase") or ("Zero" if enabled else "Disabled"),
        active_sessions=int(terminal.get("activeSessions") or 0),
    )
