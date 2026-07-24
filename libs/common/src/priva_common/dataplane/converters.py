"""proto → pydantic/record converters for the gRPC data-plane client.

The server side (priva_data_spine.server) builds proto messages FROM records;
this module maps the proto responses BACK to the boundary DTOs the Protocols
return. A "not found" result rides as a message with an empty key field
(account_id / binding_id) and maps to ``None`` here.
"""

from __future__ import annotations

import json

from priva_common.dataplane.client import (
    BindingRecord,
    ChannelPlatformConfigRecord,
    FeishuChannelConfigRecord,
    FeishuSecretRecord,
    HookPolicyRecord,
    PendingRegistrationRecord,
    QuotaRecord,
    ResourceSpecRecord,
    RunnerDefaultsRecord,
)
from priva_common.models.auth import UserRecord
from priva_common.models.scheduler import JobRunRecord, ScheduledJobDefinition


def user_from_pb(m) -> UserRecord | None:
    if not m.account_id:
        return None
    return UserRecord(
        username=m.username,
        password_hash="",  # never carried over the wire (security); auth goes via VerifyPassword
        role=m.role or "user",
        api_key=m.api_key or None,
        account_id=m.account_id,
        status=m.status or "active",
        agent_runner_type=m.agent_runner_type or "auto_scale",
        feishu_user_id=m.feishu_user_id or None,
        feishu_display_name=m.feishu_display_name or None,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


def quota_from_pb(m) -> QuotaRecord | None:
    if not m.account_id:
        return None
    return QuotaRecord(
        account_id=m.account_id,
        tier=m.tier or "default",
        max_concurrent_sessions=m.max_concurrent_sessions,
        idle_grace_seconds=m.idle_grace_seconds,
        updated_at=m.updated_at or None,
    )


def binding_from_pb(m) -> BindingRecord | None:
    if not m.binding_id:
        return None
    return BindingRecord(
        binding_id=m.binding_id,
        account_id=m.account_id,
        session_uuid=m.session_uuid or None,  # "" (detached) → None
        first_run_done=m.first_run_done,
        feishu_chat_id=m.feishu_chat_id or None,
        chat_type=m.chat_type,
        chat_name=m.chat_name,
        bound_at=m.bound_at or None,
        rebound_at=m.rebound_at or None,
    )


def resource_spec_from_pb(m) -> ResourceSpecRecord | None:
    if not m.account_id:
        return None
    return ResourceSpecRecord(
        account_id=m.account_id,
        cpu_cores=m.cpu_cores,
        memory_mb=m.memory_mb,
        volume_gb=m.volume_gb,
        updated_at=m.updated_at or None,
    )


def feishu_config_from_pb(m) -> FeishuChannelConfigRecord | None:
    if not m.account_id:
        return None
    return FeishuChannelConfigRecord(
        account_id=m.account_id,
        app_id=m.app_id or None,
        has_app_secret=m.has_app_secret,
        app_secret_updated_at=m.app_secret_updated_at or None,
        user_enabled=m.user_enabled,
        admin_disabled=m.admin_disabled,
        effective_enabled=m.effective_enabled,
        single_chat_access_mode=m.single_chat_access_mode or "owner_only",
        allowed_union_ids=m.allowed_union_ids or "[]",
        welcome_message=m.welcome_message,
        reject_message=m.reject_message,
        model=m.model or None,
        max_queue_size=m.max_queue_size,
        enable_permission_feedback=m.enable_permission_feedback,
        feedback_timeout_seconds=m.feedback_timeout_seconds,
        domain=m.domain or "feishu",
        owner_union_id=m.owner_union_id,
        owner_open_id=m.owner_open_id,
        owner_bound_at=m.owner_bound_at or None,
        group_chat_enabled=m.group_chat_enabled,
        effective_group_enabled=m.effective_group_enabled,
        conn_status=m.conn_status or "disabled",
        last_error_code=m.last_error_code if m.last_error_code else None,
        last_error_message=m.last_error_message or None,
        last_connected_at=m.last_connected_at or None,
        status_updated_at=m.status_updated_at or None,
        desired_digest=m.desired_digest or None,
        updated_by=m.updated_by,
        updated_at=m.updated_at or None,
    )


def feishu_secret_from_pb(m) -> FeishuSecretRecord | None:
    # Connector-only. account_id "" means the account had no config row at all.
    if not m.account_id:
        return None
    return FeishuSecretRecord(
        account_id=m.account_id,
        app_id=m.app_id or None,
        app_secret=m.app_secret or "",
        domain=m.domain or "feishu",
    )


def channel_platform_from_pb(m) -> ChannelPlatformConfigRecord:
    # Singleton with static defaults — a never-written row reads as all-off.
    return ChannelPlatformConfigRecord(
        group_chat_disabled=m.group_chat_disabled,
        updated_by=m.updated_by,
        updated_at=m.updated_at or None,
    )


def runner_defaults_from_pb(m) -> RunnerDefaultsRecord:
    # Singleton, always populated server-side (seeded from settings) — no None case.
    return RunnerDefaultsRecord(
        idle_grace_seconds=m.idle_grace_seconds,
        min_alive_after_wake_seconds=m.min_alive_after_wake_seconds,
        cpu_cores=m.cpu_cores,
        memory_mb=m.memory_mb,
        storage_gb=m.storage_gb,
        terminal_resource_percent=m.terminal_resource_percent,
        terminal_max_sessions=m.terminal_max_sessions,
        terminal_idle_timeout_seconds=m.terminal_idle_timeout_seconds,
        terminal_max_lifetime_seconds=m.terminal_max_lifetime_seconds,
        terminal_scale_down_grace_seconds=m.terminal_scale_down_grace_seconds,
        updated_at=m.updated_at or None,
    )


def hook_policy_from_pb(m) -> HookPolicyRecord | None:
    if not m.id:
        return None
    return HookPolicyRecord(
        id=m.id,
        hook_type=m.hook_type or "command",
        name=m.name,
        description=m.description,
        events=list(m.events),
        matcher=m.matcher,
        timeout_seconds=m.timeout_seconds,
        interpreter=m.interpreter,
        script_body=m.script_body,
        content_hash=m.content_hash,
        url=m.url,
        headers_json=m.headers_json,
        allowed_env_vars=list(m.allowed_env_vars),
        mcp_server=m.mcp_server,
        mcp_tool=m.mcp_tool,
        enabled=m.enabled,
        enforced=m.enforced,
        default_on=m.default_on,
        predefined=m.predefined,
        seed_version=m.seed_version,
        target=m.target,
        updated_at=m.updated_at or None,
        updated_by=m.updated_by,
        enforced_events=list(m.enforced_events),
    )


def job_from_pb(m) -> ScheduledJobDefinition | None:
    if not m.job_id:
        return None
    return ScheduledJobDefinition.model_validate({
        "id": m.job_id,
        "name": m.name,
        "prompt": m.prompt,
        "trigger": json.loads(m.trigger),
        "timezone": m.timezone,
        "status": m.status or "active",
        "model": m.model or None,
        "job_config": json.loads(m.job_config) if m.job_config else None,
        "created_at": m.created_at,
        "updated_at": m.updated_at,
    })


def run_from_pb(m) -> JobRunRecord | None:
    if not m.run_id:
        return None
    return JobRunRecord(
        run_id=m.run_id,
        job_id=m.job_id or "",
        job_name=m.job_name,
        username="",  # wire carries account_id, not username (in-process parity)
        started_at=m.started_at,
        finished_at=m.finished_at or None,
        status=m.status,
        duration_ms=m.duration_ms if m.duration_ms else None,
        is_error=m.is_error,
        error_message=m.error_message or None,
        num_turns=m.num_turns if m.num_turns else None,
        result_summary=m.result_summary or None,
        session_id=m.session_id or None,
    )


def pending_from_pb(m) -> PendingRegistrationRecord | None:
    if not m.request_id:
        return None
    return PendingRegistrationRecord(
        request_id=m.request_id,
        username=m.username,
        display_name=m.display_name or None,
        runner_type=m.runner_type or "auto_scale",
        cpu_cores=m.cpu_cores,
        memory_mb=m.memory_mb,
        volume_gb=m.volume_gb,
        note=m.note or None,
        status=m.status or "pending",
        created_at=m.created_at or None,
        updated_at=m.updated_at or None,
        password_hash=m.password_hash or None,
    )
