from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Literal

from claude_agent_sdk import (
    ClaudeSDKClient,
    fork_session as sdk_fork_session,
    get_session_info,
    get_session_messages,
    list_sessions,
    rename_session as sdk_rename_session,
    tag_session as sdk_tag_session,
)
from claude_agent_sdk._internal.sessions import _canonicalize_path, _get_project_dir
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import TypeAdapter, ValidationError

from priva_common.logging import get_app_logger
from priva_common.models.agent import (
    AddDirsRequest,
    AgentRunRequest,
    AgentRunResponse,
    ArchiveRequest,
    ArchivedSessionListResponse,
    FlatSessionListResponse,
    ForkRequest,
    ForkResponse,
    GroupedSessionListResponse,
    ImageItem,
    PermissionRespondRequest,
    PinRequest,
    RenameRequest,
    RewindRequest,
    RewindResponse,
    SessionGroupResponse,
    SessionInfoResponse,
    SessionListResponse,
    SessionMessageResponse,
    SessionMessagesResponse,
    TagRequest,
    WorkdirArchiveRequest,
    WorkdirPinRequest,
    WsClientFrame,
    WsInitFrame,
    WsPermissionFrame,
    WsQueueCancelFrame,
    WsQueueFrame,
)
from ..services.claude_sdk.options import build_agent_options
from ..services.claude_sdk.session_add_dirs import (
    delete_add_dirs,
    read_add_dirs,
    write_add_dirs,
)
from ..services.claude_sdk import session_meta

_ws_frame_adapter = TypeAdapter(WsClientFrame)
from priva_common.audit_log import AuditEntry, get_audit_logger
from ..deps import account_from_ws, get_current_user, get_user_workspace, negotiated_subprotocol
from ..services.claude_sdk.client import agent_run, agent_run_events, agent_run_stream
from ..services.claude_sdk.permission_coordinator import registry
from priva_common.user_store import UserRecord
from priva_common.metrics import AGENT_RUNS_FINISHED, AGENT_RUNS_STARTED

import os


_ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_MAX_IMAGE_SIZE = 3 * 1024 * 1024  # 3MB decoded
_MAX_IMAGES = 5


def _is_within_directory(path: str, directory: str) -> bool:
    try:
        return os.path.commonpath([path, directory]) == directory
    except ValueError:
        return False


def _validate_dir(path: str) -> str:
    """Resolve a user-supplied directory to its realpath; 400 if missing/not a dir.

    cwd and add_dirs may point anywhere on the (single-account) pod FS — OS uid
    gating is the real boundary — so the only checks are existence + is-a-directory.
    """
    real = os.path.realpath(os.path.expanduser(path))
    if not os.path.isdir(real):
        raise HTTPException(400, f"Directory not found: {path}")
    return real


def _validate_dirs(paths: list[str] | None) -> list[str]:
    return [_validate_dir(p) for p in (paths or [])]


def _find_session_cwd(session_id: str) -> str | None:
    """The cwd a session lives under (scans all project dirs). None if unknown."""
    try:
        info = get_session_info(session_id)
    except Exception:
        logger.warning("get_session_info failed for %s", session_id, exc_info=True)
        return None
    return getattr(info, "cwd", None) if info else None


def _resolve_run_cwd(request_cwd: str | None, session_id: str | None, user) -> str:
    """Effective cwd for a run.

    Resume (session_id present): the session's recorded cwd — request.cwd is
    ignored because cwd is locked once a conversation exists. New session:
    request.cwd (validated) if given, else the user's default workspace.
    """
    if session_id:
        found = _find_session_cwd(session_id)
        if found:
            return found
    if request_cwd:
        return _validate_dir(request_cwd)
    return get_user_workspace(user)


def _resolve_run_add_dirs(
    request_add_dirs: list[str] | None, cwd: str, session_id: str | None
) -> list[str]:
    """Effective add_dirs: the request's validated set if provided, else the
    session's stored sidecar set (recover on resume)."""
    if request_add_dirs is not None:
        return _validate_dirs(request_add_dirs)
    return read_add_dirs(cwd, session_id)


def _session_info_to_response(s, meta: dict | None = None) -> SessionInfoResponse:
    flags = session_meta.get_session_flags(s.session_id, meta)
    return SessionInfoResponse(
        session_id=s.session_id,
        summary=s.summary,
        last_modified=s.last_modified,
        file_size=s.file_size,
        custom_title=s.custom_title,
        first_prompt=s.first_prompt,
        git_branch=s.git_branch,
        cwd=s.cwd,
        session_source="project",
        tag=getattr(s, "tag", None),
        pinned=flags["pinned"],
        archived=flags["archived"],
    )


