"""Control-panel DTOs for per-account Feishu bot config.

The wire/data-plane record (`FeishuChannelConfigRecord`) already omits the
app_secret; these HTTP DTOs enforce the same write-only contract for the SPA:
the response exposes only `app_secret_set` (a boolean), never the value. Two
write DTOs mirror the two role-scoped routes — the USER self-serve route writes
credentials + own toggle + behaviour, the ADMIN route writes ONLY the kill-switch.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


def _mask_union_id(union_id: str) -> str:
    """`on_9f3ab8c41d2` → `on_9f3a…1d2`: enough to recognize, not enough to replay."""
    if not union_id:
        return ""
    if len(union_id) <= 10:
        return union_id[:4] + "…"
    return f"{union_id[:7]}…{union_id[-3:]}"


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
    # Owner link-code binding — union_id is masked (never fully exposed to the SPA);
    # the pending code itself is NEVER in this read view (POST /feishu-link-code only).
    owner_bound: bool = False
    owner_bound_at: str | None = None
    owner_union_id_masked: str = ""
    # Group-chat participation (feat_feishu_DM.md §5): the user's own opt-in, the
    # composed effective bit, and the admin global switch (drives the grey-out +
    # hint in the SPA — the user toggle stays visible but inert while it's on).
    group_chat_enabled: bool = False
    effective_group_enabled: bool = False
    group_chat_globally_disabled: bool = False
    connection: FeishuConnectionStatus = Field(default_factory=FeishuConnectionStatus)
    updated_by: str = ""
    updated_at: str | None = None

    @classmethod
    def from_record(cls, rec, account_id: str,
                    group_chat_globally_disabled: bool = False) -> "FeishuConfigResponse":
        """Map a FeishuChannelConfigRecord (or None = not configured) to the DTO.
        Duck-typed to avoid importing the data-plane record type here."""
        if rec is None:
            return cls(account_id=account_id,
                       group_chat_globally_disabled=group_chat_globally_disabled)
        owner_union = getattr(rec, "owner_union_id", "") or ""
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
            owner_bound=bool(owner_union),
            owner_bound_at=getattr(rec, "owner_bound_at", None),
            owner_union_id_masked=_mask_union_id(owner_union),
            group_chat_enabled=bool(getattr(rec, "group_chat_enabled", False)),
            effective_group_enabled=bool(getattr(rec, "effective_group_enabled", False)),
            group_chat_globally_disabled=group_chat_globally_disabled,
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
    group_chat_enabled: bool | None = None


class FeishuAdminConfigUpdate(BaseModel):
    """Admin write — kill-switch ONLY (cannot touch credentials or the user's toggle)."""
    admin_disabled: bool | None = None


class ChannelPlatformConfigResponse(BaseModel):
    """Admin Configurations ▸ Channels — the platform-wide singleton."""
    group_chat_disabled: bool = False
    updated_by: str = ""
    updated_at: str | None = None


class ChannelPlatformConfigUpdate(BaseModel):
    """Admin write — the global group-chat kill switch (None = leave unchanged)."""
    group_chat_disabled: bool | None = None


class FeishuLinkCodeResponse(BaseModel):
    """One-shot response to POST /me/feishu-link-code — the ONLY place the plaintext
    code ever appears (storage keeps its SHA-256 + expiry)."""
    code: str
    expires_at: str


class FeishuSessionEntry(BaseModel):
    """One chat the bot has been talked to in (a channel_binding row). session_id
    None = the chat was reset with /new (next message starts a fresh session)."""
    chat_id: str
    chat_type: str = ""      # "p2p" | "group" | "" (pre-feature rows)
    chat_name: str = ""      # p2p: peer name · group: group name · "" unresolved
    session_id: str | None = None
    updated_at: str | None = None


class FeishuSessionsResponse(BaseModel):
    sessions: list[FeishuSessionEntry]
