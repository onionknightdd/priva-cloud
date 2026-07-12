"""Slash-command (custom command) API — list + CRUD across User/Project scopes.

Mirrors the subagents router: ``scope`` + ``cwd`` query params select the
target ``.claude/commands`` directory; the body is the command's prompt template.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from priva_common.audit_log import AuditEntry, get_audit_logger
from priva_common.models.auth import UserRecord
from priva_common.models.commands import (
    CommandCreateRequest,
    CommandDetail,
    CommandListResponse,
    CommandUpdateRequest,
)

from ..deps import require_user
from ..services.commands import (
    create_command,
    delete_command,
    get_command,
    list_commands,
    update_command,
)

router = APIRouter(prefix="/api/sandbox/commands", tags=["commands"])


@router.get("/list", response_model=CommandListResponse)
async def list_all_commands(user: UserRecord = Depends(require_user)):
    return list_commands(user.username)


@router.get("/{name}", response_model=CommandDetail)
async def get_one_command(
    name: str,
    scope: str = Query("project"),
    cwd: str | None = Query(None),
    user: UserRecord = Depends(require_user),
):
    return get_command(user.username, scope, cwd, name)


@router.post("/", response_model=CommandDetail)
async def create_one_command(
    request: CommandCreateRequest,
    user: UserRecord = Depends(require_user),
):
    detail = create_command(user.username, request)
    get_audit_logger().append(AuditEntry(
        actor=user.username,
        action="commands.create",
        target=detail.name,
        details={"scope": detail.scope, "cwd": detail.cwd},
    ))
    return detail


@router.put("/{name}", response_model=CommandDetail)
async def update_one_command(
    name: str,
    request: CommandUpdateRequest,
    scope: str = Query("project"),
    cwd: str | None = Query(None),
    user: UserRecord = Depends(require_user),
):
    detail = update_command(user.username, scope, cwd, name, request)
    audit = get_audit_logger()
    if request.new_name and request.new_name != name:
        audit.append(AuditEntry(
            actor=user.username,
            action="commands.rename",
            target=detail.name,
            details={"old": name, "new": detail.name},
        ))
    audit.append(AuditEntry(
        actor=user.username,
        action="commands.update",
        target=detail.name,
        details={"scope": detail.scope, "cwd": detail.cwd},
    ))
    return detail


@router.delete("/{name}")
async def delete_one_command(
    name: str,
    scope: str = Query("project"),
    cwd: str | None = Query(None),
    user: UserRecord = Depends(require_user),
):
    delete_command(user.username, scope, cwd, name)
    get_audit_logger().append(AuditEntry(
        actor=user.username,
        action="commands.delete",
        target=name,
        details={"scope": scope, "cwd": cwd},
    ))
    return {"message": f"Command '{name}' deleted successfully"}