def _validate_images(images: list[ImageItem] | None) -> list[dict] | None:
    if not images:
        return None
    if len(images) > _MAX_IMAGES:
        raise HTTPException(400, f"Maximum {_MAX_IMAGES} images per message")
    validated = []
    for img in images:
        if img.media_type not in _ALLOWED_IMAGE_TYPES:
            raise HTTPException(400, f"Unsupported image type: {img.media_type}")
        # base64 is ~4/3 of original, estimate decoded size
        decoded_size = len(img.data) * 3 // 4
        if decoded_size > _MAX_IMAGE_SIZE:
            raise HTTPException(413, f"Image exceeds {_MAX_IMAGE_SIZE // (1024*1024)}MB limit")
        validated.append({"data": img.data, "media_type": img.media_type})
    return validated


def _validate_attachments(attachments, cwd: str) -> list[dict] | None:
    """Validate attachment paths and return list of {path, name} dicts."""
    if not attachments:
        return None
    real_cwd = os.path.realpath(cwd)
    validated = []
    for att in attachments:
        path = att.path if hasattr(att, "path") else att
        name = getattr(att, "name", None)
        # Resolve to canonical path to prevent traversal
        real_path = os.path.realpath(path)
        if not _is_within_directory(real_path, real_cwd):
            raise HTTPException(400, f"Attachment path outside workspace: {path}")
        if not os.path.isfile(real_path):
            raise HTTPException(400, f"Attachment file not found: {path}")
        validated.append({"path": real_path, "name": name})
    return validated


def _session_jsonl_path(cwd: str, session_id: str) -> Path:
    canonical = _canonicalize_path(cwd)
    project_dir = _get_project_dir(canonical)
    return project_dir / f"{session_id}.jsonl"


def _iter_jsonl_dicts(path: Path):
    if not path.exists():
        return

    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Skipping invalid JSONL row %s:%s", path, line_number)
                continue
            if isinstance(item, dict):
                yield item


def _message_content_blocks(raw: dict) -> list[dict]:
    message = raw.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def _tool_use_result(raw: dict) -> dict | None:
    result = raw.get("toolUseResult") or raw.get("tool_use_result")
    return result if isinstance(result, dict) else None


def _build_subagent_parent_map(cwd: str, session_id: str) -> dict[str, str]:
    """Map sidechain agent ids back to the top-level Agent/Task tool_use id."""
    parent_by_agent_id: dict[str, str] = {}
    for raw in _iter_jsonl_dicts(_session_jsonl_path(cwd, session_id)):
        if raw.get("type") != "user":
            continue

        result = _tool_use_result(raw)
        if not result:
            continue

        agent_id = result.get("agentId") or result.get("agent_id")
        if not agent_id:
            continue

        for block in _message_content_blocks(raw):
            if block.get("type") == "tool_result" and block.get("tool_use_id"):
                parent_by_agent_id.setdefault(str(agent_id), str(block["tool_use_id"]))
                break

    return parent_by_agent_id


def _has_tool_result(raw: dict) -> bool:
    return any(block.get("type") == "tool_result" for block in _message_content_blocks(raw))


def _with_inline_tool_use_result(raw: dict):
    """Attach raw toolUseResult to tool_result blocks for replay consumers."""
    tool_result = _tool_use_result(raw)
    if not tool_result:
        return raw.get("message")

    message = raw.get("message")
    if not isinstance(message, dict):
        return message

    content = message.get("content")
    if not isinstance(content, list):
        return message

    next_content = []
    changed = False
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            next_content.append({**block, "tool_use_result": tool_result})
            changed = True
        else:
            next_content.append(block)

    return {**message, "content": next_content} if changed else message


