"""Per-user config faces (quickactions, vision-model) served from the agent's
own workspace.

These read/write the user's ``.priva.user.yml`` via the shared
``priva_common.skill_exclude`` accessors, so the values land in
``$work_dir/<username>/.priva.user.yml`` — on the agent-runner that is the
per-account PVC (/workspace/<username>), the SAME file the agent reads
(``vision_model`` at claude_sdk/service.py). Previously the control-panel served
these from its own pod-local /tmp/cp-workspace, which the agent never sees, so
the saved vision_model was silently dropped and quickactions died with the pod.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from priva_common import skill_exclude as _user_yaml
from priva_common.models.auth import UserRecord
from priva_common.models.resource import (
    QuickAction,
    QuickActionListResponse,
    QuickActionUpdateRequest,
    VisionModelResponse,
    VisionModelUpdateRequest,
)
from ..deps import require_user

router = APIRouter(prefix="/api/resource", tags=["user-config"])


# ── Quick actions ────────────────────────────────────────────────────


@router.get("/quickactions", response_model=QuickActionListResponse)
async def list_quickactions(user: UserRecord = Depends(require_user)):
    raw = _user_yaml.get_user_yaml_key(user.username, "quickactions", [])
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
    _user_yaml.save_user_yaml_key(user.username, "quickactions", qa_dicts)
    return QuickActionListResponse(quickactions=request.quickactions)


# ── Vision model config ──────────────────────────────────────────────


@router.get("/vision-model", response_model=VisionModelResponse)
async def get_vision_model(user: UserRecord = Depends(require_user)):
    vm = _user_yaml.get_user_yaml_key(user.username, "vision_model")
    return VisionModelResponse(vision_model=vm if isinstance(vm, str) and vm else None)


@router.put("/vision-model", response_model=VisionModelResponse)
async def update_vision_model(
    request: VisionModelUpdateRequest,
    user: UserRecord = Depends(require_user),
):
    # save_user_yaml_key pops the key when value is falsy/None.
    _user_yaml.save_user_yaml_key(
        user.username, "vision_model", request.vision_model or None
    )
    return VisionModelResponse(vision_model=request.vision_model)
