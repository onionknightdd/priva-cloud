"""Hooks API — admin-policy catalog, user config, testing, and logs.

The catalog is the read-only, user-visible face of the admin hook policies
(data-spine snapshot; script bodies are never exposed). Admin hooks are
enforced-only and delivered NATIVELY via the managed-policy ConfigMap (D6) —
there is no per-user enable/disable and no programmatic fallback. User-configured
hooks live natively in the CLI-loaded ``settings.json`` at the user + project
scope (D5); the CLI runs every loaded scope, so there is no shadowing.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from priva_common.audit_log import AuditEntry, get_audit_logger
from priva_common.models.hooks import (
    HookCatalogEntry,
    HookConfig,
    HookLogsResponse,
    HookTestRequest,
    HookTestResponse,
)
from priva_common.user_store import UserRecord

from ..deps import get_user_workspace, require_user
from ..services.hooks.config_manager import VALID_SCOPES, HookConfigManager
from ..services.hooks.executor import test_hook
from ..services.hooks.log_store import get_hook_log_store
from ..services.hooks.policy import get_policy_snapshot

router = APIRouter(prefix="/api/sandbox/hooks", tags=["hooks"])


def _enforced_policies():
    """Admin policies active for every user — enforced-only since D6/D7."""
    return [p for p in get_policy_snapshot() if p.enforced]


# -- Helpers ----------------------------------------------------------------


def _get_config_manager(user: UserRecord) -> HookConfigManager:
    return HookConfigManager(user.username)


def _catalog_for(username: str) -> list[HookCatalogEntry]:
    """The admin catalog as shown to users. Enforced-only (D6/D7): every listed
    policy is enforced, so ``enabled`` mirrors ``enforced`` — there is no per-user
    toggle."""
    return [
        HookCatalogEntry(
            id=p.id,
            name=p.name,
            description=p.description,
            hook_type=p.hook_type,
            # Show the events the hook actually fires on (per-event enforcement).
            events=[e for e in p.events
                    if e in set(getattr(p, "enforced_events", None) or p.events)],
            matcher=p.matcher,
            enforced=p.enforced,
            default_on=p.default_on,
            enabled=p.enforced,
            predefined=p.predefined,
        )
        for p in get_policy_snapshot()
    ]


# -- Admin hook-policy catalog (user view, read-only) -------------------------


@router.get("/catalog", response_model=list[HookCatalogEntry])
async def list_catalog(user: UserRecord = Depends(require_user)):
    """Admin hook policies visible to this user (read-only).

    Enforced-only and natively delivered — no enable/disable. No script bodies
    here; the description is the user-facing contract."""
    return _catalog_for(user.username)


# -- User hook config -------------------------------------------------------


@router.get("/config")
async def get_config(user: UserRecord = Depends(require_user)):
    """User-configured hooks grouped by settings.json scope, plus the admin
    hooks active for this user.

    ``scopes`` holds one group per scope that carries hooks: ``user``
    ($CLAUDE_CONFIG_DIR/settings.json, always present) then each project workdir
    ({cwd}/.claude/settings.json). Every scope is loaded natively by the CLI
    (setting_sources=["project","user"]) and runs alongside the admin hooks —
    there is NO shadowing (the CLI merges all loaded scopes and runs every
    matching hook). ``admin`` lists the admin hooks active per event (virtual
    entries; managed via the admin policy channel, not editable here)."""
    mgr = _get_config_manager(user)
    mgr.purge_all_legacy()
    scopes = [
        {"scope": scope, "cwd": cwd, "hooks": hooks}
        for scope, cwd, hooks in mgr.read_all()
    ]

    active = _enforced_policies()
    admin: dict[str, list[dict]] = {}
    for p in active:
        # Per-event enforcement: list the hook only under events it fires on.
        for event in [e for e in p.events
                      if e in set(getattr(p, "enforced_events", None) or p.events)]:
            admin.setdefault(event, []).append({
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "hook_type": p.hook_type,
                "matcher": p.matcher,
                "enforced": p.enforced,
            })

    return {"scopes": scopes, "admin": admin}


@router.put("/config")
async def update_config(
    config: HookConfig,
    scope: str = Query("project"),
    cwd: str | None = Query(None),
    user: UserRecord = Depends(require_user),
):
    """Replace this user's hooks in ONE settings.json scope (``user`` | ``project``).

    Writes natively into the CLI-loaded settings.json (surgical: only the
    ``hooks`` key is touched, so the user-scope ``env`` cred block is preserved)
    so the CLI runs the hooks directly. Only the target scope is modified."""
    if scope not in VALID_SCOPES:
        raise HTTPException(422, f"Invalid scope: {scope}")
    if scope == "project" and cwd is not None and not os.path.isabs(cwd):
        raise HTTPException(400, "An absolute 'cwd' is required for project-scope hooks")

    mgr = _get_config_manager(user)
    mgr.write_scope_hooks(scope, cwd, {
        event: [entry.model_dump(exclude_none=True) for entry in entries]
        for event, entries in config.hooks.items()
    })

    audit = get_audit_logger()
    audit.append(AuditEntry(
        actor=user.username,
        action="hooks.config_updated",
        details={"scope": scope, "cwd": cwd, "events": list(config.hooks.keys())},
    ))

    return {"status": "ok", "scope": scope, "cwd": cwd, "hooks": config.hooks}


# -- Testing ----------------------------------------------------------------


@router.post("/test", response_model=HookTestResponse)
async def test_hook_endpoint(
    request: HookTestRequest,
    user: UserRecord = Depends(require_user),
):
    """Dry-run a user custom command hook with sample JSON input."""
    cwd = get_user_workspace(user)
    result = await test_hook(
        event_type=request.event_type,
        handler=request.handler,
        input_json=request.input_json,
        cwd=cwd,
    )
    return result


# -- Script content ---------------------------------------------------------


@router.get("/script/content")
async def get_script_content(
    path: str = Query(..., description="Script path relative to user work dir"),
    user: UserRecord = Depends(require_user),
):
    """Read the content of a hook script file within the user's work directory.

    The path must resolve to a file inside the user's workspace. Path traversal
    (e.g. ``../``) is rejected for safety.
    """
    cwd = get_user_workspace(user)
    cwd_resolved = Path(cwd).resolve()

    # Resolve the requested path relative to the user's work dir
    target = (cwd_resolved / path).resolve()

    # Safety: ensure the resolved path is within the user's work dir
    if not str(target).startswith(str(cwd_resolved) + os.sep) and target != cwd_resolved:
        raise HTTPException(403, "Access denied: path is outside the work directory")

    if not target.is_file():
        raise HTTPException(404, f"File not found: {path}")

    # Limit file size to prevent reading huge files
    file_size = target.stat().st_size
    if file_size > 512 * 1024:  # 512 KB
        raise HTTPException(413, "File too large to read (max 512 KB)")

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        raise HTTPException(500, f"Failed to read file: {exc}")

    # Detect language from extension
    suffix = target.suffix.lower()
    lang_map = {
        ".py": "python", ".sh": "bash", ".bash": "bash",
        ".js": "javascript", ".ts": "typescript",
        ".rb": "ruby", ".go": "go", ".rs": "rust",
    }
    language = lang_map.get(suffix, "text")

    return {
        "path": str(target.relative_to(cwd_resolved)),
        "content": content,
        "language": language,
        "size": file_size,
    }


# -- Execution logs ---------------------------------------------------------


@router.get("/logs", response_model=HookLogsResponse)
async def get_logs(
    event_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    before: str | None = Query(default=None),
    after: str | None = Query(default=None),
    _user: UserRecord = Depends(require_user),
):
    """Hook execution history for this user."""
    store = get_hook_log_store()
    entries, next_cursor, prev_cursor, total = await asyncio.to_thread(
        store.query_cursor,
        event_type=event_type,
        limit=limit,
        before=before,
        after=after,
    )
    return HookLogsResponse(
        entries=entries,
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
        total=total,
        limit=limit,
    )
