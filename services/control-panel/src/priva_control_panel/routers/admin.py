from __future__ import annotations

import asyncio
import secrets
from pathlib import Path

from claude_agent_sdk import list_sessions
from fastapi import APIRouter, Depends, HTTPException, Query

from priva_common.models.admin import (
    AdminStatsResponse,
    AuditEntryResponse,
    AuditLogResponse,
    CliPathResponse,
    CliPathUpdate,
    FleetAccountEntry,
    FleetResponse,
    HistoryRetentionResponse,
    HistoryRetentionUpdate,
    PendingRegistrationResponse,
    PresetPromptResponse,
    PresetPromptUpdate,
    RetryableToolEntry,
    RetryCallbackWeComConfig,
    RetryableToolsResponse,
    RetryableToolsUpdate,
    RiskyToolsResponse,
    RiskyToolsUpdate,
    SensitivePatternEntry,
    SensitivePatternsResponse,
    SensitivePatternsUpdate,
    UserStatsEntry,
)
from priva_common.models.plugin import PluginConfigUpdate, PluginInfo, PluginListResponse
from priva_common.models.auth import UserCreate, UserPublic, UserUpdate
from priva_common.models.mcp import McpLevel, McpServerListResponse
from priva_common.models.skills import SkillLevel, SkillListResponse
from priva_common.audit_log import AuditEntry, get_audit_logger
from ..services.auth import require_admin, user_record_to_public
from priva_common.config import get_settings
# Scheduler (deferred, Phase 4) and skills (moved to agent-runner) services are
# not available to the control-panel; the few admin endpoints that used them
# return 503 below. Per-user skill management is reached via the proxied
# /api/resource/skills routes instead.
from ..services.secret_env import write_user_env
from priva_common.logging import get_app_logger
from priva_common.user_store import UserRecord, get_user_store

logger = get_app_logger(__name__)

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


@router.get("/users", response_model=list[UserPublic])
async def list_users():
    from priva_common.dataplane import get_client

    store = get_user_store()
    # One resource_specs.list() call, mapped by account_id, so the table's RUNNER
    # column + the edit-drawer prefill don't fan out N gRPC calls.
    specs = {s.account_id: s for s in get_client().resource_specs.list()}
    out: list[UserPublic] = []
    for u in store.list_users():
        pub = user_record_to_public(u)
        spec = specs.get(u.account_id)
        if spec is not None:
            pub.cpu_cores = spec.cpu_cores
            pub.memory_mb = spec.memory_mb
            pub.volume_gb = spec.volume_gb
        out.append(pub)
    return out


@router.post("/users", response_model=UserPublic)
async def create_user(
    request: UserCreate,
    current_user: UserRecord = Depends(require_admin),
):
    store = get_user_store()
    settings = get_settings()
    password = request.password or settings.auth.default_password
    try:
        user = store.create_user(request.username, password, request.role)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    # Provision the per-account agent-runner (operator reconciles the AgentTenant CR).
    from ..provisioner import ensure_tenant
    try:
        ensure_tenant(user.account_id, user.username)
    except Exception as exc:  # pragma: no cover
        logger.warning("provision tenant failed for {}: {}", user.username, exc)

    # Write env if provided
    if request.env:
        env_dict = request.env.model_dump(exclude_none=True)
        if env_dict:
            write_user_env(request.username, env_dict)

    audit = get_audit_logger()
    audit.append(AuditEntry(
        actor=current_user.username,
        action="user.created",
        target=request.username,
        details={"role": request.role},
    ))

    return user_record_to_public(user)