def _load_subagent_session_messages(cwd: str, session_id: str) -> list[SessionMessageResponse]:
    """Hydrate historical subagent sidechain messages.

    Claude stores live subagent turns under
    `<project>/<session_id>/subagents/agent-<agentId>.jsonl`. The normal SDK
    history reader only returns the main session JSONL, so replay needs to load
    these sidechains and pin them to the parent Agent/Task tool_use id.
    """
    parent_by_agent_id = _build_subagent_parent_map(cwd, session_id)
    if not parent_by_agent_id:
        return []

    session_dir = _session_jsonl_path(cwd, session_id).with_suffix("")
    subagents_dir = session_dir / "subagents"
    if not subagents_dir.exists():
        return []

    hydrated: list[SessionMessageResponse] = []
    for path in sorted(subagents_dir.glob("agent-*.jsonl")):
        filename_agent_id = path.stem.removeprefix("agent-")
        parent_tool_use_id = parent_by_agent_id.get(filename_agent_id)

        for raw in _iter_jsonl_dicts(path):
            msg_type = raw.get("type")
            if msg_type not in {"user", "assistant"}:
                continue

            raw_agent_id = raw.get("agentId") or raw.get("agent_id")
            if not parent_tool_use_id and raw_agent_id:
                parent_tool_use_id = parent_by_agent_id.get(str(raw_agent_id))
            if not parent_tool_use_id:
                continue

            # Sidechain user prompt rows are internal scaffolding. Keep only
            # user rows that contain tool_result blocks so replay can attach
            # outputs to the corresponding subagent tool_use blocks.
            if msg_type == "user" and not _has_tool_result(raw):
                continue

            uuid = raw.get("uuid")
            message = _with_inline_tool_use_result(raw)
            if not uuid or message is None:
                continue

            hydrated.append(SessionMessageResponse(
                type=msg_type,
                uuid=str(uuid),
                session_id=str(raw.get("sessionId") or raw.get("session_id") or session_id),
                message=message,
                parent_tool_use_id=parent_tool_use_id,
                metadata={
                    "timestamp": raw["timestamp"],
                } if isinstance(raw.get("timestamp"), str) else None,
            ))

    return hydrated


def _build_message_replay_metadata(cwd: str, session_id: str) -> dict[str, dict]:
    """Collect per-message timestamps plus assistant usage/duration for replay.

    The SDK history reader returns user/assistant turns only. Claude's JSONL
    keeps token usage on assistant rows and writes the final elapsed time as a
    following system ``turn_duration`` row keyed by the assistant UUID. Stitch
    those back together so the Web UI can render the same action metadata after
    loading a past session.
    """
    metadata_by_uuid: dict[str, dict] = {}
    assistant_count_in_turn = 0

    for raw in _iter_jsonl_dicts(_session_jsonl_path(cwd, session_id)):
        if raw.get("isSidechain") is True:
            continue

        uuid = raw.get("uuid")
        if uuid and isinstance(raw.get("timestamp"), str):
            metadata_by_uuid.setdefault(str(uuid), {})["timestamp"] = raw["timestamp"]

        msg_type = raw.get("type")
        if msg_type == "user" and not _has_tool_result(raw):
            assistant_count_in_turn = 0
            continue

        if msg_type == "assistant":
            if not uuid:
                continue
            assistant_count_in_turn += 1
            message = raw.get("message")
            if isinstance(message, dict):
                usage = message.get("usage")
                if isinstance(usage, dict):
                    metadata_by_uuid.setdefault(str(uuid), {})["usage"] = usage
            continue

        if msg_type == "system" and raw.get("subtype") == "turn_duration":
            parent_uuid = raw.get("parentUuid")
            if not parent_uuid:
                continue
            meta = metadata_by_uuid.setdefault(str(parent_uuid), {})
            duration_ms = raw.get("durationMs")
            if isinstance(duration_ms, (int, float)):
                meta["duration_ms"] = int(duration_ms)
            usage = meta.get("usage")
            iterations = usage.get("iterations") if isinstance(usage, dict) else None
            if isinstance(iterations, list) and iterations:
                meta["agent_loops"] = len(iterations)
            elif assistant_count_in_turn > 0:
                meta["agent_loops"] = assistant_count_in_turn

    return metadata_by_uuid


logger = get_app_logger(__name__)

router = APIRouter(prefix="/api/sandbox/agent", tags=["agent"])


@router.post("/run", response_model=AgentRunResponse)
async def run_agent(
    http_request: Request,
    request: AgentRunRequest,
    user: UserRecord | None = Depends(get_current_user),
):
    cwd = _resolve_run_cwd(request.cwd, request.session_id, user)
    add_dirs = _resolve_run_add_dirs(request.add_dirs, cwd, request.session_id)
    username = user.username if user else None
    attachments = _validate_attachments(request.attachments, cwd)
    images = _validate_images(request.images)
    auth_method = getattr(http_request.state, "auth_method", "jwt")
    AGENT_RUNS_STARTED.inc()
    outcome = "success"
    try:
        result = await agent_run(
            request.message, request.session_id, request.permission_mode,
            cwd=cwd, add_dirs=add_dirs, username=username, model_override=request.model,
            auth_method=auth_method,
            attachments=attachments, images=images, mcp_servers=request.mcp_servers,
            enable_file_checkpointing=request.enable_file_checkpointing,
            fork_session=request.fork_session,
        )
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except Exception:
        outcome = "error"
        raise
    finally:
        AGENT_RUNS_FINISHED.labels(outcome=outcome).inc()
    return AgentRunResponse(**result)


