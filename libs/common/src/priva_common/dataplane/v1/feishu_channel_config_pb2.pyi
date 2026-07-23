from priva_common.dataplane.v1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class FeishuChannelConfig(_message.Message):
    __slots__ = ("account_id", "app_id", "has_app_secret", "app_secret_updated_at", "user_enabled", "admin_disabled", "effective_enabled", "single_chat_access_mode", "allowed_union_ids", "welcome_message", "reject_message", "model", "max_queue_size", "enable_permission_feedback", "feedback_timeout_seconds", "domain", "conn_status", "last_error_code", "last_error_message", "last_connected_at", "status_updated_at", "desired_digest", "updated_by", "updated_at", "owner_union_id", "owner_open_id", "owner_bound_at", "group_chat_enabled", "effective_group_enabled")
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    APP_ID_FIELD_NUMBER: _ClassVar[int]
    HAS_APP_SECRET_FIELD_NUMBER: _ClassVar[int]
    APP_SECRET_UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    USER_ENABLED_FIELD_NUMBER: _ClassVar[int]
    ADMIN_DISABLED_FIELD_NUMBER: _ClassVar[int]
    EFFECTIVE_ENABLED_FIELD_NUMBER: _ClassVar[int]
    SINGLE_CHAT_ACCESS_MODE_FIELD_NUMBER: _ClassVar[int]
    ALLOWED_UNION_IDS_FIELD_NUMBER: _ClassVar[int]
    WELCOME_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    REJECT_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    MAX_QUEUE_SIZE_FIELD_NUMBER: _ClassVar[int]
    ENABLE_PERMISSION_FEEDBACK_FIELD_NUMBER: _ClassVar[int]
    FEEDBACK_TIMEOUT_SECONDS_FIELD_NUMBER: _ClassVar[int]
    DOMAIN_FIELD_NUMBER: _ClassVar[int]
    CONN_STATUS_FIELD_NUMBER: _ClassVar[int]
    LAST_ERROR_CODE_FIELD_NUMBER: _ClassVar[int]
    LAST_ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    LAST_CONNECTED_AT_FIELD_NUMBER: _ClassVar[int]
    STATUS_UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    DESIRED_DIGEST_FIELD_NUMBER: _ClassVar[int]
    UPDATED_BY_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    OWNER_UNION_ID_FIELD_NUMBER: _ClassVar[int]
    OWNER_OPEN_ID_FIELD_NUMBER: _ClassVar[int]
    OWNER_BOUND_AT_FIELD_NUMBER: _ClassVar[int]
    GROUP_CHAT_ENABLED_FIELD_NUMBER: _ClassVar[int]
    EFFECTIVE_GROUP_ENABLED_FIELD_NUMBER: _ClassVar[int]
    account_id: str
    app_id: str
    has_app_secret: bool
    app_secret_updated_at: str
    user_enabled: bool
    admin_disabled: bool
    effective_enabled: bool
    single_chat_access_mode: str
    allowed_union_ids: str
    welcome_message: str
    reject_message: str
    model: str
    max_queue_size: int
    enable_permission_feedback: bool
    feedback_timeout_seconds: int
    domain: str
    conn_status: str
    last_error_code: int
    last_error_message: str
    last_connected_at: str
    status_updated_at: str
    desired_digest: str
    updated_by: str
    updated_at: str
    owner_union_id: str
    owner_open_id: str
    owner_bound_at: str
    group_chat_enabled: bool
    effective_group_enabled: bool
    def __init__(self, account_id: _Optional[str] = ..., app_id: _Optional[str] = ..., has_app_secret: _Optional[bool] = ..., app_secret_updated_at: _Optional[str] = ..., user_enabled: _Optional[bool] = ..., admin_disabled: _Optional[bool] = ..., effective_enabled: _Optional[bool] = ..., single_chat_access_mode: _Optional[str] = ..., allowed_union_ids: _Optional[str] = ..., welcome_message: _Optional[str] = ..., reject_message: _Optional[str] = ..., model: _Optional[str] = ..., max_queue_size: _Optional[int] = ..., enable_permission_feedback: _Optional[bool] = ..., feedback_timeout_seconds: _Optional[int] = ..., domain: _Optional[str] = ..., conn_status: _Optional[str] = ..., last_error_code: _Optional[int] = ..., last_error_message: _Optional[str] = ..., last_connected_at: _Optional[str] = ..., status_updated_at: _Optional[str] = ..., desired_digest: _Optional[str] = ..., updated_by: _Optional[str] = ..., updated_at: _Optional[str] = ..., owner_union_id: _Optional[str] = ..., owner_open_id: _Optional[str] = ..., owner_bound_at: _Optional[str] = ..., group_chat_enabled: _Optional[bool] = ..., effective_group_enabled: _Optional[bool] = ...) -> None: ...