@router.put("/users/{username}", response_model=UserPublic)
async def update_user(
    username: str,
    request: UserUpdate,
    current_user: UserRecord = Depends(require_admin),
):
    store = get_user_store()
    existing = store.get_user(username)
    if existing is None:
        raise HTTPException(404, f"User '{username}' not found")

    # Last admin protection on role demotion
    if request.role and request.role != "admin" and existing.role == "admin":
        if store.count_admins() <= 1:
            raise HTTPException(400, "Cannot remove the last admin")

    kwargs = {}
    audit = get_audit_logger()

    if request.password is not None:
        kwargs["password"] = request.password
        audit.append(AuditEntry(
            actor=current_user.username,
            action="user.password_reset",
            target=username,
        ))

    if request.role is not None and request.role != existing.role:
        kwargs["role"] = request.role
        audit.append(AuditEntry(
            actor=current_user.username,
            action="user.role_changed",
            target=username,
            details={"old_role": existing.role, "new_role": request.role},
        ))
    elif request.role is not None:
        kwargs["role"] = request.role

    if request.api_key is not None:
        if request.api_key == "__generate__":
            kwargs["api_key"] = "sk-" + secrets.token_hex(24)
            audit.append(AuditEntry(
                actor=current_user.username,
                action="user.apikey_generated",
                target=username,
            ))
        elif request.api_key == "__revoke__":
            kwargs["api_key"] = None
            audit.append(AuditEntry(
                actor=current_user.username,
                action="user.apikey_revoked",
                target=username,
            ))
        else:
            kwargs["api_key"] = request.api_key

    if request.agent_runner_type is not None:
        if request.agent_runner_type not in ("auto_scale", "persistent"):
            raise HTTPException(400, "Invalid agent_runner_type")
        if request.agent_runner_type != existing.agent_runner_type:
            kwargs["agent_runner_type"] = request.agent_runner_type
            audit.append(AuditEntry(
                actor=current_user.username,
                action="user.runner_type_changed",
                target=username,
                details={"old": existing.agent_runner_type, "new": request.agent_runner_type},
            ))

    # Write env if provided
    if request.env:
        env_dict = request.env.model_dump(exclude_none=True)
        if env_dict:
            write_user_env(username, env_dict)

    try:
        user = store.update_user(username, **kwargs)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    # Resource-spec edit + live CR reconcile. Persist the spec in data-spine, then
    # patch the AgentTenant CR; the operator's field handlers apply it to the pod
    # (Recreate restart for cpu/mem, grow for volume, scale for persistent toggle).
    from priva_common.dataplane import get_client

    spec_kwargs = {}
    if request.cpu_cores is not None:
        spec_kwargs["cpu_cores"] = request.cpu_cores
    if request.memory_mb is not None:
        spec_kwargs["memory_mb"] = request.memory_mb
    if request.volume_gb is not None:
        spec_kwargs["volume_gb"] = request.volume_gb
    if spec_kwargs:
        get_client().resource_specs.set(user.account_id, **spec_kwargs)
        audit.append(AuditEntry(
            actor=current_user.username,
            action="user.resource_spec_changed",
            target=username,
            details=spec_kwargs,
        ))

    if request.agent_runner_type is not None or spec_kwargs:
        from ..provisioner import update_tenant_runtime
        try:
            update_tenant_runtime(
                user.account_id,
                runner_type=request.agent_runner_type,
                cpu=request.cpu_cores,
                memory_mb=request.memory_mb,
                storage_gb=request.volume_gb,
            )
        except Exception as exc:  # pragma: no cover - kube optional locally
            logger.warning("update_tenant_runtime failed for {}: {}", username, exc)

    pub = user_record_to_public(user)
    spec = get_client().resource_specs.get(user.account_id)
    if spec is not None:
        pub.cpu_cores, pub.memory_mb, pub.volume_gb = spec.cpu_cores, spec.memory_mb, spec.volume_gb
    return pub