@router.post("/run/stream")
async def run_agent_stream(
    http_request: Request,
    request: AgentRunRequest,
    user: UserRecord | None = Depends(get_current_user),
):
    cwd = _resolve_run_cwd(request.cwd, request.session_id, user)
    add_dirs = _resolve_run_add_dirs(request.add_dirs, cwd, request.session_id)
    username = user.username if user else None
    attachments = _validate_attachments(request.attachments, cwd)
    images = _validate_images(request.images)
    auth_method = getattr(http_request.state, "auth_method", "jwt")
    return StreamingResponse(
        agent_run_stream(
            request.message, request.session_id, request.permission_mode,
            cwd=cwd, add_dirs=add_dirs, username=username, model_override=request.model,
            auth_method=auth_method,
            attachments=attachments, images=images, mcp_servers=request.mcp_servers,
            mask_output=(auth_method == "api_key"),
            enable_file_checkpointing=request.enable_file_checkpointing,
            fork_session=request.fork_session,
            enable_permission_feedback=request.enable_permission_feedback,
        ),
        media_type="text/event-stream",
    )


_GROUP_PAGE_SIZE = 10  # first-N sessions shown per cwd group


def _sort_in_group(resp: list[SessionInfoResponse]) -> None:
    """Order a cwd's sessions: pinned first, then most-recent first.

    Two stable passes — sort by activity, then partition pinned ahead — so pins
    land inside the first ``_GROUP_PAGE_SIZE`` page even if they're old.
    """
    resp.sort(key=lambda r: r.last_modified, reverse=True)
    resp.sort(key=lambda r: not r.pinned)


@router.get("/sessions", response_model=None)
async def list_agent_sessions(
    limit: int = 20,
    offset: int = 0,
    cwd: str | None = None,
    archived: bool = False,
    source: Literal["all", "project", "global"] = "all",  # deprecated — ignored
    user: UserRecord | None = Depends(get_current_user),
) -> GroupedSessionListResponse | FlatSessionListResponse | ArchivedSessionListResponse:
    """List sessions.

    Default (no ``cwd``): grouped by cwd across ALL of the account's project
    dirs, archived sessions excluded. Groups sort active-workspace first, then
    pinned workdirs, then the rest (each tier by recent activity); within a
    group, pinned sessions float to the top. Each group carries its first
    ``_GROUP_PAGE_SIZE`` (non-archived) sessions plus a ``has_more`` flag.

    With ``cwd``: a flat paginated page for that one directory (archived
    excluded) — backs the per-group "more in this dir" loader.

    With ``archived=true``: a flat list of every archived session across all
    cwds — backs the Settings → Archived panel. ``source`` is legacy/ignored.
    """
    del source  # legacy parameter, kept for client compat
    active_cwd = get_user_workspace(user)
    meta = session_meta.read_meta()

    # Archived view (Settings → Archived): every archived session, flat.
    if archived:
        out = [
            _session_info_to_response(s, meta)
            for s in list_sessions(directory=None)
            if session_meta.get_session_flags(s.session_id, meta)["archived"]
        ]
        out.sort(key=lambda r: r.last_modified, reverse=True)
        return ArchivedSessionListResponse(sessions=out)

    # Single-cwd page (the "more in this dir" loader); archived excluded.
    if cwd:
        resp = [
            _session_info_to_response(s, meta)
            for s in list_sessions(directory=cwd)
            if not session_meta.get_session_flags(s.session_id, meta)["archived"]
        ]
        _sort_in_group(resp)
        return FlatSessionListResponse(
            cwd=cwd,
            sessions=resp[offset : offset + limit],
            total=len(resp),
            limit=limit,
            offset=offset,
        )

    # Grouped default: scan every project dir, bin by the session's real cwd,
    # dropping archived sessions (a fully-archived workdir thus disappears).
    by_cwd: dict[str, list[SessionInfoResponse]] = {}
    for s in list_sessions(directory=None):
        if session_meta.get_session_flags(s.session_id, meta)["archived"]:
            continue
        by_cwd.setdefault(s.cwd or active_cwd, []).append(
            _session_info_to_response(s, meta)
        )

    groups: list[SessionGroupResponse] = []
    for group_cwd, resp in by_cwd.items():
        _sort_in_group(resp)
        groups.append(SessionGroupResponse(
            cwd=group_cwd,
            total=len(resp),
            sessions=resp[:_GROUP_PAGE_SIZE],
            has_more=len(resp) > _GROUP_PAGE_SIZE,
            last_activity=resp[0].last_modified if resp else 0,
            pinned=session_meta.get_workdir_pinned(group_cwd, meta),
        ))

    # Order (stable passes, last wins): activity desc → pinned ahead → active
    # workspace absolute first.
    groups.sort(key=lambda g: g.last_activity, reverse=True)
    groups.sort(key=lambda g: not g.pinned)
    groups.sort(key=lambda g: g.cwd != active_cwd)

    return GroupedSessionListResponse(groups=groups, active_cwd=active_cwd)


