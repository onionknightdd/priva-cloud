from __future__ import annotations

import asyncio
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from priva_common.models.admin import AuditEntryResponse, AuditLogResponse
from priva_common.models.auth import (
    ApiKeyResponse,
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    RegisterResponse,
    SetupRequest,
    SetupStatus,
    UserPublic,
)
from priva_common.models.feishu import (
    FeishuConfigResponse,
    FeishuLinkCodeResponse,
    FeishuSessionEntry,
    FeishuSessionsResponse,
    FeishuUserConfigUpdate,
)
from ..services.feishu_connector import nudge_reconcile
from ..services.auth import (
    assert_account_active,
    create_jwt,
    client_ip,
    rate_limiter,
    require_user,
    user_record_to_public,
)
from priva_common.audit_log import AuditEntry, get_audit_logger
from priva_common.config import get_settings
from priva_common.user_store import get_user_store, UserRecord

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/setup", response_model=SetupStatus)
async def check_setup():
    store = get_user_store()
    return SetupStatus(needs_setup=not store.has_users())


def _provision_tenant(user) -> None:
    """Best-effort: create the account's AgentTenant CR. Never block user creation
    (a local CP without a cluster, or a transient kube error, must still succeed)."""
    from ..provisioner import ensure_tenant
    try:
        ensure_tenant(user.account_id, user.username)
    except Exception as exc:  # pragma: no cover
        from priva_common.logging import get_app_logger
        get_app_logger(__name__).warning("provision tenant failed for {}: {}", user.username, exc)


@router.post("/setup", response_model=LoginResponse)
async def setup_admin(request: SetupRequest):
    store = get_user_store()
    if store.has_users():
        raise HTTPException(403, "Setup already completed")
    user = store.create_user(request.username, request.password, role="admin")
    token = create_jwt(user.username, user.role, user)

    # Provision the per-account agent-runner (operator reconciles the AgentTenant CR).
    _provision_tenant(user)

    # Seed creds (if provided) into the new account's agent-runner settings.json, by
    # waking its pod (provisioned just above) and PUTting through the runner token.
    # Best-effort: a slow cold pod must not fail setup — creds can be re-entered in
    # the SPA. (await: the pod must exist before the write, so order matters.)
    if request.env:
        env_dict = request.env.model_dump(exclude_none=True)
        if env_dict:
            try:
                from ..provisioner import push_account_credentials
                await push_account_credentials(user.account_id, user.username, env_dict)
            except Exception as exc:  # pragma: no cover
                from priva_common.logging import get_app_logger
                get_app_logger(__name__).warning(
                    "setup: push creds failed for {}: {}", user.username, exc)

    public = user_record_to_public(user)
    return LoginResponse(access_token=token, user=public)


