from __future__ import annotations

import asyncio
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query

from priva_common.models.admin import (
    AuditEntryResponse,
    AuditLogResponse,
    CliPathResponse,
    CliPathUpdate,
    ClusterCapacityMetric,
    ClusterCapacityResponse,
    FleetAccountEntry,
    FleetResponse,
    GatewayMetricsResponse,
    HealthDep,
    HistoryRetentionResponse,
    HistoryRetentionUpdate,
    PendingRegistrationResponse,
    PresetPromptResponse,
    PresetPromptUpdate,
    ResourceUsageAccountEntry,
    ResourceUsageResponse,
    RunnerDefaultsResponse,
    RunnerDefaultsUpdate,
    RetryableToolEntry,
    RetryCallbackWeComConfig,
    RetryableToolsResponse,
    RetryableToolsUpdate,
    SensitivePatternEntry,
    SensitivePatternsResponse,
    SensitivePatternsUpdate,
    ReplicaCount,
    SystemEdge,
    SystemHealthResponse,
    SystemNode,
)
from priva_common.models.auth import UserCreate, UserPublic, UserUpdate
from priva_common.models.feishu import (
    ChannelPlatformConfigResponse,
    ChannelPlatformConfigUpdate,
    FeishuAdminConfigUpdate,
    FeishuConfigResponse,
)
from priva_common.audit_log import AuditEntry, get_audit_logger
from ..services.auth import require_admin, user_record_to_public
from ..services.feishu_connector import nudge_reconcile
from priva_common.config import get_settings
from priva_common.logging import get_app_logger
from priva_common.user_store import UserRecord, get_user_store

logger = get_app_logger(__name__)

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


# --- Agent Runner Sandbox: global runner defaults --------------------------------
# Platform-wide defaults every account inherits unless it carries a per-account CR
# override. Stored in data-spine (runner_defaults), then materialized as a complete
# runtimeDefaults snapshot on each AgentTenant CR. Running pods still apply resource
# changes at their safe zero boundary. CPU crosses the API as millicores for the UI.