@router.get("/sessions/{session_id}/messages", response_model=SessionMessagesResponse)
async def get_agent_session_messages(
    session_id: str,
    limit: int | None = None,
    offset: int = 0,
    user: UserRecord | None = Depends(get_current_user),
):
    """Retrieve messages from a specific past session."""
    cwd = _find_session_cwd(session_id) or get_user_workspace(user)
    messages = get_session_messages(
        session_id=session_id, directory=cwd, limit=limit, offset=offset
    )
    replay_metadata = _build_message_replay_metadata(cwd, session_id)
    sidechain_messages: list[SessionMessageResponse] = []
    if limit is None and offset == 0:
        try:
            sidechain_messages = _load_subagent_session_messages(cwd, session_id)
        except Exception:
            logger.exception("Failed to hydrate subagent messages for session %s", session_id)

    return SessionMessagesResponse(
        messages=[
            SessionMessageResponse(
                type=m.type,
                uuid=m.uuid,
                session_id=m.session_id,
                message=m.message,
                parent_tool_use_id=m.parent_tool_use_id,
                metadata=replay_metadata.get(m.uuid),
            )
            for m in messages
        ] + sidechain_messages,
        add_dirs=read_add_dirs(cwd, session_id),
    )


@router.delete("/sessions/{session_id}")
async def delete_agent_session(session_id: str, user: UserRecord | None = Depends(get_current_user)):
    """Delete a session's transcript (any cwd in this account)."""
    cwd = _find_session_cwd(session_id)
    if not cwd:
        raise HTTPException(404, "Session not found")

    session_file = _session_jsonl_path(cwd, session_id)
    if not session_file.exists():
        raise HTTPException(404, "Session file not found")

    session_file.unlink()
    delete_add_dirs(cwd, session_id)
    await session_meta.prune_session(session_id)

    actor = user.username if user else "anonymous"
    audit = get_audit_logger()
    audit.append(AuditEntry(
        actor=actor,
        action="session.deleted",
        target=session_id,
    ))

    return {"status": "ok"}


@router.post("/permission/respond")
async def respond_permission(
    http_request: Request,
    request: PermissionRespondRequest,
    user: UserRecord | None = Depends(get_current_user),
):
    coordinator = registry.get(request.session_id)
    if not coordinator:
        raise HTTPException(404, "No active session for this stream")
    owner = coordinator.owner_username
    if owner is not None and (user is None or user.username != owner):
        raise HTTPException(403, "Not authorized for this permission request")
    try:
        coordinator.resolve(
            request.request_id,
            request.decision,
            request.message or "",
            request.updated_input,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"status": "ok"}


@router.post("/rewind", response_model=RewindResponse)
async def rewind_session(
    http_request: Request,
    req: RewindRequest,
    user: UserRecord | None = Depends(get_current_user),
):
    """Rewind files on disk to the snapshot taken before a given user message.

    Requires the session to have been run with `enable_file_checkpointing=True`.
    Refuses to run while a live stream is in flight for the same session.
    """
    cwd = _find_session_cwd(req.session_id)
    if not cwd:
        raise HTTPException(404, "Session not found")
    username = user.username if user else None
    auth_method = getattr(http_request.state, "auth_method", "jwt")
    if registry.get(req.session_id):
        raise HTTPException(409, "Finish the current run before rewinding")
    opts = await build_agent_options(
        session_id=req.session_id,
        permission_mode="bypassPermissions",
        cwd=cwd, username=username,
        auth_method=auth_method,
        enable_file_checkpointing=True,
    )
    try:
        async with ClaudeSDKClient(options=opts) as client:
            await client.query("")
            async for _ in client.receive_response():
                await client.rewind_files(req.checkpoint_uuid)
                break
        get_audit_logger().append(AuditEntry(
            actor=username or "anonymous",
            action="session.rewound",
            target=req.session_id,
            details={"checkpoint_uuid": req.checkpoint_uuid},
        ))
        return RewindResponse(status="ok")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("rewind failed")
        raise HTTPException(400, f"Rewind failed: {exc}") from exc