@router.post("/register", response_model=RegisterResponse)
async def register(request: RegisterRequest, http_request: Request):
    """Public self-registration. Stores a pending request (bcrypt password hash +
    requested runner type / resource spec) for an admin to approve. No account is
    created until approval."""
    import bcrypt

    from priva_common.dataplane import get_client

    # Unauthenticated and it runs cost-12 bcrypt plus a DB insert per call, so
    # rate-limit it by source address before doing any of that work.
    ip = client_ip(http_request)
    rate_limiter.check(request.username, ip)
    rate_limiter.record_failure(request.username, ip)

    if request.runner_type not in ("auto_scale", "persistent"):
        raise HTTPException(400, "Invalid runner_type")

    store = get_user_store()
    if store.get_user(request.username) is not None:
        raise HTTPException(409, "Username already taken")

    client = get_client()
    if client.registrations.get_open_by_username(request.username) is not None:
        raise HTTPException(409, "A registration request for this username is already pending")

    password_hash = bcrypt.hashpw(request.password.encode(), bcrypt.gensalt()).decode()
    rec = client.registrations.create(
        username=request.username,
        password_hash=password_hash,
        display_name=request.display_name,
        runner_type=request.runner_type,
        cpu_cores=request.cpu_cores,
        memory_mb=request.memory_mb,
        volume_gb=request.volume_gb,
        note=request.note,
    )

    audit = get_audit_logger()
    audit.append(AuditEntry(
        actor=request.username,
        action="registration.requested",
        target=request.username,
        details={"runner_type": request.runner_type, "request_id": rec.request_id},
    ))
    return RegisterResponse(status="pending_approval", request_id=rec.request_id)


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest, http_request: Request):
    store = get_user_store()
    settings = get_settings()

    ip = client_ip(http_request)
    rate_limiter.check(request.username, ip)

    if not store.verify_password(request.username, request.password):
        rate_limiter.record_failure(request.username, ip)
        audit = get_audit_logger()
        audit.append(AuditEntry(
            actor=request.username,
            action="login.failed",
            target=request.username,
        ))
        raise HTTPException(401, "Invalid username or password")

    rate_limiter.reset(request.username, ip)
    user = store.get_user(request.username)

    # Right password, frozen account: record WHY before refusing, so the audit trail
    # separates a lifecycle rejection from a bad-credential one.
    if user.status != "active":
        audit = get_audit_logger()
        audit.append(AuditEntry(
            actor=request.username,
            action="login.failed",
            target=request.username,
            details={"status": user.status},
        ))
    assert_account_active(user)

    # Determine effective role
    role = user.role
    if user.username in settings.auth.admins and role != "admin":
        role = "admin"

    # password_hash rides along so the token carries its `pwd` epoch: a later
    # password change invalidates this session (services/auth.py).
    token = create_jwt(user.username, role, user)
    public = user_record_to_public(user)
    if role != user.role:
        public.role = role

    audit = get_audit_logger()
    audit.append(AuditEntry(
        actor=user.username,
        action="login.success",
        target=user.username,
    ))

    return LoginResponse(access_token=token, user=public)


@router.post("/refresh", response_model=LoginResponse)
async def refresh_token(user: UserRecord = Depends(require_user)):
    settings = get_settings()
    role = user.role
    if user.username in settings.auth.admins and role != "admin":
        role = "admin"
    token = create_jwt(user.username, role, user)
    public = user_record_to_public(user)
    if role != user.role:
        public.role = role
    return LoginResponse(access_token=token, user=public)


@router.get("/me", response_model=UserPublic)
async def get_me(user: UserRecord = Depends(require_user)):
    settings = get_settings()
    public = user_record_to_public(user)
    if user.username in settings.auth.admins:
        public.role = "admin"

    # Usage stats AND the workspace path are agent-runtime state (derived from the
    # per-account /workspace PVC) that the control-panel cannot see. The SPA gets
    # them from the agent-runner (GET /api/user/overview, GET /api/user/stats), so
    # /me no longer sets public.workspace. /me stays control-plane only: identity,
    # role, api-key presence.
    return public