def _runner_defaults_response(d) -> RunnerDefaultsResponse:
    total_cpu_m = int(round(d.cpu_cores * 1000))
    percent = int(d.terminal_resource_percent)
    terminal_cpu_m = 0
    terminal_memory_mb = 0
    if percent > 0:
        terminal_cpu_m = min(total_cpu_m - 1, max(1, total_cpu_m * percent // 100))
        terminal_memory_mb = min(d.memory_mb - 1, max(1, d.memory_mb * percent // 100))
    return RunnerDefaultsResponse(
        idle_grace_seconds=d.idle_grace_seconds,
        min_alive_after_wake_seconds=d.min_alive_after_wake_seconds,
        cpu_millicores=int(round(d.cpu_cores * 1000)),
        memory_mb=d.memory_mb,
        storage_gb=d.storage_gb,
        terminal_resource_percent=d.terminal_resource_percent,
        terminal_max_sessions=d.terminal_max_sessions,
        terminal_idle_timeout_seconds=d.terminal_idle_timeout_seconds,
        terminal_max_lifetime_seconds=d.terminal_max_lifetime_seconds,
        terminal_scale_down_grace_seconds=d.terminal_scale_down_grace_seconds,
        terminal_enabled=d.terminal_resource_percent > 0,
        runner_cpu_millicores=total_cpu_m - terminal_cpu_m,
        terminal_cpu_millicores=terminal_cpu_m,
        runner_memory_mb=d.memory_mb - terminal_memory_mb,
        terminal_memory_mb=terminal_memory_mb,
        updated_at=d.updated_at,
    )


@router.get("/runner-defaults", response_model=RunnerDefaultsResponse)
async def get_runner_defaults():
    from priva_common.dataplane import get_client
    return _runner_defaults_response(get_client().runner_defaults.get())


@router.put("/runner-defaults", response_model=RunnerDefaultsResponse)
async def update_runner_defaults(
    request: RunnerDefaultsUpdate,
    current_user: UserRecord = Depends(require_admin),
):
    from priva_common.dataplane import get_client

    kw: dict = {}
    if request.idle_grace_seconds is not None:
        if request.idle_grace_seconds < 0:
            raise HTTPException(400, "idle_grace_seconds must be >= 0")
        kw["idle_grace_seconds"] = int(request.idle_grace_seconds)
    if request.min_alive_after_wake_seconds is not None:
        if request.min_alive_after_wake_seconds < 0:
            raise HTTPException(400, "min_alive_after_wake_seconds must be >= 0")
        kw["min_alive_after_wake_seconds"] = int(request.min_alive_after_wake_seconds)
    if request.cpu_millicores is not None:
        if request.cpu_millicores <= 0:
            raise HTTPException(400, "cpu_millicores must be > 0")
        kw["cpu_cores"] = request.cpu_millicores / 1000.0
    if request.memory_mb is not None:
        if request.memory_mb <= 0:
            raise HTTPException(400, "memory_mb must be > 0")
        kw["memory_mb"] = int(request.memory_mb)
    if request.storage_gb is not None:
        if request.storage_gb <= 0:
            raise HTTPException(400, "storage_gb must be > 0")
        kw["storage_gb"] = int(request.storage_gb)
    if request.terminal_resource_percent is not None:
        percent = int(request.terminal_resource_percent)
        if percent < 0 or percent > 50 or percent % 5:
            raise HTTPException(400, "terminal_resource_percent must be 0..50 in 5% steps")
        kw["terminal_resource_percent"] = percent
    if request.terminal_max_sessions is not None:
        if request.terminal_max_sessions < 1 or request.terminal_max_sessions > 20:
            raise HTTPException(400, "terminal_max_sessions must be 1..20")
        kw["terminal_max_sessions"] = int(request.terminal_max_sessions)
    if request.terminal_idle_timeout_seconds is not None:
        if request.terminal_idle_timeout_seconds < 60 or request.terminal_idle_timeout_seconds > 86400:
            raise HTTPException(400, "terminal_idle_timeout_seconds must be 60..86400")
        kw["terminal_idle_timeout_seconds"] = int(request.terminal_idle_timeout_seconds)
    if request.terminal_max_lifetime_seconds is not None:
        if request.terminal_max_lifetime_seconds < 60 or request.terminal_max_lifetime_seconds > 86400:
            raise HTTPException(400, "terminal_max_lifetime_seconds must be 60..86400")
        kw["terminal_max_lifetime_seconds"] = int(request.terminal_max_lifetime_seconds)
    if request.terminal_scale_down_grace_seconds is not None:
        if request.terminal_scale_down_grace_seconds < 0 or request.terminal_scale_down_grace_seconds > 3600:
            raise HTTPException(400, "terminal_scale_down_grace_seconds must be 0..3600")
        kw["terminal_scale_down_grace_seconds"] = int(request.terminal_scale_down_grace_seconds)

    client = get_client()
    if not kw:
        return _runner_defaults_response(client.runner_defaults.get())
    current = client.runner_defaults.get()
    candidate_percent = int(kw.get("terminal_resource_percent", current.terminal_resource_percent))
    candidate_cpu_m = int(round(float(kw.get("cpu_cores", current.cpu_cores)) * 1000))
    candidate_memory_mb = int(kw.get("memory_mb", current.memory_mb))
    if candidate_percent > 0 and (candidate_cpu_m < 2 or candidate_memory_mb < 2):
        raise HTTPException(
            400, "enabled Terminal requires at least 2 millicores CPU and 2Mi memory")
    d = client.runner_defaults.set(**kw)
    # Materialize the resolved policy into every AgentTenant CR once. The operator then
    # consumes CR-local desired state instead of N per-tenant data-spine reads per timer.
    from ..provisioner import sync_all_tenants
    try:
        await asyncio.to_thread(sync_all_tenants, defaults=d)
    except Exception as exc:
        # The periodic account sync is the retry backstop. The defaults write itself is
        # already committed, so surface drift in logs without returning a false failure.
        logger.warning("runner-default AgentTenant propagation deferred: {}", exc)
    # Capability discovery is cached because every Agent UI polls it. Make this
    # replica reflect an admin policy edit immediately; other replicas expire within
    # the short TTL in routers.terminal.
    from .terminal import clear_terminal_capability_cache
    clear_terminal_capability_cache()
    get_audit_logger().append(AuditEntry(
        actor=current_user.username,
        action="admin.runner_defaults_changed",
        target="runner_defaults",
        details=kw,
    ))
    return _runner_defaults_response(d)


def _user_public_with_spec(user: UserRecord) -> UserPublic:
    """One account's row for the admin users table: identity + the EFFECTIVE resource
    spec (per-account override, else the inherited global defaults) — the same join
    ``list_users`` does in batch."""
    from priva_common.dataplane import get_client

    pub = user_record_to_public(user)
    spec = get_client().resource_specs.get(user.account_id)
    if spec is not None:
        pub.cpu_cores, pub.memory_mb, pub.volume_gb = spec.cpu_cores, spec.memory_mb, spec.volume_gb
    else:  # inheriting the global defaults — report the effective values
        rd = get_client().runner_defaults.get()
        pub.cpu_cores, pub.memory_mb, pub.volume_gb = rd.cpu_cores, rd.memory_mb, rd.storage_gb
    return pub


def _active_admin_count(store) -> int:
    """Admins that can still log in. ``count_admins`` is status-blind, so it would let
    the last *usable* admin be frozen while disabled/purged rows pad the count."""
    return sum(1 for u in store.list_users() if u.role == "admin" and u.status == "active")


@router.get("/users", response_model=list[UserPublic])
async def list_users():
    from priva_common.dataplane import get_client

    store = get_user_store()
    client = get_client()
    # One resource_specs.list() call, mapped by account_id, so the table's RUNNER
    # column + the edit-drawer prefill don't fan out N gRPC calls.
    specs = {s.account_id: s for s in client.resource_specs.list()}
    # Accounts WITHOUT a spec row are inheriting the global defaults — show those
    # effective values (not the bare model defaults) so the table is honest.
    rd = client.runner_defaults.get()
    out: list[UserPublic] = []
    for u in store.list_users():
        pub = user_record_to_public(u)
        spec = specs.get(u.account_id)
        if spec is not None:
            pub.cpu_cores = spec.cpu_cores
            pub.memory_mb = spec.memory_mb
            pub.volume_gb = spec.volume_gb
        else:
            pub.cpu_cores = rd.cpu_cores
            pub.memory_mb = rd.memory_mb
            pub.volume_gb = rd.storage_gb
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

    # Seed creds (if provided) into the new account's agent-runner settings.json, by
    # waking its pod (provisioned just above) and PUTting through the runner token.
    # Best-effort: a slow cold pod must not fail user creation — the user can set
    # creds in the SPA.
    if request.env:
        env_dict = request.env.model_dump(exclude_none=True)
        if env_dict:
            try:
                from ..provisioner import push_account_credentials
                await push_account_credentials(user.account_id, user.username, env_dict)
            except Exception as exc:  # pragma: no cover
                logger.warning("create_user: push creds failed for {}: {}", user.username, exc)

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
        if existing.status == "active" and _active_admin_count(store) <= 1:
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

    # Write creds (if provided) into the account's agent-runner settings.json by
    # waking its pod and PUTting through the runner token. Best-effort: a slow cold
    # pod must not fail the admin edit. The account already exists (`existing`), so
    # its tenant/pod is provisioned; account_id is stable across the update.
    if request.env:
        env_dict = request.env.model_dump(exclude_none=True)
        if env_dict:
            try:
                from ..provisioner import push_account_credentials
                await push_account_credentials(existing.account_id, username, env_dict)
            except Exception as exc:  # pragma: no cover
                logger.warning("update_user: push creds failed for {}: {}", username, exc)

    # Re-run the last-admin guard on the same uninterrupted stretch as the write: the
    # creds push above awaits, so two concurrent demotions could each clear the guard
    # at the top of this handler and land a role='user' row, leaving no usable admin.
    if request.role and request.role != "admin" and existing.role == "admin":
        if existing.status == "active" and _active_admin_count(store) <= 1:
            raise HTTPException(400, "Cannot remove the last admin")

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

    return _user_public_with_spec(user)


@router.post("/users/{username}/disable", response_model=UserPublic)
async def disable_user(
    username: str,
    current_user: UserRecord = Depends(require_admin),
):
    """Freeze an account (reversible): login + gateway refuse it, the scheduler skips
    it, its Feishu worker is torn down, and the operator quiesces its Runner/Terminal
    to zero. Every byte of the account's data is preserved — ``enable`` undoes it."""
    store = get_user_store()
    if username == current_user.username:
        raise HTTPException(400, "Cannot disable your own account")

    existing = store.get_user(username)
    if existing is None:
        raise HTTPException(404, f"User '{username}' not found")

    if existing.status == "purged":
        raise HTTPException(400, "Account is being purged")

    # Last admin protection
    if existing.role == "admin" and existing.status == "active" and _active_admin_count(store) <= 1:
        raise HTTPException(400, "Cannot remove the last admin")

    try:
        user = store.update_user(username, status="disabled")
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    # The account status is authoritative; the CR patch is convergence. A kube failure
    # (or a CR that was never provisioned) must not leave the account half-frozen —
    # sync_all_tenants re-converges desiredState within its loop.
    from ..provisioner import set_tenant_desired_state
    try:
        await asyncio.to_thread(set_tenant_desired_state, user.account_id, "offboarding")
    except Exception as exc:  # pragma: no cover - kube optional locally
        logger.warning("quiesce AgentTenant failed for {}: {}", username, exc)

    audit = get_audit_logger()
    audit.append(AuditEntry(
        actor=current_user.username,
        action="user.disabled",
        target=username,
        details={"role": existing.role},
    ))

    await nudge_reconcile(user.account_id, user.username)  # drop the Feishu WS now; poll is the backstop
    return _user_public_with_spec(user)


@router.post("/users/{username}/enable", response_model=UserPublic)
async def enable_user(
    username: str,
    current_user: UserRecord = Depends(require_admin),
):
    """Thaw a disabled account: access is restored and the operator releases the
    Runner/Terminal back to the wake path. A purged account is a tombstone — its
    runtime and storage are already destroyed — and can never be brought back."""
    store = get_user_store()
    existing = store.get_user(username)
    if existing is None:
        raise HTTPException(404, f"User '{username}' not found")

    if existing.status == "purged":
        raise HTTPException(400, "Account is being purged")

    try:
        user = store.update_user(username, status="active")
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    from ..provisioner import set_tenant_desired_state
    try:
        await asyncio.to_thread(set_tenant_desired_state, user.account_id, "active")
    except Exception as exc:  # pragma: no cover - kube optional locally
        logger.warning("release AgentTenant failed for {}: {}", username, exc)

    audit = get_audit_logger()
    audit.append(AuditEntry(
        actor=current_user.username,
        action="user.enabled",
        target=username,
        details={"role": existing.role},
    ))

    await nudge_reconcile(user.account_id, user.username)
    return _user_public_with_spec(user)


@router.get("/users/{username}/feishu-config", response_model=FeishuConfigResponse)
async def get_user_feishu_config(username: str):
    """Admin read: status + credential PRESENCE only — the app_secret is never returned."""
    store = get_user_store()
    existing = store.get_user(username)
    if existing is None:
        raise HTTPException(404, f"User '{username}' not found")
    from priva_common.dataplane import get_client
    rec = get_client().feishu_configs.get(existing.account_id)
    return FeishuConfigResponse.from_record(rec, existing.account_id)


@router.put("/users/{username}/feishu-config", response_model=FeishuConfigResponse)
async def update_user_feishu_config(
    username: str,
    request: FeishuAdminConfigUpdate,
    current_user: UserRecord = Depends(require_admin),
):
    """Admin write: the kill-switch (admin_disabled) ONLY. Credentials + the user's own
    toggle are not in this DTO — the admin cannot touch them (edit right stays with the user)."""
    store = get_user_store()
    existing = store.get_user(username)
    if existing is None:
        raise HTTPException(404, f"User '{username}' not found")
    from priva_common.dataplane import get_client

    if request.admin_disabled is None:
        rec = get_client().feishu_configs.get(existing.account_id)
        return FeishuConfigResponse.from_record(rec, existing.account_id)

    rec = get_client().feishu_configs.set_admin(
        existing.account_id, admin_disabled=request.admin_disabled, updated_by=current_user.username)
    get_audit_logger().append(AuditEntry(
        actor=current_user.username,
        action="user.feishu_disabled" if request.admin_disabled else "user.feishu_enabled",
        target=username,
        details={"admin_disabled": request.admin_disabled},
    ))
    await nudge_reconcile(existing.account_id, existing.username)  # best-effort; poll is the backstop
    return FeishuConfigResponse.from_record(rec, existing.account_id)


# --- Configurations ▸ Channels: platform-wide channel settings -------------------

@router.get("/channel-platform", response_model=ChannelPlatformConfigResponse)
async def get_channel_platform_config():
    from priva_common.dataplane import get_client
    r = get_client().channel_platform.get()
    return ChannelPlatformConfigResponse(
        group_chat_disabled=r.group_chat_disabled, updated_by=r.updated_by, updated_at=r.updated_at)


@router.put("/channel-platform", response_model=ChannelPlatformConfigResponse)
async def update_channel_platform_config(
    request: ChannelPlatformConfigUpdate,
    current_user: UserRecord = Depends(require_admin),
):
    """The global group-chat kill switch (feat_feishu_DM.md §5.1). data-spine
    recomputes the desired_digest of every account whose effective_group_enabled
    flips, so the connector re-arms exactly those workers; the nudges below only
    shave the poll latency."""
    from priva_common.dataplane import get_client
    client = get_client()
    if request.group_chat_disabled is None:
        r = client.channel_platform.get()
        return ChannelPlatformConfigResponse(
            group_chat_disabled=r.group_chat_disabled, updated_by=r.updated_by, updated_at=r.updated_at)
    r = client.channel_platform.set(
        group_chat_disabled=request.group_chat_disabled, updated_by=current_user.username)
    get_audit_logger().append(AuditEntry(
        actor=current_user.username,
        action="admin.group_chat_disabled" if request.group_chat_disabled else "admin.group_chat_enabled",
        target="channel_platform",
        details={"group_chat_disabled": request.group_chat_disabled},
    ))
    for cfg in client.feishu_configs.list_effective():
        await nudge_reconcile(cfg.account_id)  # fire-and-forget per account
    return ChannelPlatformConfigResponse(
        group_chat_disabled=r.group_chat_disabled, updated_by=r.updated_by, updated_at=r.updated_at)


@router.delete("/users/{username}", status_code=202)
async def delete_user(
    username: str,
    current_user: UserRecord = Depends(require_admin),
):
    """Purge an account — irreversible, and allowed while it is awake (force-kill).

    Ordered so no failure can resurrect the account: the row is tombstoned
    ``purged`` FIRST (which already revokes every token and stops the scheduler /
    gateway), THEN the AgentTenant CR is deleted, which hands teardown to the
    operator's finalizer (pods to zero, storage deprovisioned, export PVC removed).
    The row itself is reaped by the ``sync_all_tenants`` sweep once the CR is gone, so
    a crash anywhere in between resumes instead of rolling back. Returns 202 — the
    account shows as PURGING until the sweep releases the username."""
    store = get_user_store()
    if username == current_user.username:
        raise HTTPException(400, "Cannot delete your own account")

    existing = store.get_user(username)
    if existing is None:
        raise HTTPException(404, f"User '{username}' not found")

    # Last admin protection
    if existing.role == "admin" and existing.status == "active" and _active_admin_count(store) <= 1:
        raise HTTPException(400, "Cannot remove the last admin")

    try:
        store.update_user(username, status="purged")
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    audit = get_audit_logger()
    audit.append(AuditEntry(
        actor=current_user.username,
        action="user.deleted",
        target=username,
        details={"role": existing.role},
    ))

    await nudge_reconcile(existing.account_id, existing.username)  # drop the Feishu WS now

    from ..provisioner import delete_tenant
    try:
        await asyncio.to_thread(delete_tenant, existing.account_id)
    except Exception as exc:
        # The tombstone stands: the account stays purged and sync_all_tenants re-issues
        # the CR delete, but the admin must know the runtime is not gone yet.
        logger.warning("delete AgentTenant failed for {}: {}", username, exc)
        raise HTTPException(502, f"Account revoked, but teardown could not start: {exc}") from exc

    return {"status": "purging", "account_id": existing.account_id}


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


async def _fleet_snapshot() -> dict:
    """Shared fleet fan-out (used by /fleet and /system-health).

    Lists the AgentTenant CRs (operator-written phase / readyReplicas / podIP),
    then fans out concurrently to each *awake* pod's /health to read its in-flight
    ``active_runs`` (summed = "running sessions") and self-reported ``deps``. The
    probe is fail-open, so one unreachable pod degrades to active_runs=None rather
    than failing the snapshot. Returns the sorted entries, the summary counts, the
    number of awake pods whose probe failed, and the raw health bodies.
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
    healths: list[dict] = []
    probe_failures = 0
    if awake_targets:
        results = await asyncio.gather(*(probe_health(ip, port) for _, ip in awake_targets))
        for (idx, _), health in zip(awake_targets, results):
            if health:
                entries[idx]["active_runs"] = int(health.get("active_runs") or 0)
                entries[idx]["last_activity_ts"] = health.get("last_activity_ts")
                healths.append(health)
            else:
                probe_failures += 1  # awake pod that should answer but didn't

    awake_count = sum(1 for e in entries if e["awake"])
    running = sum((e["active_runs"] or 0) for e in entries)

    # Awake first, then busiest, then by account for a stable order.
    entries.sort(key=lambda e: (not e["awake"], -(e["active_runs"] or 0), e["account_id"]))

    return {
        "entries": entries,
        "total_accounts": len(entries),
        "awake_sandboxes": awake_count,
        "running_sessions": running,
        "probe_failures": probe_failures,
        "healths": healths,
    }


async def _gateway_snapshot() -> GatewayMetricsResponse:
    """Shared gateway scrape (used by /gateway-metrics and /system-health).

    Scrapes the data-plane gateway pod's Prometheus endpoint (port 15020 — a
    container port not exposed on the Service, so we target the pod IP directly)
    and sums ``agentgateway_requests_total``. Cumulative counters; the SPA derives
    req/s from the delta between polls, so the server stays stateless. Fail-open:
    no reachable gateway pod => available=False.
    """
    import time as _time

    from ..provisioner import list_gateway_pod_ips, scrape_gateway_metrics

    settings = get_settings()
    port = settings.kubernetes.gateway_metrics_port

    ips = await asyncio.to_thread(list_gateway_pod_ips)
    parsed = None
    for ip in ips:  # first pod that answers wins (single data-plane pod in alpha)
        parsed = await scrape_gateway_metrics(ip, port)
        if parsed:
            break

    if not parsed:
        return GatewayMetricsResponse(available=False, scraped_at=_time.time())
    return GatewayMetricsResponse(
        available=True,
        total_requests=parsed["total_requests"],
        connections=parsed["connections"],
        by_status_class=parsed["by_status_class"],
        by_backend=parsed["by_backend"],
        scraped_at=_time.time(),
    )


@router.get("/fleet", response_model=FleetResponse)
async def get_fleet():
    """Live fleet snapshot for the admin dashboard. See ``_fleet_snapshot``."""
    snap = await _fleet_snapshot()
    return FleetResponse(
        total_accounts=snap["total_accounts"],
        awake_sandboxes=snap["awake_sandboxes"],
        running_sessions=snap["running_sessions"],
        accounts=[FleetAccountEntry(**e) for e in snap["entries"]],
    )


@router.post("/accounts/{account_id}/restart-pod")
async def restart_account_pod(
    account_id: str,
    current_user: UserRecord = Depends(require_admin),
):
    """Admin: force-restart an awake account runner through a controlled 1→0→1.

    The zero boundary lets the operator converge the full Deployment template before
    wake, so inherited resource and Runner/Terminal allocation changes actually
    reach the replacement Pod. No-op if the runner is asleep.
    """
    from ..provisioner import force_restart_pod

    try:
        deleted = await asyncio.to_thread(force_restart_pod, account_id)
    except Exception as exc:
        raise HTTPException(500, f"Force restart failed: {exc}") from exc

    get_audit_logger().append(AuditEntry(
        actor=current_user.username,
        action="runner.force_restarted",
        target=account_id,
        details={"pods_deleted": deleted},
    ))
    return {"status": "ok", "pods_deleted": deleted}


@router.post("/accounts/{account_id}/shutdown")
async def shutdown_account_runner(
    account_id: str,
    current_user: UserRecord = Depends(require_admin),
):
    """Admin: shut an account's agent-runner down now — flip its CR status not-routable,
    then scale the Deployment to zero (mirrors the operator idle-sweep). The operator only
    scales *up* (spec.wake), so this never fights it; the next user request re-wakes the
    pod. No-op if the runner is already asleep. Any in-flight run is lost."""
    from ..provisioner import shutdown_runner

    try:
        replicas = await asyncio.to_thread(shutdown_runner, account_id)
    except Exception as exc:
        raise HTTPException(500, f"Shutdown failed: {exc}") from exc

    get_audit_logger().append(AuditEntry(
        actor=current_user.username,
        action="runner.shutdown",
        target=account_id,
        details={"replicas_scaled_down": replicas},
    ))
    return {"status": "ok", "replicas_scaled_down": replicas}


@router.get("/gateway-metrics", response_model=GatewayMetricsResponse)
async def get_gateway_metrics():
    """Live agentgateway HTTP traffic snapshot. See ``_gateway_snapshot``."""
    return await _gateway_snapshot()


def _capacity_metric(
    node_allocatable: float,
    non_runner_requested: float,
    allocated: float,
) -> ClusterCapacityMetric:
    """Build one CPU/memory capacity line using the admin-confirmed formula."""
    assignable = max(0.0, float(node_allocatable) - float(non_runner_requested))
    allocated = float(allocated)
    remaining = assignable - allocated
    allocation_percent = (allocated / assignable * 100.0) if assignable > 0 else None
    overcommit_percent = (
        max(allocation_percent - 100.0, 0.0)
        if allocation_percent is not None else None
    )
    return ClusterCapacityMetric(
        node_allocatable=round(float(node_allocatable), 1),
        non_runner_requested=round(float(non_runner_requested), 1),
        assignable=round(assignable, 1),
        allocated=round(allocated, 1),
        remaining=round(remaining, 1),
        allocation_percent=(round(allocation_percent, 1)
                            if allocation_percent is not None else None),
        overcommit_percent=(round(overcommit_percent, 1)
                            if overcommit_percent is not None else None),
    )


@router.get("/cluster-capacity", response_model=ClusterCapacityResponse)
async def get_cluster_capacity():
    """Capacity that can be committed to active tenant runtimes.

    CPU and memory use scheduling semantics, not metrics-server usage:
    eligible Node allocatable minus effective requests of non-committed Pods.
    The numerator is every active account's effective resource quota, including
    auto-scale accounts at zero replicas. Runner + Terminal already split that
    single committed quota and are therefore counted exactly once per account.
    """
    import time as _time

    from priva_common.dataplane import get_client

    from ..provisioner import scrape_cluster_capacity

    active_account_ids = {
        user.account_id
        for user in get_user_store().list_users()
        if user.status == "active" and user.account_id
    }
    client = get_client()
    specs = {spec.account_id: spec for spec in client.resource_specs.list()}
    defaults = client.runner_defaults.get()

    allocated_cpu_m = 0.0
    allocated_memory_mb = 0.0
    for account_id in active_account_ids:
        spec = specs.get(account_id)
        allocated_cpu_m += float(spec.cpu_cores if spec else defaults.cpu_cores) * 1000.0
        allocated_memory_mb += float(spec.memory_mb if spec else defaults.memory_mb)

    snapshot = await asyncio.to_thread(scrape_cluster_capacity, active_account_ids)
    available = snapshot is not None
    snapshot = snapshot or {}
    return ClusterCapacityResponse(
        available=available,
        total_nodes=int(snapshot.get("total_nodes", 0)),
        eligible_nodes=int(snapshot.get("eligible_nodes", 0)),
        pending_non_runner_pods=int(snapshot.get("pending_non_runner_pods", 0)),
        active_accounts=len(active_account_ids),
        cpu=_capacity_metric(
            snapshot.get("node_allocatable_cpu_m", 0.0),
            snapshot.get("non_runner_requested_cpu_m", 0.0),
            allocated_cpu_m,
        ),
        memory=_capacity_metric(
            snapshot.get("node_allocatable_memory_mb", 0.0),
            snapshot.get("non_runner_requested_memory_mb", 0.0),
            allocated_memory_mb,
        ),
        nodes=snapshot.get("nodes", []),
        scraped_at=_time.time(),
    )


@router.get("/resource-usage", response_model=ResourceUsageResponse)
async def get_resource_usage():
    """Agent-runtime resource consumption for the admin Resource Quota view.

    Joins three sources by account_id: the fleet snapshot (live roster + awake
    state + username), live metrics-server CPU/memory (``scrape_runner_usage`` —
    only awake pods report), and the per-account ``account_resource_spec`` (the
    allocated quota). ``used`` totals sum over awake rows; ``allocated`` totals
    sum over ALL accounts. Fail-open: an unreachable metrics-server sets
    ``available=False`` and zeroes the used figures rather than failing.
    """
    import time as _time

    from priva_common.dataplane import get_client

    from ..provisioner import scrape_runner_usage, scrape_volume_usage

    fleet_snap, usage, vol_usage = await asyncio.gather(
        _fleet_snapshot(),
        asyncio.to_thread(scrape_runner_usage),
        asyncio.to_thread(scrape_volume_usage),
    )
    available = usage is not None
    usage = usage or {}
    vol_usage = vol_usage or {}  # {account_id: {used_bytes, limit_bytes}}, wake-free

    specs = {s.account_id: s for s in get_client().resource_specs.list()}
    users = {u.account_id: u for u in get_user_store().list_users()}
    # Accounts without a spec inherit the global defaults — use those as the allocated
    # fallback so the quota bars reflect the effective ceiling, not bare constants.
    rd = get_client().runner_defaults.get()

    entries: list[ResourceUsageAccountEntry] = []
    cpu_used = cpu_alloc = mem_used = mem_alloc = 0.0
    vol_alloc = 0
    vol_used = 0.0
    awake_n = sleeping_n = 0

    for e in fleet_snap["entries"]:
        aid = e["account_id"]
        spec = specs.get(aid)
        user = users.get(aid)
        u = usage.get(aid) or {}
        awake = bool(e["awake"])

        cpu_alloc_m = (spec.cpu_cores if spec else rd.cpu_cores) * 1000.0
        mem_alloc_mb = float(spec.memory_mb if spec else rd.memory_mb)
        vol_gb = int(spec.volume_gb if spec else rd.storage_gb)
        cpu_used_m = float(u.get("cpu_m", 0.0)) if awake else 0.0
        mem_used_mb = float(u.get("memory_mb", 0.0)) if awake else 0.0
        # Volume used is backend-reported (wake-free) — independent of awake state.
        vu = vol_usage.get(aid)
        vol_used_gb = round(vu["used_bytes"] / 1024 ** 3, 2) if vu else None

        entries.append(ResourceUsageAccountEntry(
            account_id=aid,
            username=e.get("username") or (user.username if user else None),
            runner_type=(getattr(user, "agent_runner_type", None) or "auto_scale"),
            awake=awake,
            cpu_used_m=round(cpu_used_m, 1),
            cpu_allocated_m=round(cpu_alloc_m, 1),
            memory_used_mb=round(mem_used_mb, 1),
            memory_allocated_mb=round(mem_alloc_mb, 1),
            volume_gb=vol_gb,
            volume_used_gb=vol_used_gb,
        ))

        cpu_used += cpu_used_m
        cpu_alloc += cpu_alloc_m
        mem_used += mem_used_mb
        mem_alloc += mem_alloc_mb
        vol_alloc += vol_gb
        vol_used += vol_used_gb or 0.0
        awake_n += 1 if awake else 0
        sleeping_n += 0 if awake else 1

    return ResourceUsageResponse(
        available=available,
        cpu_used_m=round(cpu_used, 1),
        cpu_allocated_m=round(cpu_alloc, 1),
        memory_used_mb=round(mem_used, 1),
        memory_allocated_mb=round(mem_alloc, 1),
        volume_allocated_gb=vol_alloc,
        volume_used_gb=round(vol_used, 2),
        awake=awake_n,
        sleeping=sleeping_n,
        total_accounts=len(entries),
        accounts=entries,
        scraped_at=_time.time(),
    )


@router.get("/system-health", response_model=SystemHealthResponse)
async def get_system_health():
    """Topology + live per-module health for the admin System Map.

    Read-only observability: k8s Deployment readiness (operator / data-spine) for
    up/down, the fleet fan-out for the agent-runner tier, the gateway scrape for
    the edge, and each module's self-reported ``/health.deps`` for edge-level ✕.
    Every probe is fail-open so an unreachable dependency degrades a node/edge
    rather than failing the snapshot. Planned modules render disabled (dim).
    """
    import time as _time

    from ..provisioner import dataspine_health, deployment_ready

    # --- Fan out the independent probes concurrently. ---
    fleet_snap, gateway, ds_health, operator_dep, dataspine_dep, cp_dep, gw_dep, scheduler_dep, connector_dep = await asyncio.gather(
        _fleet_snapshot(),
        _gateway_snapshot(),
        dataspine_health(),
        asyncio.to_thread(deployment_ready, "operator"),
        asyncio.to_thread(deployment_ready, "data-spine"),
        asyncio.to_thread(deployment_ready, "control-panel"),
        # The agentgateway controller names its Deployment after the Gateway CR
        # (deploy/gateway/gateway.yaml / helm gateway.name). Fail-open: an
        # unknown name just means no replica chip on that node.
        asyncio.to_thread(deployment_ready, "priva-gateway"),
        # scheduler (Phase 4a): leaderless firing engine, Deployment "scheduler".
        asyncio.to_thread(deployment_ready, "scheduler"),
        # channel-connector (Phase 4b): the Feishu WS bridge, Deployment "channel-connector".
        asyncio.to_thread(deployment_ready, "channel-connector"),
    )

    def _reps(dep: dict | None) -> ReplicaCount | None:
        # None (probe failed / no kube) => the node renders without a chip.
        return None if dep is None else ReplicaCount(ready=dep["ready"], desired=dep["desired"])

    # --- data-spine node: Deployment readiness for up/down, enriched by readyz/stats. ---
    ds_ready_ok = bool(ds_health and ds_health.get("ready"))
    if dataspine_dep is None:
        ds_status = "down"
    elif dataspine_dep["ready"] >= 1:
        ds_status = "up"
    elif dataspine_dep["desired"] >= 1:
        ds_status = "degraded"
    else:
        ds_status = "down"
    ds_stats = (ds_health or {}).get("stats") or {}
    ds_metrics = {k: float(v) for k, v in ds_stats.items() if isinstance(v, (int, float))}
    # Storage backend self-reported by the data-spine (Stats.backend) — never
    # hardcoded here, so the map stays truthful across sqlite/postgres cutovers.
    # Unknown (older data-spine image, or down) → omit the storage suffix.
    ds_backend = {"sqlite": "SQLite", "postgres": "Postgres"}.get(str(ds_stats.get("backend") or ""))
    ds_sub = f":50051 gRPC · {ds_backend}" if ds_backend else ":50051 gRPC"

    # --- operator node: Deployment readiness. ---
    if operator_dep is None:
        op_status = "down"
    elif operator_dep["ready"] >= 1:
        op_status = "up"
    elif operator_dep["desired"] >= 1:
        op_status = "degraded"
    else:
        op_status = "down"

    # --- scheduler node (Phase 4a): Deployment readiness, same rule as operator.
    # Shipped as the leaderless firing engine — no longer a planned module. ---
    if scheduler_dep is None:
        sched_status = "down"
    elif scheduler_dep["ready"] >= 1:
        sched_status = "up"
    elif scheduler_dep["desired"] >= 1:
        sched_status = "degraded"
    else:
        sched_status = "down"

    # --- channel-connector node (Phase 4b): Deployment readiness, same rule as the
    # operator/scheduler. Shipped as the Feishu WS bridge — no longer a planned
    # module. None (no kube / unknown name) → down, no replica chip. ---
    if connector_dep is None:
        conn_status = "down"
    elif connector_dep["ready"] >= 1:
        conn_status = "up"
    elif connector_dep["desired"] >= 1:
        conn_status = "degraded"
    else:
        conn_status = "down"

    # --- agent-runner tier: up if any awake, idle if scaled-to-zero, down on probe failure. ---
    awake = fleet_snap["awake_sandboxes"]
    if fleet_snap["probe_failures"] > 0:
        ar_status = "down"
    elif awake >= 1:
        ar_status = "up"
    else:
        ar_status = "idle"
    # Merge the probed pods' self-reported data-spine dep: any pod reporting it down
    # marks the agent-runner→data-spine edge ✕. None (no probe data) = unknown.
    pod_ds_oks = [
        bool(d.get("ok"))
        for h in fleet_snap["healths"]
        for d in (h.get("deps") or [])
        if d.get("name") == "data-spine"
    ]
    ar_ds_ok: bool | None = all(pod_ds_oks) if pod_ds_oks else None
    ar_deps = [HealthDep(name="data-spine", ok=ar_ds_ok,
                         detail=None if ar_ds_ok is None else
                         ("reachable" if ar_ds_ok else "a pod reports data-spine down"))]

    # --- gateway (edge) node. ---
    gw_up = bool(gateway.available)
    gw_metrics = {"connections": float(gateway.connections)} if gw_up else {}

    nodes = [
        SystemNode(id="browser", label="browser", sub="SPA · JWT/WS", plane="edge",
                   status="up", detail="admin / user SPA"),
        SystemNode(id="agentgateway", label="agentgateway", sub=":80 · Gateway API (Rust)",
                   plane="edge", status="up" if gw_up else "down",
                   detail="edge — transports runtime bytes, makes no decisions",
                   metrics=gw_metrics, replicas=_reps(gw_dep)),
        SystemNode(id="control-panel", label="control-panel", sub=":8080 HTTP · :9000 EPP",
                   plane="control", status="up",
                   detail="auth · admin · config · EndPoint Picker",
                   deps=[HealthDep(name="data-spine", ok=ds_ready_ok,
                                   detail=(ds_health or {}).get("detail"))],
                   replicas=_reps(cp_dep)),
        # operator: the replica chip carries ready/desired (was the "1/1 ready"
        # metric line); detail stays None so the 180px box doesn't overflow
        # (role text: reconciles AgentTenant CRD · injects Secret · idle reaping).
        SystemNode(id="operator", label="operator", sub="kopf · sole scaler 0 ↔ 1",
                   plane="control", status=op_status, replicas=_reps(operator_dep)),
        # scheduler: shipped in Phase 4a. Leaderless waker/dispatcher — fires,
        # claims exactly-once over the data-spine, wakes the pod, dispatches the
        # run; executes nothing itself (the pod runs the job). The replica chip
        # carries ready/desired; replicas is an availability knob, not sharding.
        SystemNode(id="scheduler", label="scheduler", sub="claim → wake → dispatch",
                   plane="control", status=sched_status, detail="leaderless firing",
                   replicas=_reps(scheduler_dep)),
        SystemNode(id="data-spine", label="data-spine", sub=ds_sub,
                   plane="data", status=ds_status,
                   detail=(ds_health or {}).get("detail") or "source of truth",
                   metrics=ds_metrics, replicas=_reps(dataspine_dep)),
        SystemNode(id="redis", label="redis", sub="coordination bulletin board",
                   plane="data", status="disabled", detail="planned · phase 4"),
        SystemNode(id="agent-runner", label="agent-runner", sub="ar-<account> · :8091 · 0 ↔ 1",
                   plane="tenant", status=ar_status,
                   detail="per-tenant runtime · runs the claude CLI",
                   metrics={"awake": float(awake),
                            "running": float(fleet_snap["running_sessions"]),
                            "total": float(fleet_snap["total_accounts"])},
                   deps=ar_deps),
        # 飞书DM: the external IM (self-built app). NOT our infra and NOT in any
        # cluster plane — the frontend draws it OUTSIDE the cluster boundary (public
        # internet). Modeled like `browser`, an always-present external entry point;
        # its edges to the connector go ✕ automatically when the connector is down.
        SystemNode(id="feishu", label="飞书DM", sub="外部 IM",
                   plane="external", status="up"),
        SystemNode(id="channel-connector", label="channel-connector",
                   sub=":8083 · Feishu WS fan-out", plane="tenant", status=conn_status,
                   detail="IM ⇄ runtime bridge", replicas=_reps(connector_dep)),
        SystemNode(id="state-reader", label="state-reader", sub="wake-free JSONL reads",
                   plane="tenant", status="disabled", detail="planned · phase 5"),
    ]
    status_by_id = {n.id: n.status for n in nodes}

    def _live(node_id: str) -> bool:
        return status_by_id.get(node_id) not in ("down", "disabled")

    def _edge(source: str, target: str, *, label: str | None = None, kind: str = "control",
              bytepath: bool = False, disabled: bool = False, dep_ok: bool | None = None) -> SystemEdge:
        # General rule: healthy unless an endpoint is down/disabled, AND (when a
        # self-reported dep result is available) that dep is ok.
        healthy = (not disabled and _live(source) and _live(target)
                   and (dep_ok is not False))
        return SystemEdge(source=source, target=target, label=label, kind=kind,
                          bytepath=bytepath, healthy=healthy, disabled=disabled)

    # control-panel self-reports its own data-spine readyz → drives that edge's ✕.
    cp_ds_ok = ds_ready_ok

    edges = [
        # Byte path (animated when healthy).
        _edge("browser", "agentgateway", label="HTTP :80", kind="byte", bytepath=True),
        _edge("agentgateway", "control-panel", label="/ · /admin · /api", kind="byte", bytepath=True),
        _edge("agentgateway", "agent-runner", label="runtime bytes", kind="byte", bytepath=True),
        # Decision / control / data edges.
        _edge("agentgateway", "control-panel", label="ext_proc (EPP)", kind="decision"),
        _edge("control-panel", "operator", label="patch CR", kind="control"),
        _edge("operator", "agent-runner", label="scale 0 ↔ 1 · Secret", kind="control"),
        _edge("control-panel", "data-spine", label="gRPC", kind="grpc", dep_ok=cp_ds_ok),
        _edge("operator", "data-spine", label="gRPC", kind="grpc"),
        _edge("agent-runner", "data-spine", label="gRPC", kind="grpc", dep_ok=ar_ds_ok),
        # scheduler (Phase 4a): the leaderless fire → claim → wake → dispatch path.
        # Claims the fire over the data-spine gRPC, wakes the pod via a CR patch
        # (the operator stays the sole scaler), then dispatches the run to the pod
        # with a 202-admission POST. Animated when the scheduler is up.
        _edge("scheduler", "data-spine", label="gRPC", kind="grpc"),
        _edge("scheduler", "operator", label="wake", kind="control"),
        _edge("scheduler", "agent-runner", label="dispatch", kind="control"),
        # channel-connector (Phase 4b): the Feishu WS bridge. Inbound DM over the WS
        # long-connection, reply over REST (feishu ⇄ connector, unlabeled — the
        # particle flow shows the duplex). Wakes the pod via a CR patch (operator
        # stays sole scaler, same pattern as the scheduler) and dials /run/stream,
        # relaying the assistant text back. control-panel pushes a targeted reconcile
        # on config edit. gRPC to the data-spine for bindings/secret/status. All
        # animated when the connector is up.
        # These two cross the cluster boundary (drawn in the frontend) — the ONLY hop
        # that leaves k8s (the WS long-connection the connector dialed out). Unlabeled;
        # the boundary crossing itself conveys the egress.
        _edge("feishu", "channel-connector", kind="byte", bytepath=True),
        _edge("channel-connector", "feishu", kind="byte", bytepath=True),
        _edge("channel-connector", "agent-runner", label="/run/stream", kind="byte", bytepath=True),
        _edge("channel-connector", "operator", label="wake", kind="control"),
        _edge("channel-connector", "data-spine", label="gRPC", kind="grpc"),
        _edge("control-panel", "channel-connector", label="reconcile", kind="control"),
        # Planned edge (never animated). Redis stays parked — v1 is Postgres-claim,
        # re-list, and CR wake (no Redis).
        _edge("data-spine", "redis", kind="grpc", disabled=True),
    ]

    return SystemHealthResponse(nodes=nodes, edges=edges, scraped_at=_time.time())


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