@router.post("/fork", response_model=ForkResponse)
async def fork_agent_session(
    req: ForkRequest,
    user: UserRecord | None = Depends(get_current_user),
):
    """Fork a session — either mid-session (up_to_message_uuid) or tail fork."""
    cwd = _find_session_cwd(req.session_id)
    if not cwd:
        raise HTTPException(404, "Session not found")
    try:
        result = sdk_fork_session(
            session_id=req.session_id,
            directory=cwd,
            up_to_message_id=req.up_to_message_uuid,
            title=req.title,
        )
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc
    get_audit_logger().append(AuditEntry(
        actor=user.username if user else "anonymous",
        action="session.forked",
        target=req.session_id,
        details={"new_session_id": result.session_id, "up_to": req.up_to_message_uuid},
    ))
    return ForkResponse(
        new_session_id=result.session_id,
        parent_session_id=req.session_id,
        title=req.title,
    )


@router.patch("/sessions/{session_id}")
async def rename_agent_session(
    session_id: str,
    req: RenameRequest,
    user: UserRecord | None = Depends(get_current_user),
):
    """Rename a session by appending a custom-title entry."""
    cwd = _find_session_cwd(session_id)
    if not cwd:
        raise HTTPException(404, "Session not found")
    try:
        sdk_rename_session(
            session_id=session_id, title=req.title.strip(), directory=cwd,
        )
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc
    get_audit_logger().append(AuditEntry(
        actor=user.username if user else "anonymous",
        action="session.renamed",
        target=session_id,
        details={"title": req.title.strip()},
    ))
    return {"status": "ok"}


@router.put("/sessions/{session_id}/tag")
async def tag_agent_session(
    session_id: str,
    req: TagRequest,
    user: UserRecord | None = Depends(get_current_user),
):
    """Set or clear a session's tag (pass tag=None to clear)."""
    cwd = _find_session_cwd(session_id)
    if not cwd:
        raise HTTPException(404, "Session not found")
    try:
        sdk_tag_session(session_id=session_id, tag=req.tag, directory=cwd)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc
    get_audit_logger().append(AuditEntry(
        actor=user.username if user else "anonymous",
        action="session.tagged",
        target=session_id,
        details={"tag": req.tag},
    ))
    return {"status": "ok"}


@router.put("/sessions/{session_id}/add_dirs")
async def set_agent_session_add_dirs(
    session_id: str,
    req: AddDirsRequest,
    user: UserRecord | None = Depends(get_current_user),
):
    """Persist a session's additional directories (SDK --add-dir).

    Saved immediately so a resume on any device recovers the set; the next run
    the user starts re-launches the agent with these ``--add-dir`` flags.
    """
    cwd = _find_session_cwd(session_id)
    if not cwd:
        raise HTTPException(404, "Session not found")
    dirs = _validate_dirs(req.add_dirs)
    write_add_dirs(cwd, session_id, dirs)
    get_audit_logger().append(AuditEntry(
        actor=user.username if user else "anonymous",
        action="session.add_dirs_set",
        target=session_id,
        details={"count": len(dirs)},
    ))
    return {"status": "ok", "add_dirs": dirs}


@router.put("/sessions/{session_id}/pin")
async def pin_agent_session(
    session_id: str,
    req: PinRequest,
    user: UserRecord | None = Depends(get_current_user),
):
    """Pin/unpin a session (floats it to the top of its workdir group)."""
    if not _find_session_cwd(session_id):
        raise HTTPException(404, "Session not found")
    flags = await session_meta.set_session_flags(session_id, pinned=req.pinned)
    get_audit_logger().append(AuditEntry(
        actor=user.username if user else "anonymous",
        action="session.pinned" if req.pinned else "session.unpinned",
        target=session_id,
    ))
    return {"status": "ok", **flags}


@router.put("/sessions/{session_id}/archive")
async def archive_agent_session(
    session_id: str,
    req: ArchiveRequest,
    user: UserRecord | None = Depends(get_current_user),
):
    """Archive/unarchive a session (hides it from the default list)."""
    if not _find_session_cwd(session_id):
        raise HTTPException(404, "Session not found")
    flags = await session_meta.set_session_flags(session_id, archived=req.archived)
    get_audit_logger().append(AuditEntry(
        actor=user.username if user else "anonymous",
        action="session.archived" if req.archived else "session.restored",
        target=session_id,
    ))
    return {"status": "ok", **flags}


@router.put("/workdirs/pin")
async def pin_workdir(
    req: WorkdirPinRequest,
    user: UserRecord | None = Depends(get_current_user),
):
    """Pin/unpin a workdir (floats the whole cwd group toward the top)."""
    await session_meta.set_workdir_pinned(req.cwd, req.pinned)
    get_audit_logger().append(AuditEntry(
        actor=user.username if user else "anonymous",
        action="workdir.pinned" if req.pinned else "workdir.unpinned",
        target=req.cwd,
    ))
    return {"status": "ok", "pinned": req.pinned}


