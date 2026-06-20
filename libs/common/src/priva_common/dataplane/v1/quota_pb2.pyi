from priva_common.dataplane.v1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class Quota(_message.Message):
    __slots__ = ("account_id", "tier", "max_concurrent_sessions", "idle_grace_seconds", "updated_at")
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    TIER_FIELD_NUMBER: _ClassVar[int]
    MAX_CONCURRENT_SESSIONS_FIELD_NUMBER: _ClassVar[int]
    IDLE_GRACE_SECONDS_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    account_id: str
    tier: str
    max_concurrent_sessions: int
    idle_grace_seconds: int
    updated_at: str
    def __init__(self, account_id: _Optional[str] = ..., tier: _Optional[str] = ..., max_concurrent_sessions: _Optional[int] = ..., idle_grace_seconds: _Optional[int] = ..., updated_at: _Optional[str] = ...) -> None: ...

class SetQuotaRequest(_message.Message):
    __slots__ = ("account_id", "tier", "max_concurrent_sessions", "idle_grace_seconds", "update_mask")
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    TIER_FIELD_NUMBER: _ClassVar[int]
    MAX_CONCURRENT_SESSIONS_FIELD_NUMBER: _ClassVar[int]
    IDLE_GRACE_SECONDS_FIELD_NUMBER: _ClassVar[int]
    UPDATE_MASK_FIELD_NUMBER: _ClassVar[int]
    account_id: str
    tier: str
    max_concurrent_sessions: int
    idle_grace_seconds: int
    update_mask: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, account_id: _Optional[str] = ..., tier: _Optional[str] = ..., max_concurrent_sessions: _Optional[int] = ..., idle_grace_seconds: _Optional[int] = ..., update_mask: _Optional[_Iterable[str]] = ...) -> None: ...