class SetFeishuUserConfigRequest(_message.Message):
    __slots__ = ("account_id", "app_id", "app_secret", "user_enabled", "single_chat_access_mode", "allowed_union_ids", "welcome_message", "reject_message", "model", "max_queue_size", "enable_permission_feedback", "feedback_timeout_seconds", "domain", "updated_by", "update_mask", "group_chat_enabled")
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    APP_ID_FIELD_NUMBER: _ClassVar[int]
    APP_SECRET_FIELD_NUMBER: _ClassVar[int]
    USER_ENABLED_FIELD_NUMBER: _ClassVar[int]
    SINGLE_CHAT_ACCESS_MODE_FIELD_NUMBER: _ClassVar[int]
    ALLOWED_UNION_IDS_FIELD_NUMBER: _ClassVar[int]
    WELCOME_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    REJECT_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    MAX_QUEUE_SIZE_FIELD_NUMBER: _ClassVar[int]
    ENABLE_PERMISSION_FEEDBACK_FIELD_NUMBER: _ClassVar[int]
    FEEDBACK_TIMEOUT_SECONDS_FIELD_NUMBER: _ClassVar[int]
    DOMAIN_FIELD_NUMBER: _ClassVar[int]
    UPDATED_BY_FIELD_NUMBER: _ClassVar[int]
    UPDATE_MASK_FIELD_NUMBER: _ClassVar[int]
    GROUP_CHAT_ENABLED_FIELD_NUMBER: _ClassVar[int]
    account_id: str
    app_id: str
    app_secret: str
    user_enabled: bool
    single_chat_access_mode: str
    allowed_union_ids: str
    welcome_message: str
    reject_message: str
    model: str
    max_queue_size: int
    enable_permission_feedback: bool
    feedback_timeout_seconds: int
    domain: str
    updated_by: str
    update_mask: _containers.RepeatedScalarFieldContainer[str]
    group_chat_enabled: bool
    def __init__(self, account_id: _Optional[str] = ..., app_id: _Optional[str] = ..., app_secret: _Optional[str] = ..., user_enabled: _Optional[bool] = ..., single_chat_access_mode: _Optional[str] = ..., allowed_union_ids: _Optional[str] = ..., welcome_message: _Optional[str] = ..., reject_message: _Optional[str] = ..., model: _Optional[str] = ..., max_queue_size: _Optional[int] = ..., enable_permission_feedback: _Optional[bool] = ..., feedback_timeout_seconds: _Optional[int] = ..., domain: _Optional[str] = ..., updated_by: _Optional[str] = ..., update_mask: _Optional[_Iterable[str]] = ..., group_chat_enabled: _Optional[bool] = ...) -> None: ...

class SetFeishuAdminConfigRequest(_message.Message):
    __slots__ = ("account_id", "admin_disabled", "updated_by", "update_mask")
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    ADMIN_DISABLED_FIELD_NUMBER: _ClassVar[int]
    UPDATED_BY_FIELD_NUMBER: _ClassVar[int]
    UPDATE_MASK_FIELD_NUMBER: _ClassVar[int]
    account_id: str
    admin_disabled: bool
    updated_by: str
    update_mask: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, account_id: _Optional[str] = ..., admin_disabled: _Optional[bool] = ..., updated_by: _Optional[str] = ..., update_mask: _Optional[_Iterable[str]] = ...) -> None: ...

