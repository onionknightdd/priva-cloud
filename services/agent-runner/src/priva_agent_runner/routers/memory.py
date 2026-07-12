"""Memory (CLAUDE.md) API — list scopes, read/write per scope.

Content can run large, so reads go over the ``sandboxRead`` lane on the client;
the payloads here stay simple JSON. See ``services.memory`` for the path model.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from priva_common.audit_log import AuditEntry, get_audit_logger
from priva_common.models.auth import UserRecord
from priva_common.models.memory import (
    AutoMemoryContent,
    AutoMemoryEnabledRequest,
    AutoMemoryListResponse,
    MemoryContent,
    MemoryListResponse,
    MemoryUpdateRequest,
)

from ..deps import require_user
from ..services.memory import (
    delete_auto_memory,
    list_auto_memory,
    list_memory,
    read_auto_memory,
    read_memory,
    set_auto_memory_enabled,
    write_auto_memory,
    write_memory,
)

router = APIRouter(prefix="/api/sandbox/memory", tags=["memory"])


@router.get("/list", response_model=MemoryListResponse)
async def list_memory_scopes(user: UserRecord = Depends(require_user)):
    """CLAUDE.md scopes for this user: User + each project workdir (existence flagged)."""
    return list_memory(user.username)


@router.get("/content", response_model=MemoryContent)
async def get_memory_content(
    scope: str = Query("user"),
    cwd: str | None = Query(None),
    user: UserRecord = Depends(require_user),
):
    """Read one scope's CLAUDE.md (empty content when the file does not exist yet)."""
    return read_memory(user.username, scope, cwd)


@router.put("/content", response_model=MemoryContent)
async def put_memory_content(
    request: MemoryUpdateRequest,
    scope: str = Query("user"),
    cwd: str | None = Query(None),
    user: UserRecord = Depends(require_user),
):
    """Write one scope's CLAUDE.md (atomic replace). The CLI picks it up next run."""
    result = write_memory(user.username, scope, cwd, request.content)
    get_audit_logger().append(AuditEntry(
        actor=user.username,
        action="memory.update",
        target=result.path,
        details={"scope": scope, "cwd": cwd, "bytes": len(request.content)},
    ))
    return result


# --- Auto memory (Claude-written) — browse / edit / delete / toggle ---------


@router.get("/auto/list", response_model=AutoMemoryListResponse)
async def list_auto_memory_projects(user: UserRecord = Depends(require_user)):
    """Auto-memory per project workdir: the memory dir, its files, and the toggle."""
    return list_auto_memory(user.username)


@router.get("/auto/content", response_model=AutoMemoryContent)
async def get_auto_memory_file(
    cwd: str = Query(...),
    name: str = Query(...),
    user: UserRecord = Depends(require_user),
):
    """Read one auto-memory file (the ``MEMORY.md`` index or a topic file)."""
    return read_auto_memory(user.username, cwd, name)


@router.put("/auto/content", response_model=AutoMemoryContent)
async def put_auto_memory_file(
    request: MemoryUpdateRequest,
    cwd: str = Query(...),
    name: str = Query(...),
    user: UserRecord = Depends(require_user),
):
    """Edit an existing auto-memory file (create unsupported). CLI picks it up next run."""
    result = write_auto_memory(user.username, cwd, name, request.content)
    get_audit_logger().append(AuditEntry(
        actor=user.username,
        action="memory.auto.update",
        target=result.path,
        details={"cwd": cwd, "name": name, "bytes": len(request.content)},
    ))
    return result


@router.delete("/auto/content")
async def delete_auto_memory_file(
    cwd: str = Query(...),
    name: str = Query(...),
    user: UserRecord = Depends(require_user),
):
    """Delete one auto-memory file (curation)."""
    path = delete_auto_memory(user.username, cwd, name)
    get_audit_logger().append(AuditEntry(
        actor=user.username,
        action="memory.auto.delete",
        target=path,
        details={"cwd": cwd, "name": name},
    ))
    return {"ok": True, "path": path}


@router.put("/auto/enabled")
async def put_auto_memory_enabled(
    request: AutoMemoryEnabledRequest,
    cwd: str = Query(...),
    user: UserRecord = Depends(require_user),
):
    """Per-project auto-memory on/off (writes ``autoMemoryEnabled`` to
    ``{cwd}/.claude/settings.json``)."""
    enabled = set_auto_memory_enabled(user.username, cwd, request.enabled)
    get_audit_logger().append(AuditEntry(
        actor=user.username,
        action="memory.auto.toggle",
        target=cwd,
        details={"cwd": cwd, "enabled": enabled},
    ))
    return {"cwd": cwd, "enabled": enabled}