@router.put("/workdirs/archive")
async def archive_workdir(
    req: WorkdirArchiveRequest,
    user: UserRecord | None = Depends(get_current_user),
):
    """Archive a workdir by cascading the archive to every session in it; the
    group then vanishes from the default list and is restored per-session."""
    ids = [s.session_id for s in list_sessions(directory=req.cwd)]
    await session_meta.archive_workdir(ids)
    get_audit_logger().append(AuditEntry(
        actor=user.username if user else "anonymous",
        action="workdir.archived",
        target=req.cwd,
        details={"count": len(ids)},
    ))
    return {"status": "ok", "count": len(ids)}


@router.websocket("/ws/run")
async def ws_run(websocket: WebSocket):
    import uuid as _uuid
    ws_id = str(_uuid.uuid4())[:8]  # short connection tag
    logger.info("[WS:%s] Connection accepted", ws_id)
    # Echo the SPA's `priva.ws.v1` subprotocol (the token rode a sibling
    # `priva.token.<jwt>` offer) so the browser handshake completes.
    await websocket.accept(subprotocol=negotiated_subprotocol(websocket))

    # --- Read first message: init frame with auth ---
    try:
        raw_text = await websocket.receive_text()
        raw = json.loads(raw_text)
        client_tab_id = str(raw.get("client_tab_id") or "")[:32]
        prompt_preview = " ".join(str(raw.get("message") or "").split())[:160]
        logger.info("[WS:%s] INIT client_tab_id=%s prompt=%s", ws_id, client_tab_id, prompt_preview)
        frame = _ws_frame_adapter.validate_python(raw)
    except (json.JSONDecodeError, ValidationError) as exc:
        await websocket.send_json({"event": "error", "data": {"message": f"Invalid init frame: {exc}"}})
        await websocket.close(code=4000)
        return

    if not isinstance(frame, WsInitFrame):
        await websocket.send_json({"event": "error", "data": {"message": "First message must be type 'init'"}})
        await websocket.close(code=4000)
        return

    # --- Authenticate (CP-injected signed runner token on the WS handshake) ---
    try:
        user = account_from_ws(websocket)
    except HTTPException:
        await websocket.send_json({"event": "error", "data": {"message": "Authentication failed"}})
        await websocket.close(code=4001)
        return

    try:
        cwd = _resolve_run_cwd(frame.cwd, frame.session_id, user)
        add_dirs = _resolve_run_add_dirs(frame.add_dirs, cwd, frame.session_id)
    except HTTPException as exc:
        await websocket.send_json({"event": "error", "data": {"message": f"Validation failed: {exc.detail}"}})
        await websocket.close(code=4000)
        return
    username = user.username if user else None

    # The browser is always JWT-authenticated at the control-panel before the
    # proxy opens this socket, so masking follows the jwt (login-session) path.
    auth_method = "jwt"
    mask_output = auth_method == "api_key"

    logger.info(
        "[WS:%s] Authenticated user=%s session_id=%s client_tab_id=%s prompt=%s",
        ws_id,
        username,
        frame.session_id,
        client_tab_id,
        prompt_preview,
    )

    # Validate attachments and images if provided. This happens before the
    # agent stream starts, so errors must be sent explicitly over the socket.
    try:
        attachments = _validate_attachments(frame.attachments, cwd) if frame.attachments else None
        images = _validate_images(frame.images)
    except HTTPException as exc:
        await websocket.send_json({"event": "error", "data": {"message": f"Validation failed: {exc.detail}"}})
        await websocket.close(code=4000)
        return

    # --- Set up agent run ---
    cancelled = asyncio.Event()
    coordinator_out: list = [None]
    queue_out: list = [None]

    # Load masking patterns once.
    # Only applies when admin has explicitly saved patterns.
    _ws_mask_patterns: list[dict] = []
    if mask_output:
        try:
            from priva_common.user_store import get_user_store as _get_store
            runtime = _get_store().get_runtime_config()
            pii_cfg = runtime.get("pii_masking") or {}
            _ws_mask_patterns = list(pii_cfg.get("patterns") or [])
        except Exception:
            pass

    async def emit(event_type: str, data: dict) -> None:
        if event_type == "permission_request":
            logger.info(
                "[WS:%s] EMIT permission_request request_id=%s tool=%s client_tab_id=%s prompt=%s",
                ws_id,
                data.get("request_id"),
                data.get("tool_name"),
                client_tab_id,
                prompt_preview,
            )
        if event_type == "stream_init":
            logger.info("[WS:%s] EMIT stream_init stream_id=%s", ws_id, data.get("stream_id"))
        out_data = data
        if _ws_mask_patterns and event_type not in ("keepalive", "stream_init", "permission_request", "permission_timeout"):
            from priva_common.sensitive_mask import mask_sensitive
            out_data, _ = mask_sensitive(_ws_mask_patterns, data)
        try:
            await websocket.send_json({"event": event_type, "data": out_data})
        except Exception:
            cancelled.set()

    # --- Reader task: handle permission responses and abort ---
    async def reader() -> None:
        try:
            while True:
                text = await websocket.receive_text()
                try:
                    raw_msg = json.loads(text)
                    msg = _ws_frame_adapter.validate_python(raw_msg)
                except (json.JSONDecodeError, ValidationError) as exc:
                    await websocket.send_json({"event": "error", "data": {"message": f"Invalid frame: {exc}"}})
                    continue

                if isinstance(msg, WsPermissionFrame):
                    coord = coordinator_out[0]
                    if coord:
                        try:
                            coord.resolve(
                                msg.request_id,
                                msg.decision,
                                msg.message or "",
                                msg.updated_input,
                            )
                        except ValueError as exc:
                            await websocket.send_json({"event": "error", "data": {"message": str(exc)}})
                    else:
                        await websocket.send_json({"event": "error", "data": {"message": "No permission coordinator active"}})
                elif isinstance(msg, WsInitFrame):
                    await websocket.send_json({"event": "error", "data": {"message": "Already initialized"}})
                elif isinstance(msg, WsQueueFrame):
                    q = queue_out[0]
                    if q is None:
                        await websocket.send_json({"event": "error", "data": {"message": "No active stream to queue into"}})
                        continue
                    try:
                        q_attachments = _validate_attachments(msg.attachments, cwd) if msg.attachments else []
                        q_images = _validate_images(msg.images) if msg.images else []
                    except HTTPException as exc:
                        await websocket.send_json({"event": "error", "data": {"message": f"Queue validation failed: {exc.detail}"}})
                        continue
                    await q.put((msg.id, msg.text, q_attachments or [], q_images or []))
                    await websocket.send_json({"event": "queued", "data": {"id": msg.id}})
                elif isinstance(msg, WsQueueCancelFrame):
                    q = queue_out[0]
                    if q is None:
                        await websocket.send_json({"event": "error", "data": {"message": "No active stream to cancel from"}})
                        continue
                    # asyncio.Queue has no random-access delete: drain + rebuild
                    remaining: list[tuple[str, str, list, list]] = []
                    removed = False
                    while not q.empty():
                        try:
                            entry = q.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        if entry[0] == msg.id and not removed:
                            removed = True
                            continue
                        remaining.append(entry)
                    for entry in remaining:
                        q.put_nowait(entry)
                    if removed:
                        await websocket.send_json({"event": "queue_cancelled", "data": {"id": msg.id}})
                    else:
                        await websocket.send_json({"event": "error", "data": {"message": f"Queued id not found: {msg.id}"}})
                else:
                    # WsAbortFrame
                    cancelled.set()
                    return
        except WebSocketDisconnect:
            cancelled.set()

    reader_task = asyncio.create_task(reader())

    AGENT_RUNS_STARTED.inc()
    uncaught_exc: Exception | None = None
    try:
        await agent_run_events(
            frame.message,
            frame.session_id,
            frame.permission_mode,
            cwd,
            username,
            frame.model,
            auth_method=auth_method,
            add_dirs=add_dirs,
            emit=emit,
            cancelled=cancelled,
            coordinator_out=coordinator_out,
            queue_out=queue_out,
            attachments=attachments,
            images=images,
            mcp_servers=frame.mcp_servers,
            enable_file_checkpointing=frame.enable_file_checkpointing,
            fork_session=frame.fork_session,
            enable_permission_feedback=frame.enable_permission_feedback,
        )
    except Exception as exc:
        uncaught_exc = exc
        logger.exception("WebSocket agent run error")
        try:
            await websocket.send_json({"event": "stream_error", "data": {
                "code": type(exc).__name__,
                "message": str(exc) or repr(exc),
                "fatal": True,
                "api_error_status": getattr(exc, "api_error_status", None),
            }})
        except Exception:
            pass
    finally:
        if uncaught_exc is not None:
            run_outcome = "error"
        elif cancelled.is_set():
            run_outcome = "cancelled"
        else:
            run_outcome = "success"
        AGENT_RUNS_FINISHED.labels(outcome=run_outcome).inc()

        reader_task.cancel()
        try:
            await reader_task
        except asyncio.CancelledError:
            pass
        try:
            # 4500 = server error; distinct from 4000 (protocol) and 4001 (auth)
            close_code = 4500 if uncaught_exc is not None else 1000
            await websocket.close(code=close_code)
        except Exception:
            pass
