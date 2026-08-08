from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
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
from claude_agent_sdk._internal.sessions import (
    _canonicalize_path,
    _get_claude_config_home_dir,
    _get_project_dir,
)
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
    WsAttachFrame,
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
from ..services.claude_sdk.run_registry import RUN_END_EVENT, RunRecord, run_registry
from priva_common.user_store import UserRecord
from priva_common.metrics import AGENT_RUNS_FINISHED, AGENT_RUNS_STARTED

import os


# Claude-native image block types. The web client rasterizes SVG/BMP to PNG
# before this boundary because the provider does not accept those MIME types.
_ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MiB decoded
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
    sched = session_meta.get_scheduler_info(s.session_id, meta)
    tags = session_meta.get_session_tags(
        s.session_id,
        meta,
        fallback=getattr(s, "tag", None),
    )
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
        tag=tags[0] if tags else None,
        tags=tags,
        tag_colors=session_meta.get_tag_colors(tags, meta),
        pinned=flags["pinned"],
        archived=flags["archived"],
        origin="scheduler" if sched else None,
        scheduler_job_name=(sched or {}).get("job_name") or None,
        last_response_model=session_meta.get_last_response_model(s.session_id, meta),
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
        try:
            decoded_size = len(base64.b64decode(img.data, validate=True))
        except (binascii.Error, ValueError):
            raise HTTPException(400, "Invalid base64 image data") from None
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


_WORKFLOW_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _texts_from_blocks(blocks: list[dict]) -> str:
    return "\n".join(
        b["text"]
        for b in blocks
        if b.get("type") == "text" and isinstance(b.get("text"), str)
    ).strip()


def _extract_agent_prompt_result(path: Path) -> tuple[str | None, str | None]:
    """Recover a workflow sub-agent's full prompt + result from its transcript.

    The first ``user`` record is the prompt the agent was launched with. The
    result is the agent's return value: for schema agents that's the final
    ``StructuredOutput`` tool input, otherwise the last assistant text. The
    streamed task_progress events only carry truncated previews of these.
    """
    prompt: str | None = None
    structured: str | None = None
    last_assistant_text: str | None = None
    for raw in _iter_jsonl_dicts(path):
        message = raw.get("message")
        role = message.get("role") if isinstance(message, dict) else None
        is_user = raw.get("type") == "user" or role == "user"
        is_assistant = raw.get("type") == "assistant" or role == "assistant"
        if prompt is None and is_user:
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str) and content.strip():
                prompt = content.strip()
            else:
                text = _texts_from_blocks(_message_content_blocks(raw))
                if text:
                    prompt = text
        if is_assistant:
            blocks = _message_content_blocks(raw)
            text = _texts_from_blocks(blocks)
            if text:
                last_assistant_text = text
            for block in blocks:
                if block.get("type") == "tool_use" and block.get("name") == "StructuredOutput":
                    payload = block.get("input")
                    if isinstance(payload, dict):
                        try:
                            structured = json.dumps(payload, indent=2, ensure_ascii=False)
                        except (TypeError, ValueError):
                            structured = None
    return prompt, (structured or last_assistant_text)


def _read_workflow_agent_transcript(agent_id: str) -> dict | None:
    """Locate ``agent-<id>.jsonl`` under the user's projects tree and pull the
    full prompt + result out of it. ``None`` when no transcript exists yet."""
    projects = _get_claude_config_home_dir() / "projects"
    match = next(projects.rglob(f"agent-{agent_id}.jsonl"), None)
    if match is None:
        return None
    prompt, result = _extract_agent_prompt_result(match)
    return {"agentId": agent_id, "prompt": prompt, "result": result}


_WORKFLOW_RUN_ID_RE = re.compile(r"^wf_[A-Za-z0-9_-]{1,64}$")

# Fields the workflow inspector needs to repaint a card on session reload. The
# on-disk snapshot (workflows/<runId>.json) also stores the full script, logs and
# result — omitted here to keep the payload lean; workflowProgress is the bulk.
_WORKFLOW_STATE_KEYS = (
    "runId", "taskId", "status", "summary", "workflowName", "startTime",
    "durationMs", "totalTokens", "totalToolCalls", "agentCount",
    "phases", "workflowProgress",
)