class SetFeishuStatusRequest(_message.Message):
    __slots__ = ("account_id", "conn_status", "last_error_code", "last_error_message", "last_connected_at", "update_mask")
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    CONN_STATUS_FIELD_NUMBER: _ClassVar[int]
    LAST_ERROR_CODE_FIELD_NUMBER: _ClassVar[int]
    LAST_ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    LAST_CONNECTED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATE_MASK_FIELD_NUMBER: _ClassVar[int]
    account_id: str
    conn_status: str
    last_error_code: int
    last_error_message: str
    last_connected_at: str
    update_mask: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, account_id: _Optional[str] = ..., conn_status: _Optional[str] = ..., last_error_code: _Optional[int] = ..., last_error_message: _Optional[str] = ..., last_connected_at: _Optional[str] = ..., update_mask: _Optional[_Iterable[str]] = ...) -> None: ...

class FeishuChannelConfigList(_message.Message):
    __slots__ = ("configs",)
    CONFIGS_FIELD_NUMBER: _ClassVar[int]
    configs: _containers.RepeatedCompositeFieldContainer[FeishuChannelConfig]
    def __init__(self, configs: _Optional[_Iterable[_Union[FeishuChannelConfig, _Mapping]]] = ...) -> None: ...

class FeishuSecret(_message.Message):
    __slots__ = ("account_id", "app_id", "app_secret", "domain")
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    APP_ID_FIELD_NUMBER: _ClassVar[int]
    APP_SECRET_FIELD_NUMBER: _ClassVar[int]
    DOMAIN_FIELD_NUMBER: _ClassVar[int]
    account_id: str
    app_id: str
    app_secret: str
    domain: str
    def __init__(self, account_id: _Optional[str] = ..., app_id: _Optional[str] = ..., app_secret: _Optional[str] = ..., domain: _Optional[str] = ...) -> None: ...

class LinkCode(_message.Message):
    __slots__ = ("code", "expires_at")
    CODE_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    code: str
    expires_at: str
    def __init__(self, code: _Optional[str] = ..., expires_at: _Optional[str] = ...) -> None: ...

class BindOwnerRequest(_message.Message):
    __slots__ = ("account_id", "code", "union_id", "open_id")
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    CODE_FIELD_NUMBER: _ClassVar[int]
    UNION_ID_FIELD_NUMBER: _ClassVar[int]
    OPEN_ID_FIELD_NUMBER: _ClassVar[int]
    account_id: str
    code: str
    union_id: str
    open_id: str
    def __init__(self, account_id: _Optional[str] = ..., code: _Optional[str] = ..., union_id: _Optional[str] = ..., open_id: _Optional[str] = ...) -> None: ...

class BindOwnerResult(_message.Message):
    __slots__ = ("ok",)
    OK_FIELD_NUMBER: _ClassVar[int]
    ok: bool
    def __init__(self, ok: _Optional[bool] = ...) -> None: ...

class UnbindOwnerRequest(_message.Message):
    __slots__ = ("account_id", "updated_by")
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    UPDATED_BY_FIELD_NUMBER: _ClassVar[int]
    account_id: str
    updated_by: str
    def __init__(self, account_id: _Optional[str] = ..., updated_by: _Optional[str] = ...) -> None: ...

class ChannelPlatformConfig(_message.Message):
    __slots__ = ("group_chat_disabled", "updated_by", "updated_at")
    GROUP_CHAT_DISABLED_FIELD_NUMBER: _ClassVar[int]
    UPDATED_BY_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    group_chat_disabled: bool
    updated_by: str
    updated_at: str
    def __init__(self, group_chat_disabled: _Optional[bool] = ..., updated_by: _Optional[str] = ..., updated_at: _Optional[str] = ...) -> None: ...

class SetChannelPlatformConfigRequest(_message.Message):
    __slots__ = ("group_chat_disabled", "updated_by", "update_mask")
    GROUP_CHAT_DISABLED_FIELD_NUMBER: _ClassVar[int]
    UPDATED_BY_FIELD_NUMBER: _ClassVar[int]
    UPDATE_MASK_FIELD_NUMBER: _ClassVar[int]
    group_chat_disabled: bool
    updated_by: str
    update_mask: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, group_chat_disabled: _Optional[bool] = ..., updated_by: _Optional[str] = ..., update_mask: _Optional[_Iterable[str]] = ...) -> None: ...
