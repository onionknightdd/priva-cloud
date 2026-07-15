"""Control-panel DTOs for per-account Feishu bot config.

The wire/data-plane record (`FeishuChannelConfigRecord`) already omits the
app_secret; these HTTP DTOs enforce the same write-only contract for the SPA:
the response exposes only `app_secret_set` (a boolean), never the value. Two
write DTOs mirror the two role-scoped routes — the USER self-serve route writes
credentials + own toggle + behaviour, the ADMIN route writes ONLY the kill-switch.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FeishuConnectionStatus(BaseModel):
    """Connector-observed runtime health (read-only; written by the channel-connector)."""
    conn_status: str = "disabled"  # disabled|connecting|connected|auth_failed|error|conflict
    last_error_code: int | None = None
    last_error_message: str | None = None
    last_connected_at: str | None = None
    status_updated_at: str | None = None


class FeishuConfigResponse(BaseModel):
    """Read view returned by both the admin (status) and user (self-serve) endpoints.
    app_secret is NEVER exposed — only `app_secret_set`."""
    account_id: str
    app_id: str | None = None
    app_secret_set: bool = False
    app_secret_updated_at: str | None = None
    user_enabled: bool = False
    admin_disabled: bool = False
    effective_enabled: bool = False
    single_chat_access_mode: str = "owner_only"
    allowed_union_ids: str = "[]"
    welcome_message: str = ""
    reject_message: str = ""
    model: str | None = None
    max_queue_size: int = 3
    enable_permission_feedback: bool = True
    feedback_timeout_seconds: int = 180
    domain: str = "feishu"
    connection: FeishuConnectionStatus = Field(default_factory=FeishuConnectionStatus)
    updated_by: str = ""
    updated_at: str | None = None

    @classmethod
    def from_record(cls, rec, account_id: str) -> "FeishuConfigResponse":
        """Map a FeishuChannelConfigRecord (or None = not configured) to the DTO.
        Duck-typed to avoid importing the data-plane record type here."""
        if rec is None:
            return cls(account_id=account_id)
        return cls(
            account_id=rec.account_id,
            app_id=rec.app_id,
            app_secret_set=rec.has_app_secret,
            app_secret_updated_at=rec.app_secret_updated_at,
            user_enabled=rec.user_enabled,
            admin_disabled=rec.admin_disabled,
            effective_enabled=rec.effective_enabled,
            single_chat_access_mode=rec.single_chat_access_mode,
            allowed_union_ids=rec.allowed_union_ids,
            welcome_message=rec.welcome_message,
            reject_message=rec.reject_message,
            model=rec.model,
            max_queue_size=rec.max_queue_size,
            enable_permission_feedback=rec.enable_permission_feedback,
            feedback_timeout_seconds=rec.feedback_timeout_seconds,
            domain=rec.domain,
            connection=FeishuConnectionStatus(
                conn_status=rec.conn_status,
                last_error_code=rec.last_error_code,
                last_error_message=rec.last_error_message,
                last_connected_at=rec.last_connected_at,
                status_updated_at=rec.status_updated_at,
            ),
            updated_by=rec.updated_by,
            updated_at=rec.updated_at,
        )


class FeishuUserConfigUpdate(BaseModel):
    """User self-serve write — credentials + own toggle + behaviour. All Optional
    (None = leave unchanged). app_secret: None=keep, "__clear__"=unset, any other
    value = set/rotate; empty string "" is rejected (400) to avoid an accidental wipe."""
    app_id: str | None = None
    app_secret: str | None = None
    user_enabled: bool | None = None
    single_chat_access_mode: str | None = None
    allowed_union_ids: str | None = None
    welcome_message: str | None = None
    reject_message: str | None = None
    model: str | None = None
    max_queue_size: int | None = None
    enable_permission_feedback: bool | None = None
    feedback_timeout_seconds: int | None = None
    domain: str | None = None


class FeishuAdminConfigUpdate(BaseModel):
    """Admin write — kill-switch ONLY (cannot touch credentials or the user's toggle)."""
    admin_disabled: bool | None = None