@router.delete("/users/{username}")
async def delete_user(
    username: str,
    current_user: UserRecord = Depends(require_admin),
):
    store = get_user_store()
    if username == current_user.username:
        raise HTTPException(400, "Cannot delete your own account")

    existing = store.get_user(username)
    if existing is None:
        raise HTTPException(404, f"User '{username}' not found")

    # Last admin protection
    if existing.role == "admin" and store.count_admins() <= 1:
        raise HTTPException(400, "Cannot remove the last admin")

    try:
        store.delete_user(username)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    # Scheduler job cleanup for the deleted user is deferred (Phase 4): the
    # scheduler subsystem is not part of this deployment yet.

    audit = get_audit_logger()
    audit.append(AuditEntry(
        actor=current_user.username,
        action="user.deleted",
        target=username,
        details={"role": existing.role},
    ))

    return {"status": "ok"}


# --- Self-registration approval ---
# Guarded by require_admin (router-level), which accepts an admin JWT OR an admin's
# account api_key (authenticate_raw_token resolves the bearer token either way).

@router.get("/pending-registrations", response_model=list[PendingRegistrationResponse])
async def list_pending_registrations():
    from priva_common.dataplane import get_client

    items = get_client().registrations.list("pending")
    return [
        PendingRegistrationResponse(
            request_id=p.request_id,
            username=p.username,
            display_name=p.display_name,
            runner_type=p.runner_type,
            cpu_cores=p.cpu_cores,
            memory_mb=p.memory_mb,
            volume_gb=p.volume_gb,
            note=p.note,
            status=p.status,
            created_at=p.created_at,
        )
        for p in items
    ]