def _read_workflow_state(run_id: str) -> dict | None:
    """Load the persisted workflow snapshot ``workflows/<run_id>.json``.

    task_progress/task_notification events are NOT written to the session
    transcript, so a reloaded session has no per-agent detail. The workflow
    engine persists the full running state (phases + agents + status, same shape
    as the live task_progress stream) to this snapshot — the inspector reads it
    to rehydrate on reload. ``None`` when no snapshot exists."""
    projects = _get_claude_config_home_dir() / "projects"
    match = next(projects.rglob(f"workflows/{run_id}.json"), None)
    if match is None:
        return None
    try:
        data = json.loads(match.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {k: data.get(k) for k in _WORKFLOW_STATE_KEYS}


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


@router.get("/workflow-agent/{agent_id}", response_model=None)
async def get_workflow_agent_transcript(
    agent_id: str,
    user: UserRecord | None = Depends(get_current_user),
):
    """Full prompt + result for a workflow sub-agent.

    The streamed task_progress events only carry truncated
    promptPreview/resultPreview; this reads the complete text from the agent's
    on-disk transcript (``agent-<id>.jsonl`` — the same data the
    ``claude /workflows`` drill-down shows). Fetched lazily by the Canvas
    workflow inspector when an agent row is expanded.
    """
    if not _WORKFLOW_AGENT_ID_RE.match(agent_id):
        raise HTTPException(400, "invalid agent id")
    data = await asyncio.to_thread(_read_workflow_agent_transcript, agent_id)
    if data is None:
        raise HTTPException(404, "agent transcript not found")
    return data


@router.get("/workflow-state/{run_id}", response_model=None)
async def get_workflow_state(
    run_id: str,
    user: UserRecord | None = Depends(get_current_user),
):
    """Persisted workflow snapshot for a run (phases + agents + status).

    task_progress events aren't saved to the session transcript, so a reloaded
    session has no per-agent detail. The Canvas inspector fetches this snapshot
    to rehydrate the workflow card the same way the live task_progress stream
    populated it. Rides the control-panel "/" lane (snapshots run tens of KB).
    """
    if not _WORKFLOW_RUN_ID_RE.match(run_id):
        raise HTTPException(400, "invalid run id")
    data = await asyncio.to_thread(_read_workflow_state, run_id)
    if data is None:
        raise HTTPException(404, "workflow state not found")
    return data


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
            extra_disallowed_tools=request.disallowed_tools,
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
    meta = session_meta.read_meta()
    listed_sessions = list_sessions(directory=cwd if cwd and not archived else None)
    # One-time migration for SDK-era single tags: reserve their stable slots
    # before serializing the list, including tags that have never been edited
    # through Priva's multi-tag endpoint.
    listed_tags = [
        tag
        for session in listed_sessions
        for tag in session_meta.get_session_tags(
            session.session_id, meta, fallback=getattr(session, "tag", None)
        )
    ]
    meta = await session_meta.ensure_tag_colors(listed_tags)
    recent_activities = session_meta.get_recent_activities(meta)
    active_cwd = (
        recent_activities[0].get("cwd")
        if recent_activities and isinstance(recent_activities[0], dict)
        else None
    ) or get_user_workspace(user)

    # Archived view (Settings → Archived): every archived session, flat.
    if archived:
        out = [
            _session_info_to_response(s, meta)
            for s in listed_sessions
            if session_meta.get_session_flags(s.session_id, meta)["archived"]
        ]
        out.sort(key=lambda r: r.last_modified, reverse=True)
        return ArchivedSessionListResponse(sessions=out)

    # Single-cwd page (the "more in this dir" loader); archived excluded.
    if cwd:
        resp = [
            _session_info_to_response(s, meta)
            for s in listed_sessions
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
    for s in listed_sessions:
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


@router.get("/sessions/running")
async def list_running_sessions(user: UserRecord | None = Depends(get_current_user)):
    """Runs still executing in the in-process RunRegistry.

    Backs the SPA's boot restore: for each entry the client hydrates the
    session snapshot (cut at ``first_user_uuid``), then attaches over the WS
    with a full replay — running/attention dots survive a page refresh.
    """
    return {"running": run_registry.list_active()}


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
    await session_meta.record_recent_activity(cwd, session_id)
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


@router.get("/sessions/{session_id}/recap")
async def get_agent_session_recap(
    session_id: str,
    user: UserRecord | None = Depends(get_current_user),
):
    """The session's one-line recap, if one has been generated.

    ``turns`` is the message count the text was derived from, so a client that
    polls after a turn can tell a refreshed recap from the one it already has.
    Returns nulls rather than 404 when there is none — "not summarized yet" is
    an ordinary state, not an error.
    """
    del user  # auth only
    recap = session_meta.get_recap(session_id)
    return {
        "recap": recap["text"] if recap else None,
        "turns": recap["turns"] if recap else 0,
    }


@router.delete("/sessions/{session_id}")
async def delete_agent_session(session_id: str, user: UserRecord | None = Depends(get_current_user)):
    """Delete a session's transcript (any cwd in this account)."""
    # A still-running detached run must not keep appending to a deleted file.
    live = run_registry.live_for_session(session_id)
    if live is not None:
        live.cancelled.set()
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
        # WS-path runs park their coordinator on the RunRegistry record
        # (client may address it by stream/run id OR session id).
        record = run_registry.get(session_id=request.session_id, run_id=request.session_id)
        coordinator = record.coordinator_out[0] if record else None
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
    if registry.get(req.session_id) or run_registry.live_for_session(req.session_id):
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
    """Replace a session's tags (maximum three; legacy ``tag`` is accepted)."""
    cwd = _find_session_cwd(session_id)
    if not cwd:
        raise HTTPException(404, "Session not found")
    try:
        raw_tags = (
            req.tags if req.tags is not None else ([req.tag] if req.tag else [])
        )
        tags = session_meta.normalize_session_tags(raw_tags)
        # Keep the SDK's single tag in sync for older clients/tools. Priva's
        # account metadata is authoritative for the full list.
        sdk_tag_session(
            session_id=session_id,
            tag=tags[0] if tags else None,
            directory=cwd,
        )
        result = await session_meta.set_session_tags(session_id, tags)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc
    get_audit_logger().append(AuditEntry(
        actor=user.username if user else "anonymous",
        action="session.tagged",
        target=session_id,
        details={"tag": tags[0] if tags else None, "tags": tags},
    ))
    return {"status": "ok", **result}


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
    """Agent-run WebSocket: start a new run (`init`) or join a live one (`attach`).

    Runs are owned by the RunRegistry, not by this socket — the socket dying
    merely detaches a follower while the run keeps executing. Only an explicit
    `abort` frame (or run completion) ends a run.
    """
    import uuid as _uuid
    ws_id = str(_uuid.uuid4())[:8]  # short connection tag
    logger.info("[WS:{}] Connection accepted", ws_id)
    # Echo the SPA's `priva.ws.v1` subprotocol (the token rode a sibling
    # `priva.token.<jwt>` offer) so the browser handshake completes.
    await websocket.accept(subprotocol=negotiated_subprotocol(websocket))

    # --- Read first message: init or attach frame with auth ---
    try:
        raw_text = await websocket.receive_text()
        raw = json.loads(raw_text)
        client_tab_id = str(raw.get("client_tab_id") or "")[:32]
        prompt_preview = " ".join(str(raw.get("message") or "").split())[:160]
        logger.info(
            "[WS:{}] FIRST-FRAME type={} client_tab_id={} prompt={}",
            ws_id, raw.get("type"), client_tab_id, prompt_preview,
        )
        frame = _ws_frame_adapter.validate_python(raw)
    except (json.JSONDecodeError, ValidationError) as exc:
        await websocket.send_json({"event": "error", "data": {"message": f"Invalid init frame: {exc}"}})
        await websocket.close(code=4000)
        return

    if not isinstance(frame, (WsInitFrame, WsAttachFrame)):
        await websocket.send_json({"event": "error", "data": {"message": "First message must be type 'init' or 'attach'"}})
        await websocket.close(code=4000)
        return

    # --- Authenticate (CP-injected signed runner token on the WS handshake) ---
    try:
        user = account_from_ws(websocket)
    except HTTPException:
        await websocket.send_json({"event": "error", "data": {"message": "Authentication failed"}})
        await websocket.close(code=4001)
        return
    username = user.username if user else None

    # --- Attach: join a run already executing in the registry ---
    if isinstance(frame, WsAttachFrame):
        record = run_registry.get(session_id=frame.session_id, run_id=frame.run_id)
        if record is None:
            logger.info(
                "[WS:{}] ATTACH no run session_id={} run_id={}",
                ws_id, frame.session_id, frame.run_id,
            )
            await websocket.send_json({"event": "attach_error", "data": {
                "code": "no_run",
                "session_id": frame.session_id,
            }})
            try:
                await websocket.close(code=1000)
            except Exception:
                pass
            return
        logger.info(
            "[WS:{}] ATTACH run_id={} session_id={} since_seq={} status={}",
            ws_id, record.run_id[:8], record.session_id, frame.since_seq, record.status,
        )
        queue_cwd = (_find_session_cwd(record.session_id) if record.session_id else None) or get_user_workspace(user)
        await _ws_follow(
            websocket, record,
            since_seq=max(0, frame.since_seq or 0),
            send_attach_ok=True,
            cwd_for_queue=queue_cwd,
            ws_id=ws_id,
        )
        return

    # --- Init: validate and start a new run ---
    try:
        cwd = _resolve_run_cwd(frame.cwd, frame.session_id, user)
        add_dirs = _resolve_run_add_dirs(frame.add_dirs, cwd, frame.session_id)
    except HTTPException as exc:
        await websocket.send_json({"event": "error", "data": {"message": f"Validation failed: {exc.detail}"}})
        await websocket.close(code=4000)
        return

    logger.info(
        "[WS:{}] Authenticated user={} session_id={} client_tab_id={} prompt={}",
        ws_id, username, frame.session_id, client_tab_id, prompt_preview,
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

    # A session hosts at most one live run (a second concurrent resume would
    # corrupt the session JSONL). The client should attach instead.
    if frame.session_id:
        live = run_registry.live_for_session(frame.session_id)
        if live is not None:
            logger.warning(
                "[WS:{}] REFUSED double-run session_id={} (live run_id={})",
                ws_id, frame.session_id, live.run_id[:8],
            )
            await websocket.send_json({"event": "stream_error", "data": {
                "code": "RunAlreadyActive",
                "message": "This session already has a run in progress — attach to it or stop it first.",
                "fatal": True,
            }})
            await websocket.close(code=4000)
            return

    record = run_registry.create(session_id=frame.session_id)
    record.task = asyncio.create_task(
        _execute_run(record, frame, cwd, add_dirs, username, attachments, images),
        name=f"agent-run-{record.run_id[:8]}",
    )
    await _ws_follow(
        websocket, record,
        since_seq=0,
        send_attach_ok=False,
        cwd_for_queue=cwd,
        ws_id=ws_id,
    )


async def _execute_run(
    record: RunRecord,
    frame: WsInitFrame,
    cwd: str,
    add_dirs: list[str],
    username: str | None,
    attachments: list | None,
    images: list | None,
) -> None:
    """Registry-owned run task: pump agent_run_events into the record.

    Lives independently of any socket. Sockets subscribe to the record;
    ``record.cancelled`` (abort frame / session delete) stops the run.
    """

    async def emit(event_type: str, data: dict) -> None:
        if event_type == "permission_request":
            logger.info(
                "[RUN:{}] EMIT permission_request request_id={} tool={}",
                record.run_id[:8], data.get("request_id"), data.get("tool_name"),
            )
        if event_type == "stream_init":
            logger.info("[RUN:{}] EMIT stream_init stream_id={}", record.run_id[:8], data.get("stream_id"))
            if data.get("stream_id"):
                run_registry.index_run_id(record, data["stream_id"])
        # Track the CLI-assigned session id so attach-by-session-id works
        # mid-run (system.init fires right at turn start).
        if event_type == "system" and (data or {}).get("subtype") == "init":
            inner = (data or {}).get("data") or {}
            if isinstance(inner, dict) and inner.get("session_id"):
                run_registry.index_session(record, inner["session_id"])
        if event_type == "result" and (data or {}).get("session_id"):
            run_registry.index_session(record, data["session_id"])
        record.record_event(event_type, data)

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
            auth_method="jwt",
            add_dirs=add_dirs,
            emit=emit,
            cancelled=record.cancelled,
            coordinator_out=record.coordinator_out,
            queue_out=record.queue_out,
            attachments=attachments,
            images=images,
            mcp_servers=frame.mcp_servers,
            enable_file_checkpointing=frame.enable_file_checkpointing,
            fork_session=frame.fork_session,
            enable_permission_feedback=frame.enable_permission_feedback,
            include_partial_messages=frame.include_partial_messages,
        )
    except asyncio.CancelledError:
        # Process shutdown — finalize synchronously and let cancellation flow.
        AGENT_RUNS_FINISHED.labels(outcome="cancelled").inc()
        run_registry.finish(record, "aborted")
        record.record_event(RUN_END_EVENT, {"status": record.status})
        raise
    except Exception as exc:
        uncaught_exc = exc
        logger.exception("[RUN:{}] agent run error", record.run_id[:8])
        try:
            record.record_event("stream_error", {
                "code": type(exc).__name__,
                "message": str(exc) or repr(exc),
                "fatal": True,
                "api_error_status": getattr(exc, "api_error_status", None),
            })
        except Exception:
            pass
    finally:
        if record.status == "running":
            if uncaught_exc is not None:
                outcome, status = "error", "error"
            elif record.cancelled.is_set():
                outcome, status = "cancelled", "aborted"
            else:
                outcome, status = "success", "completed"
            AGENT_RUNS_FINISHED.labels(outcome=outcome).inc()
            run_registry.finish(record, status)
            record.record_event(RUN_END_EVENT, {"status": record.status})


async def _ws_follow(
    websocket: WebSocket,
    record: RunRecord,
    *,
    since_seq: int,
    send_attach_ok: bool,
    cwd_for_queue: str,
    ws_id: str,
) -> None:
    """Stream a record to one socket: optional replay, then live follow.

    The socket dying only detaches this follower — the run keeps executing
    in the registry and a later `attach` resumes from the last seen seq.
    """

    async def send_event(seq: int | None, event_type: str, data: dict) -> None:
        payload = {"event": event_type, "data": data}
        if seq is not None:
            payload["seq"] = seq
        await websocket.send_json(payload)

    sub_id, q = record.subscribe()
    reader_task = asyncio.create_task(_ws_reader(websocket, record, cwd_for_queue))
    sent_through = since_seq
    socket_alive = True
    run_ended = False
    try:
        if send_attach_ok:
            await send_event(None, "attach_ok", {
                "session_id": record.session_id,
                "run_id": record.run_id,
                "status": record.status,
                "started_at": record.started_at,
                "first_user_uuid": record.first_user_uuid,
                "replay_from": max(since_seq + 1, record.first_seq),
                "replay_gap": record.has_replay_gap(since_seq),
                "queued": record.queued_entries(),
            })
        # Replay the buffer. permission_requests already resolved are filtered
        # so a stale approval card can't reappear after a refresh.
        outstanding = record.outstanding_permission_ids()
        for seq, event_type, data in record.replay_since(since_seq):
            sent_through = seq
            if event_type == RUN_END_EVENT:
                run_ended = True
                break
            if event_type == "permission_request" and data.get("request_id") not in outstanding:
                continue
            await send_event(seq, event_type, data)
        # Live follow.
        while not run_ended:
            try:
                item = await asyncio.wait_for(q.get(), timeout=2.0)
            except asyncio.TimeoutError:
                await send_event(None, "keepalive", {})
                continue
            seq, event_type, data = item
            if seq <= sent_through:
                continue  # already covered by the replay snapshot
            sent_through = seq
            if event_type == RUN_END_EVENT:
                run_ended = True
                break
            await send_event(seq, event_type, data)
    except (WebSocketDisconnect, RuntimeError, ConnectionError):
        # Socket died — detach silently; the run continues in the registry.
        socket_alive = False
        logger.info("[WS:{}] follower detached run_id={} (run {})", ws_id, record.run_id[:8], record.status)
    except Exception:
        socket_alive = False
        logger.exception("[WS:{}] follower error run_id={}", ws_id, record.run_id[:8])
    finally:
        record.unsubscribe(sub_id)
        reader_task.cancel()
        try:
            await reader_task
        except asyncio.CancelledError:
            pass
    if socket_alive and run_ended:
        try:
            await websocket.close(code=1000)
        except Exception:
            pass


async def _ws_reader(websocket: WebSocket, record: RunRecord, cwd: str) -> None:
    """Incoming frames from one attached socket → the record's run."""
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
                coord = record.coordinator_out[0]
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
            elif isinstance(msg, (WsInitFrame, WsAttachFrame)):
                await websocket.send_json({"event": "error", "data": {"message": "Already initialized"}})
            elif isinstance(msg, WsQueueFrame):
                q = record.queue_out[0]
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
                q = record.queue_out[0]
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
                # WsAbortFrame — cancel the RUN itself (stop button), not just
                # this socket. The follower closes once RUN_END drains through.
                logger.info("[RUN:{}] abort requested via socket", record.run_id[:8])
                record.cancelled.set()
                return
    except WebSocketDisconnect:
        return
