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
    PendingRegistrationRecord,
    QuotaRecord,
    ResourceSpecRecord,
    SecretRecord,
)
from priva_common.models.auth import UserRecord


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
        session_uuid=m.session_uuid,
        first_run_done=m.first_run_done,
        feishu_chat_id=m.feishu_chat_id or None,
        bound_at=m.bound_at or None,
        rebound_at=m.rebound_at or None,
    )


def secret_from_pb(m) -> SecretRecord | None:
    if not m.account_id:
        return None
    try:
        bundle = json.loads(m.bundle) if m.bundle else {}
    except (ValueError, TypeError):
        bundle = {}
    return SecretRecord(
        account_id=m.account_id,
        bundle=bundle,
        generation=m.generation,
        updated_at=m.updated_at or None,
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
