from priva_common.dataplane.v1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Account(_message.Message):
    __slots__ = ("account_id", "username", "role", "status", "api_key", "feishu_user_id", "feishu_display_name", "created_at", "updated_at", "agent_runner_type")
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    USERNAME_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    API_KEY_FIELD_NUMBER: _ClassVar[int]
    FEISHU_USER_ID_FIELD_NUMBER: _ClassVar[int]
    FEISHU_DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    AGENT_RUNNER_TYPE_FIELD_NUMBER: _ClassVar[int]
    account_id: str
    username: str
    role: str
    status: str
    api_key: str
    feishu_user_id: str
    feishu_display_name: str
    created_at: str
    updated_at: str
    agent_runner_type: str
    def __init__(self, account_id: _Optional[str] = ..., username: _Optional[str] = ..., role: _Optional[str] = ..., status: _Optional[str] = ..., api_key: _Optional[str] = ..., feishu_user_id: _Optional[str] = ..., feishu_display_name: _Optional[str] = ..., created_at: _Optional[str] = ..., updated_at: _Optional[str] = ..., agent_runner_type: _Optional[str] = ...) -> None: ...

class UsernameRef(_message.Message):
    __slots__ = ("username",)
    USERNAME_FIELD_NUMBER: _ClassVar[int]
    username: str
    def __init__(self, username: _Optional[str] = ...) -> None: ...

class FeishuRef(_message.Message):
    __slots__ = ("feishu_user_id",)
    FEISHU_USER_ID_FIELD_NUMBER: _ClassVar[int]
    feishu_user_id: str
    def __init__(self, feishu_user_id: _Optional[str] = ...) -> None: ...

class ApiKeyRequest(_message.Message):
    __slots__ = ("api_key",)
    API_KEY_FIELD_NUMBER: _ClassVar[int]
    api_key: str
    def __init__(self, api_key: _Optional[str] = ...) -> None: ...

class AccountList(_message.Message):
    __slots__ = ("accounts",)
    ACCOUNTS_FIELD_NUMBER: _ClassVar[int]
    accounts: _containers.RepeatedCompositeFieldContainer[Account]
    def __init__(self, accounts: _Optional[_Iterable[_Union[Account, _Mapping]]] = ...) -> None: ...

class CreateAccountRequest(_message.Message):
    __slots__ = ("username", "password", "role", "agent_runner_type", "password_hash")
    USERNAME_FIELD_NUMBER: _ClassVar[int]
    PASSWORD_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    AGENT_RUNNER_TYPE_FIELD_NUMBER: _ClassVar[int]
    PASSWORD_HASH_FIELD_NUMBER: _ClassVar[int]
    username: str
    password: str
    role: str
    agent_runner_type: str
    password_hash: str
    def __init__(self, username: _Optional[str] = ..., password: _Optional[str] = ..., role: _Optional[str] = ..., agent_runner_type: _Optional[str] = ..., password_hash: _Optional[str] = ...) -> None: ...

class UpdateAccountRequest(_message.Message):
    __slots__ = ("account_id", "password", "role", "api_key", "status", "feishu_user_id", "feishu_display_name", "update_mask", "agent_runner_type")
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    PASSWORD_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    API_KEY_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    FEISHU_USER_ID_FIELD_NUMBER: _ClassVar[int]
    FEISHU_DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    UPDATE_MASK_FIELD_NUMBER: _ClassVar[int]
    AGENT_RUNNER_TYPE_FIELD_NUMBER: _ClassVar[int]
    account_id: str
    password: str
    role: str
    api_key: str
    status: str
    feishu_user_id: str
    feishu_display_name: str
    update_mask: _containers.RepeatedScalarFieldContainer[str]
    agent_runner_type: str
    def __init__(self, account_id: _Optional[str] = ..., password: _Optional[str] = ..., role: _Optional[str] = ..., api_key: _Optional[str] = ..., status: _Optional[str] = ..., feishu_user_id: _Optional[str] = ..., feishu_display_name: _Optional[str] = ..., update_mask: _Optional[_Iterable[str]] = ..., agent_runner_type: _Optional[str] = ...) -> None: ...

class VerifyPasswordRequest(_message.Message):
    __slots__ = ("username", "password")
    USERNAME_FIELD_NUMBER: _ClassVar[int]
    PASSWORD_FIELD_NUMBER: _ClassVar[int]
    username: str
    password: str
    def __init__(self, username: _Optional[str] = ..., password: _Optional[str] = ...) -> None: ...
