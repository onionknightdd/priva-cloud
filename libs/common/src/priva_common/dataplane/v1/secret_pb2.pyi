from priva_common.dataplane.v1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class Secret(_message.Message):
    __slots__ = ("account_id", "bundle", "generation", "updated_at")
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    BUNDLE_FIELD_NUMBER: _ClassVar[int]
    GENERATION_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    account_id: str
    bundle: str
    generation: int
    updated_at: str
    def __init__(self, account_id: _Optional[str] = ..., bundle: _Optional[str] = ..., generation: _Optional[int] = ..., updated_at: _Optional[str] = ...) -> None: ...

class PutSecretRequest(_message.Message):
    __slots__ = ("account_id", "bundle")
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    BUNDLE_FIELD_NUMBER: _ClassVar[int]
    account_id: str
    bundle: str
    def __init__(self, account_id: _Optional[str] = ..., bundle: _Optional[str] = ...) -> None: ...

class GetSecretRequest(_message.Message):
    __slots__ = ("account_id",)
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    account_id: str
    def __init__(self, account_id: _Optional[str] = ...) -> None: ...

class SecretAccountList(_message.Message):
    __slots__ = ("account_ids",)
    ACCOUNT_IDS_FIELD_NUMBER: _ClassVar[int]
    account_ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, account_ids: _Optional[_Iterable[str]] = ...) -> None: ...