@router.post("/pending-registrations/{request_id}/approve", response_model=UserPublic)
async def approve_registration(
    request_id: str,
    current_user: UserRecord = Depends(require_admin),
):
    from priva_common.dataplane import get_client

    client = get_client()
    pending = client.registrations.get(request_id)  # includes password_hash
    if pending is None:
        raise HTTPException(404, "Registration request not found")
    if pending.status != "pending":
        raise HTTPException(409, f"Request already {pending.status}")

    store = get_user_store()
    if store.get_user(pending.username) is not None:
        raise HTTPException(409, f"User '{pending.username}' already exists")

    # Create the account directly from the stored bcrypt hash (user can log in now).
    try:
        user = store.create_user(
            pending.username,
            role="user",
            agent_runner_type=pending.runner_type,
            password_hash=pending.password_hash,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    # Seed the resource spec, then provision the tenant with the requested values.
    client.resource_specs.set(
        user.account_id,
        cpu_cores=pending.cpu_cores,
        memory_mb=pending.memory_mb,
        volume_gb=pending.volume_gb,
    )
    from ..provisioner import ensure_tenant
    try:
        ensure_tenant(
            user.account_id, user.username,
            runner_type=pending.runner_type,
            cpu=pending.cpu_cores,
            memory_mb=pending.memory_mb,
            storage_gb=pending.volume_gb,
        )
    except Exception as exc:  # pragma: no cover - kube optional locally
        logger.warning("provision tenant failed for {}: {}", user.username, exc)

    client.registrations.set_status(request_id, "approved")

    audit = get_audit_logger()
    audit.append(AuditEntry(
        actor=current_user.username,
        action="registration.approved",
        target=pending.username,
        details={"request_id": request_id, "runner_type": pending.runner_type},
    ))

    pub = user_record_to_public(user)
    pub.cpu_cores, pub.memory_mb, pub.volume_gb = pending.cpu_cores, pending.memory_mb, pending.volume_gb
    return pub


@router.post("/pending-registrations/{request_id}/reject")
async def reject_registration(
    request_id: str,
    current_user: UserRecord = Depends(require_admin),
):
    from priva_common.dataplane import get_client

    client = get_client()
    pending = client.registrations.get(request_id)
    if pending is None:
        raise HTTPException(404, "Registration request not found")
    if pending.status != "pending":
        raise HTTPException(409, f"Request already {pending.status}")

    client.registrations.set_status(request_id, "rejected")
    audit = get_audit_logger()
    audit.append(AuditEntry(
        actor=current_user.username,
        action="registration.rejected",
        target=pending.username,
        details={"request_id": request_id},
    ))
    return {"status": "ok"}


@router.get("/stats", response_model=AdminStatsResponse)
async def get_admin_stats():
    store = get_user_store()
    settings = get_settings()
    work_dir = Path(settings.server.work_dir).expanduser()

    users = store.list_users()
    user_stats: list[UserStatsEntry] = []
    total_sessions = 0
    total_storage = 0

    for u in users:
        user_workspace = work_dir / u.username
        session_count = 0
        storage_bytes = 0
        last_active = None

        if user_workspace.exists():
            try:
                sessions = list_sessions(directory=str(user_workspace))
                session_count = len(sessions)
                for s in sessions:
                    storage_bytes += s.file_size or 0
                    if s.last_modified:
                        if last_active is None or s.last_modified > last_active:
                            last_active = s.last_modified
            except Exception:
                pass

        total_sessions += session_count
        total_storage += storage_bytes
        user_stats.append(UserStatsEntry(
            username=u.username,
            role=u.role,
            session_count=session_count,
            storage_bytes=storage_bytes,
            last_active=last_active,
        ))

    user_stats.sort(key=lambda x: x.session_count, reverse=True)

    return AdminStatsResponse(
        total_users=len(users),
        total_sessions=total_sessions,
        total_storage_bytes=total_storage,
        users=user_stats,
    )


@router.get("/fleet", response_model=FleetResponse)
async def get_fleet():
    """Live fleet snapshot for the admin dashboard.

    Lists the AgentTenant CRs (operator-written phase / readyReplicas / podIP),
    then fans out to each *awake* pod's /health to read its in-flight ``active_runs``
    (summed = "running sessions"). The probe is concurrent and fail-open, so one
    unreachable pod degrades to active_runs=None rather than failing the snapshot.
    """
    from ..provisioner import list_tenants, probe_health

    settings = get_settings()
    port = settings.kubernetes.runner_service_port

    items = await asyncio.to_thread(list_tenants)

    entries: list[dict] = []
    awake_targets: list[tuple[int, str]] = []  # (entry index, pod_ip)
    for it in items:
        spec = it.get("spec") or {}
        status = it.get("status") or {}
        meta = it.get("metadata") or {}
        account_id = spec.get("accountId") or meta.get("name") or ""
        phase = status.get("phase") or "Zero"
        ready = int(status.get("readyReplicas") or 0)
        pod_ip = status.get("podIP")
        awake = ready > 0 and phase == "Running" and bool(pod_ip)
        entries.append({
            "account_id": account_id,
            "username": spec.get("username"),
            "phase": phase,
            "awake": awake,
            "ready_replicas": ready,
            "active_runs": None,
            "last_activity_ts": None,
            "pod_ip": pod_ip,
        })
        if awake:
            awake_targets.append((len(entries) - 1, pod_ip))

    # Concurrent, fail-open /health fan-out to the awake pods only.
    if awake_targets:
        healths = await asyncio.gather(*(probe_health(ip, port) for _, ip in awake_targets))
        for (idx, _), health in zip(awake_targets, healths):
            if health:
                entries[idx]["active_runs"] = int(health.get("active_runs") or 0)
                entries[idx]["last_activity_ts"] = health.get("last_activity_ts")

    awake_count = sum(1 for e in entries if e["awake"])
    running = sum((e["active_runs"] or 0) for e in entries)

    # Awake first, then busiest, then by account for a stable order.
    entries.sort(key=lambda e: (not e["awake"], -(e["active_runs"] or 0), e["account_id"]))

    return FleetResponse(
        total_accounts=len(entries),
        awake_sandboxes=awake_count,
        running_sessions=running,
        accounts=[FleetAccountEntry(**e) for e in entries],
    )


@router.get("/audit", response_model=AuditLogResponse)
async def get_audit_log(
    limit: int = Query(default=50, ge=1, le=200),
    before: str | None = Query(default=None),
    after: str | None = Query(default=None),
    action: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    target: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
):
    from datetime import datetime

    start_time = datetime.fromisoformat(start) if start else None
    end_time = datetime.fromisoformat(end) if end else None

    audit = get_audit_logger()
    entries, next_cursor, prev_cursor, total = await asyncio.to_thread(
        audit.query_cursor,
        limit=limit,
        before=before,
        after=after,
        action_filter=action,
        actor_filter=actor,
        target_filter=target,
        start_time=start_time,
        end_time=end_time,
        session_id_filter=session_id,
    )
    return AuditLogResponse(
        entries=[
            AuditEntryResponse(
                id=e.id,
                timestamp=e.timestamp,
                actor=e.actor,
                action=e.action,
                target=e.target,
                details=e.details,
            )
            for e in entries
        ],
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
        total=total,
        limit=limit,
    )


# --- Admin scheduler endpoints ---

@router.get("/scheduler/jobs")
async def admin_list_scheduler_jobs():
    """Deferred (Phase 4): scheduler is not part of this deployment yet."""
    raise HTTPException(503, "Scheduler is unavailable in this deployment")


@router.get("/scheduler/running")
async def admin_list_running_tasks():
    """Deferred (Phase 4): scheduler is not part of this deployment yet."""
    raise HTTPException(503, "Scheduler is unavailable in this deployment")


# --- Admin per-user skills endpoints ---

@router.get("/users/{username}/skills", response_model=SkillListResponse)
async def get_user_skills(username: str):
    """Moved to agent-runner: use the proxied /api/resource/skills routes."""
    raise HTTPException(503, "Per-user skill management is served by agent-runner")


@router.delete("/users/{username}/skills/{level}/{name}")
async def delete_user_skill(
    username: str,
    level: SkillLevel,
    name: str,
    current_user: UserRecord = Depends(require_admin),
):
    """Moved to agent-runner: use the proxied /api/resource/skills routes."""
    raise HTTPException(503, "Per-user skill management is served by agent-runner")


# --- Admin per-user MCP endpoints ---

@router.get("/users/{username}/mcp", response_model=McpServerListResponse)
async def get_user_mcp_servers(username: str):
    """Moved to agent-runner: per-user MCP config lives in the per-account pod."""
    raise HTTPException(503, "Per-user MCP management is served by agent-runner")


@router.delete("/users/{username}/mcp/{level}/{name}")
async def delete_user_mcp_server(
    username: str,
    level: McpLevel,
    name: str,
    current_user: UserRecord = Depends(require_admin),
):
    """Moved to agent-runner: per-user MCP config lives in the per-account pod."""
    raise HTTPException(503, "Per-user MCP management is served by agent-runner")


# --- Admin per-user scheduler endpoints ---


@router.get("/users/{username}/scheduler/jobs")
async def get_user_scheduler_jobs(username: str):
    """Deferred (Phase 4): scheduler is not part of this deployment yet."""
    raise HTTPException(503, "Scheduler is unavailable in this deployment")


# --- Admin per-user hooks endpoints ---


@router.get("/users/{username}/hooks/active")
async def get_user_active_hooks(username: str):
    """Moved to agent-runner: per-user hook config lives in the per-account pod."""
    raise HTTPException(503, "Per-user hook management is served by agent-runner")


@router.get("/presetprompt", response_model=PresetPromptResponse)
async def get_preset_prompt():
    store = get_user_store()
    runtime = store.get_runtime_config()
    cfg = runtime.get("append_systemprompt", {})
    return PresetPromptResponse(enable=cfg.get("enable", False), content=cfg.get("content"))


@router.put("/presetprompt", response_model=PresetPromptResponse)
async def update_preset_prompt(
    request: PresetPromptUpdate,
    current_user: UserRecord = Depends(require_admin),
):
    store = get_user_store()
    store.update_runtime_config("append_systemprompt", {
        "enable": request.enable,
        "content": request.content,
    })
    audit = get_audit_logger()
    audit.append(AuditEntry(
        actor=current_user.username,
        action="runtime.presetprompt_updated",
        target="append_systemprompt",
        details={"enable": request.enable, "content_length": len(request.content or "")},
    ))
    return PresetPromptResponse(enable=request.enable, content=request.content)


@router.get("/clipath", response_model=CliPathResponse)
async def get_cli_path():
    store = get_user_store()
    runtime = store.get_runtime_config()
    return CliPathResponse(cli_path=runtime.get("cli_path"))


@router.put("/clipath", response_model=CliPathResponse)
async def update_cli_path(
    request: CliPathUpdate,
    current_user: UserRecord = Depends(require_admin),
):
    store = get_user_store()
    store.update_runtime_config("cli_path", request.cli_path)
    audit = get_audit_logger()
    audit.append(AuditEntry(
        actor=current_user.username,
        action="runtime.cli_path_updated",
        target="cli_path",
        details={"cli_path": request.cli_path},
    ))
    return CliPathResponse(cli_path=request.cli_path)


@router.get("/history-retention", response_model=HistoryRetentionResponse)
async def get_history_retention():
    store = get_user_store()
    runtime = store.get_runtime_config()
    days = runtime.get("history_retention_days", 7)
    return HistoryRetentionResponse(history_retention_days=days)


@router.put("/history-retention", response_model=HistoryRetentionResponse)
async def update_history_retention(
    request: HistoryRetentionUpdate,
    current_user: UserRecord = Depends(require_admin),
):
    store = get_user_store()
    store.update_runtime_config("history_retention_days", request.history_retention_days)
    audit = get_audit_logger()
    audit.append(AuditEntry(
        actor=current_user.username,
        action="runtime.history_retention_updated",
        target="history_retention_days",
        details={"history_retention_days": request.history_retention_days},
    ))
    return HistoryRetentionResponse(history_retention_days=request.history_retention_days)


@router.get("/retryable-tools", response_model=RetryableToolsResponse)
async def get_retryable_tools():
    store = get_user_store()
    runtime = store.get_runtime_config()
    raw_tools = runtime.get("retryable_tools", [])
    tools = [RetryableToolEntry(**t) if isinstance(t, dict) else t for t in raw_tools]
    raw_wecom = runtime.get("retry_callback_wecom")
    wecom_cfg = RetryCallbackWeComConfig(**raw_wecom) if isinstance(raw_wecom, dict) else None
    return RetryableToolsResponse(
        retryable_tools=tools,
        retry_callback_type=runtime.get("retry_callback_type", "none"),
        retry_callback_script=runtime.get("retry_callback_script"),
        retry_callback_wecom=wecom_cfg,
    )


@router.put("/retryable-tools", response_model=RetryableToolsResponse)
async def update_retryable_tools(
    request: RetryableToolsUpdate,
    current_user: UserRecord = Depends(require_admin),
):
    store = get_user_store()
    store.update_runtime_config(
        "retryable_tools",
        [t.model_dump() for t in request.retryable_tools],
    )
    store.update_runtime_config("retry_callback_type", request.retry_callback_type)
    store.update_runtime_config("retry_callback_script", request.retry_callback_script)
    store.update_runtime_config(
        "retry_callback_wecom",
        request.retry_callback_wecom.model_dump() if request.retry_callback_wecom else None,
    )
    audit = get_audit_logger()
    audit.append(AuditEntry(
        actor=current_user.username,
        action="runtime.retryable_tools_updated",
        target="retryable_tools",
        details={
            "tool_count": len(request.retryable_tools),
            "callback_type": request.retry_callback_type,
        },
    ))
    return RetryableToolsResponse(
        retryable_tools=request.retryable_tools,
        retry_callback_type=request.retry_callback_type,
        retry_callback_script=request.retry_callback_script,
        retry_callback_wecom=request.retry_callback_wecom,
    )


@router.get("/risky-tools", response_model=RiskyToolsResponse)
async def get_risky_tools():
    """List the admin-configured risky-tool patterns that force user
    approval even in bypassPermissions mode."""
    store = get_user_store()
    runtime = store.get_runtime_config()
    return RiskyToolsResponse(
        risky_tool_list=list(runtime.get("risky_tool_list") or [])
    )


@router.put("/risky-tools", response_model=RiskyToolsResponse)
async def update_risky_tools(
    request: RiskyToolsUpdate,
    current_user: UserRecord = Depends(require_admin),
):
    """Update the risky-tool patterns. Each pattern must be a valid
    Claude Code native permission-grammar string (e.g. 'Bash(rm:*)',
    'Write(/etc/**)', 'WebFetch(domain:github.com)', 'mcp__*__delete_*').
    Malformed patterns are rejected with HTTP 422."""
    from priva_common.risky_matcher import parse_rule_strict

    # Validate all patterns before committing any change.
    for raw in request.risky_tool_list:
        try:
            parse_rule_strict(raw)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    store = get_user_store()
    store.update_runtime_config("risky_tool_list", list(request.risky_tool_list))

    audit = get_audit_logger()
    audit.append(AuditEntry(
        actor=current_user.username,
        action="runtime.risky_tool_list_updated",
        target="risky_tool_list",
        details={"count": len(request.risky_tool_list)},
    ))
    return RiskyToolsResponse(risky_tool_list=list(request.risky_tool_list))


@router.get("/sensitive-patterns", response_model=SensitivePatternsResponse)
async def get_sensitive_patterns():
    """Return the PII masking config: ``enable`` toggle + ``patterns`` list."""
    store = get_user_store()
    cfg = store.get_runtime_config().get("pii_masking") or {}
    raw = cfg.get("patterns") or []
    patterns = [SensitivePatternEntry(**p) if isinstance(p, dict) else p for p in raw]
    return SensitivePatternsResponse(enable=bool(cfg.get("enable", False)), patterns=patterns)


@router.put("/sensitive-patterns", response_model=SensitivePatternsResponse)
async def update_sensitive_patterns(
    request: SensitivePatternsUpdate,
    current_user: UserRecord = Depends(require_admin),
):
    """Update PII masking config. Each pattern must compile as a regex.

    ``enable=True`` activates the PostToolUse hook that replaces matched
    substrings before the model sees tool output. The outbound SSE mask path
    runs whenever patterns are non-empty regardless of this flag."""
    from priva_common.sensitive_mask import parse_pattern_strict

    for entry in request.patterns:
        try:
            parse_pattern_strict(entry.model_dump())
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    store = get_user_store()
    store.update_runtime_config(
        "pii_masking",
        {
            "enable": bool(request.enable),
            "patterns": [p.model_dump() for p in request.patterns],
        },
    )

    audit = get_audit_logger()
    audit.append(AuditEntry(
        actor=current_user.username,
        action="runtime.pii_masking_updated",
        target="pii_masking",
        details={"enable": bool(request.enable), "count": len(request.patterns)},
    ))
    return SensitivePatternsResponse(
        enable=bool(request.enable),
        patterns=list(request.patterns),
    )


# --- Plugin management endpoints ---

@router.get("/system/plugin", response_model=PluginListResponse)
async def list_plugins():
    """Deferred (Phase 4): the plugin manager is not part of this deployment yet."""
    raise HTTPException(503, "Plugin management is unavailable in this deployment")


@router.put("/system/plugin/{plugin_id}", response_model=PluginInfo)
async def update_plugin(
    plugin_id: str,
    request: PluginConfigUpdate,
    current_user: UserRecord = Depends(require_admin),
):
    """Deferred (Phase 4): the plugin manager is not part of this deployment yet."""
    raise HTTPException(503, "Plugin management is unavailable in this deployment")
