"""Per-user config faces (quickactions, recap) served from the agent's
own workspace.

These read/write the pod's ``.priva.user.yml`` via the shared
``priva_common.skill_exclude`` accessors, so the values land in
``priva_home()/.priva.user.yml`` (default ``~/.config/priva/.priva.user.yml``),
outside the project workspace.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from priva_common import skill_exclude as _user_yaml
from priva_common.models.auth import UserRecord
from priva_common.models.resource import (
    QuickAction,
    QuickActionListResponse,
    QuickActionUpdateRequest,
    RecapSettingResponse,
    RecapSettingUpdateRequest,
)
from ..deps import require_user
from ..services.claude_sdk import session_recap

router = APIRouter(prefix="/api/sandbox/resource", tags=["user-config"])


# ── Quick actions ────────────────────────────────────────────────────


@router.get("/quickactions", response_model=QuickActionListResponse)
async def list_quickactions(user: UserRecord = Depends(require_user)):
    raw = _user_yaml.get_user_yaml_key("quickactions", [])
    if not isinstance(raw, list):
        raw = []
    actions = [
        QuickAction(name=item["name"], prompt=item["prompt"], icon=item.get("icon"))
        for item in raw
        if isinstance(item, dict) and "name" in item and "prompt" in item
    ]
    return QuickActionListResponse(quickactions=actions)


@router.put("/quickactions", response_model=QuickActionListResponse)
async def update_quickactions(
    request: QuickActionUpdateRequest,
    user: UserRecord = Depends(require_user),
):
    qa_dicts = [qa.model_dump() for qa in request.quickactions]
    _user_yaml.save_user_yaml_key("quickactions", qa_dicts)
    return QuickActionListResponse(quickactions=request.quickactions)


# ── Session recap ────────────────────────────────────────────────────


@router.get("/recap-setting", response_model=RecapSettingResponse)
async def get_recap_setting(user: UserRecord = Depends(require_user)):
    return RecapSettingResponse(recap_enabled=session_recap.is_enabled())


@router.put("/recap-setting", response_model=RecapSettingResponse)
async def update_recap_setting(
    request: RecapSettingUpdateRequest,
    user: UserRecord = Depends(require_user),
):
    # Stored explicitly either way: an absent key means "on", so turning the
    # feature off has to write ``False`` rather than pop the key.
    _user_yaml.save_user_yaml_key("recap_enabled", bool(request.recap_enabled))
    return RecapSettingResponse(recap_enabled=request.recap_enabled)