@router.get("/audit", response_model=AuditLogResponse)
async def get_control_plane_audit(
    user: UserRecord = Depends(require_user),
    limit: int = Query(default=50, ge=1, le=200),
    before: str | None = Query(default=None),
    after: str | None = Query(default=None),
    action: str | None = Query(default=None),
    target: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
):
    """Control-plane audit for the caller: the login/auth/user-mgmt events the
    control-panel itself emits into its own store. Agent-runtime audit (runs,
    skills, tools, hooks, sessions) lives on the per-account PVC and is served by
    the agent-runner at GET /api/user/audit; the SPA merges the two feeds by
    timestamp client-side so no history is lost."""
    from datetime import datetime as _dt

    start_time = _dt.fromisoformat(start) if start else None
    end_time = _dt.fromisoformat(end) if end else None

    audit = get_audit_logger()
    entries, next_cursor, prev_cursor, total = await asyncio.to_thread(
        audit.query_cursor,
        limit=limit,
        before=before,
        after=after,
        action_filter=action,
        actor_filter=user.username,
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


@router.get("/me/apikey", response_model=ApiKeyResponse)
async def get_my_apikey(user: UserRecord = Depends(require_user)):
    return ApiKeyResponse(has_key=bool(user.api_key), api_key=user.api_key)


@router.post("/me/apikey", response_model=ApiKeyResponse)
async def generate_my_apikey(user: UserRecord = Depends(require_user)):
    store = get_user_store()
    new_key = "sk-" + secrets.token_hex(24)
    store.update_user(user.username, api_key=new_key)

    audit = get_audit_logger()
    audit.append(AuditEntry(
        actor=user.username,
        action="self.apikey_generated",
        target=user.username,
    ))

    return ApiKeyResponse(has_key=True, api_key=new_key)


@router.delete("/me/apikey", response_model=ApiKeyResponse)
async def revoke_my_apikey(user: UserRecord = Depends(require_user)):
    store = get_user_store()
    store.update_user(user.username, api_key=None)

    audit = get_audit_logger()
    audit.append(AuditEntry(
        actor=user.username,
        action="self.apikey_revoked",
        target=user.username,
    ))

    return ApiKeyResponse(has_key=False)


_FEISHU_ACCESS_MODES = ("owner_only", "allowlist", "all")
_FEISHU_DOMAINS = ("feishu", "lark")


def _group_globally_disabled(client) -> bool:
    """The admin global group-chat switch, folded into the user's read view so the
    SPA can grey out the toggle. Fail-soft: an unreachable singleton reads as off."""
    try:
        return bool(client.channel_platform.get().group_chat_disabled)
    except Exception:
        return False


@router.get("/me/feishu-config", response_model=FeishuConfigResponse)
async def get_my_feishu_config(user: UserRecord = Depends(require_user)):
    from priva_common.dataplane import get_client
    client = get_client()
    rec = client.feishu_configs.get(user.account_id)
    return FeishuConfigResponse.from_record(
        rec, user.account_id, group_chat_globally_disabled=_group_globally_disabled(client))


@router.put("/me/feishu-config", response_model=FeishuConfigResponse)
async def update_my_feishu_config(
    request: FeishuUserConfigUpdate,
    user: UserRecord = Depends(require_user),
):
    """User self-serve: the ONLY writer of app_id/app_secret + user_enabled + behaviour.
    admin_disabled is not in this DTO, so a user can never lift an admin kill-switch."""
    from priva_common.dataplane import get_client

    if request.single_chat_access_mode is not None and request.single_chat_access_mode not in _FEISHU_ACCESS_MODES:
        raise HTTPException(400, "Invalid single_chat_access_mode")
    if request.domain is not None and request.domain not in _FEISHU_DOMAINS:
        raise HTTPException(400, "Invalid domain")

    audit = get_audit_logger()
    kwargs: dict = {}
    if request.app_id is not None:
        kwargs["app_id"] = request.app_id.strip()
    if request.app_secret is not None:
        if request.app_secret == "__clear__":
            kwargs["app_secret"] = ""  # data-plane clears app_secret_enc
            audit.append(AuditEntry(actor=user.username, action="self.feishu_secret_cleared",
                                    target=user.username, details={"secret_set": False}))
        elif request.app_secret == "":
            raise HTTPException(400, "app_secret cannot be empty; send '__clear__' to remove it")
        else:
            kwargs["app_secret"] = request.app_secret  # encrypted at data-spine; never logged
            audit.append(AuditEntry(actor=user.username, action="self.feishu_secret_set",
                                    target=user.username, details={"secret_set": True}))
    if request.user_enabled is not None:
        kwargs["user_enabled"] = request.user_enabled
    for f in ("single_chat_access_mode", "allowed_union_ids", "welcome_message", "reject_message",
              "model", "max_queue_size", "enable_permission_feedback", "feedback_timeout_seconds",
              "domain", "group_chat_enabled"):
        v = getattr(request, f)
        if v is not None:
            kwargs[f] = v

    client = get_client()
    rec = client.feishu_configs.set_user(user.account_id, updated_by=user.username, **kwargs)
    await nudge_reconcile(user.account_id, user.username)  # best-effort; poll is the backstop
    return FeishuConfigResponse.from_record(
        rec, user.account_id, group_chat_globally_disabled=_group_globally_disabled(client))


@router.get("/me/feishu-sessions", response_model=FeishuSessionsResponse)
async def list_my_feishu_sessions(user: UserRecord = Depends(require_user)):
    """Every chat the bot has been talked to in (per-chat channel_binding rows),
    active sessions first, most recent first. Display metadata (chat_type/chat_name)
    is stamped by the connector from live chat context."""
    from priva_common.dataplane import get_client
    bindings = get_client().bindings.list_bindings(user.account_id)
    entries = [
        FeishuSessionEntry(
            chat_id=b.feishu_chat_id or "",
            chat_type=b.chat_type or "",
            chat_name=b.chat_name or "",
            session_id=b.session_uuid,
            updated_at=b.rebound_at or b.bound_at,
        )
        for b in bindings
    ]
    # Two stable passes: most-recent first, then active (has session) ahead of reset.
    entries.sort(key=lambda e: e.updated_at or "", reverse=True)
    entries.sort(key=lambda e: e.session_id is None)
    return FeishuSessionsResponse(sessions=entries)


@router.post("/me/feishu-link-code", response_model=FeishuLinkCodeResponse)
async def create_my_feishu_link_code(user: UserRecord = Depends(require_user)):
    """Mint a single-use owner-binding code (feat_feishu_DM.md §4.1). The plaintext
    exists only in this response; overwrites any previous pending code. No reconcile
    nudge — minting a code must not bounce the WS (link cols are not in the digest)."""
    from priva_common.dataplane import get_client
    code, expires = get_client().feishu_configs.create_link_code(user.account_id)
    get_audit_logger().append(AuditEntry(
        actor=user.username, action="self.feishu_link_code_created",
        target=user.username, details={"expires_at": expires}))  # never the code itself
    return FeishuLinkCodeResponse(code=code, expires_at=expires)


@router.delete("/me/feishu-owner", response_model=FeishuConfigResponse)
async def unbind_my_feishu_owner(user: UserRecord = Depends(require_user)):
    """Drop the owner binding (gate falls back to allow-all) and discard any pending
    code. Digest changes → connector re-arms with the ownerless cfg."""
    from priva_common.dataplane import get_client
    rec = get_client().feishu_configs.unbind_owner(user.account_id, updated_by=user.username)
    get_audit_logger().append(AuditEntry(
        actor=user.username, action="self.feishu_owner_unbound", target=user.username, details={}))
    await nudge_reconcile(user.account_id, user.username)
    return FeishuConfigResponse.from_record(rec, user.account_id)


@router.put("/me/password")
async def change_my_password(
    request: ChangePasswordRequest,
    user: UserRecord = Depends(require_user),
):
    store = get_user_store()
    if not store.verify_password(user.username, request.current_password):
        raise HTTPException(401, "Current password is incorrect")
    store.update_user(user.username, password=request.new_password)

    audit = get_audit_logger()
    audit.append(AuditEntry(
        actor=user.username,
        action="self.password_changed",
        target=user.username,
    ))

    # The password epoch just changed, so the caller's own token no longer
    # verifies. Hand back a fresh one — otherwise the user sees a success
    # response and is silently 401'd out on their very next request.
    settings = get_settings()
    refreshed = get_user_store().get_user(user.username)
    role = (refreshed or user).role
    if user.username in settings.auth.admins and role != "admin":
        role = "admin"
    return {"status": "ok",
            "access_token": create_jwt(user.username, role, refreshed or user)}

# NOTE: the per-account BYOK creds endpoints (GET/PUT /me/env, /me/env/status) and
# the model-list proxy moved OFF the control-panel. Creds now live in the account's
# agent-runner settings.json — the SPA reads/writes them through agentgateway at
# /api/sandbox/credentials* (served by routers/credentials.py on the pod). The
# control-panel no longer touches ANTHROPIC_* config.
