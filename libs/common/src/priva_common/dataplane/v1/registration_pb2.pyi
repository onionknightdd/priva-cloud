from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class PendingRegistration(_message.Message):
    __slots__ = ("request_id", "username", "display_name", "runner_type", "cpu_cores", "memory_mb", "volume_gb", "note", "status", "created_at", "updated_at", "password_hash")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    USERNAME_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    RUNNER_TYPE_FIELD_NUMBER: _ClassVar[int]
    CPU_CORES_FIELD_NUMBER: _ClassVar[int]
    MEMORY_MB_FIELD_NUMBER: _ClassVar[int]
    VOLUME_GB_FIELD_NUMBER: _ClassVar[int]
    NOTE_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    PASSWORD_HASH_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    username: str
    display_name: str
    runner_type: str
    cpu_cores: float
    memory_mb: int
    volume_gb: int
    note: str
    status: str
    created_at: str
    updated_at: str
    password_hash: str
    def __init__(self, request_id: _Optional[str] = ..., username: _Optional[str] = ..., display_name: _Optional[str] = ..., runner_type: _Optional[str] = ..., cpu_cores: _Optional[float] = ..., memory_mb: _Optional[int] = ..., volume_gb: _Optional[int] = ..., note: _Optional[str] = ..., status: _Optional[str] = ..., created_at: _Optional[str] = ..., updated_at: _Optional[str] = ..., password_hash: _Optional[str] = ...) -> None: ...

class CreatePendingRequest(_message.Message):
    __slots__ = ("username", "password_hash", "display_name", "runner_type", "cpu_cores", "memory_mb", "volume_gb", "note")
    USERNAME_FIELD_NUMBER: _ClassVar[int]
    PASSWORD_HASH_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    RUNNER_TYPE_FIELD_NUMBER: _ClassVar[int]
    CPU_CORES_FIELD_NUMBER: _ClassVar[int]
    MEMORY_MB_FIELD_NUMBER: _ClassVar[int]
    VOLUME_GB_FIELD_NUMBER: _ClassVar[int]
    NOTE_FIELD_NUMBER: _ClassVar[int]
    username: str
    password_hash: str
    display_name: str
    runner_type: str
    cpu_cores: float
    memory_mb: int
    volume_gb: int
    note: str
    def __init__(self, username: _Optional[str] = ..., password_hash: _Optional[str] = ..., display_name: _Optional[str] = ..., runner_type: _Optional[str] = ..., cpu_cores: _Optional[float] = ..., memory_mb: _Optional[int] = ..., volume_gb: _Optional[int] = ..., note: _Optional[str] = ...) -> None: ...

class StatusRef(_message.Message):
    __slots__ = ("status",)
    STATUS_FIELD_NUMBER: _ClassVar[int]
    status: str
    def __init__(self, status: _Optional[str] = ...) -> None: ...

class PendingRef(_message.Message):
    __slots__ = ("request_id",)
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    def __init__(self, request_id: _Optional[str] = ...) -> None: ...

class SetStatusRequest(_message.Message):
    __slots__ = ("request_id", "status")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    status: str
    def __init__(self, request_id: _Optional[str] = ..., status: _Optional[str] = ...) -> None: ...

class PendingList(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[PendingRegistration]
    def __init__(self, items: _Optional[_Iterable[_Union[PendingRegistration, _Mapping]]] = ...) -> None: ...
