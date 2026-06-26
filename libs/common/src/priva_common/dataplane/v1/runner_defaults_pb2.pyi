from priva_common.dataplane.v1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class RunnerDefaults(_message.Message):
    __slots__ = ("idle_grace_seconds", "min_alive_after_wake_seconds", "cpu_cores", "memory_mb", "storage_gb", "runner_image", "updated_at")
    IDLE_GRACE_SECONDS_FIELD_NUMBER: _ClassVar[int]
    MIN_ALIVE_AFTER_WAKE_SECONDS_FIELD_NUMBER: _ClassVar[int]
    CPU_CORES_FIELD_NUMBER: _ClassVar[int]
    MEMORY_MB_FIELD_NUMBER: _ClassVar[int]
    STORAGE_GB_FIELD_NUMBER: _ClassVar[int]
    RUNNER_IMAGE_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    idle_grace_seconds: int
    min_alive_after_wake_seconds: int
    cpu_cores: float
    memory_mb: int
    storage_gb: int
    runner_image: str
    updated_at: str
    def __init__(self, idle_grace_seconds: _Optional[int] = ..., min_alive_after_wake_seconds: _Optional[int] = ..., cpu_cores: _Optional[float] = ..., memory_mb: _Optional[int] = ..., storage_gb: _Optional[int] = ..., runner_image: _Optional[str] = ..., updated_at: _Optional[str] = ...) -> None: ...

class SetRunnerDefaultsRequest(_message.Message):
    __slots__ = ("idle_grace_seconds", "min_alive_after_wake_seconds", "cpu_cores", "memory_mb", "storage_gb", "runner_image", "update_mask")
    IDLE_GRACE_SECONDS_FIELD_NUMBER: _ClassVar[int]
    MIN_ALIVE_AFTER_WAKE_SECONDS_FIELD_NUMBER: _ClassVar[int]
    CPU_CORES_FIELD_NUMBER: _ClassVar[int]
    MEMORY_MB_FIELD_NUMBER: _ClassVar[int]
    STORAGE_GB_FIELD_NUMBER: _ClassVar[int]
    RUNNER_IMAGE_FIELD_NUMBER: _ClassVar[int]
    UPDATE_MASK_FIELD_NUMBER: _ClassVar[int]
    idle_grace_seconds: int
    min_alive_after_wake_seconds: int
    cpu_cores: float
    memory_mb: int
    storage_gb: int
    runner_image: str
    update_mask: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, idle_grace_seconds: _Optional[int] = ..., min_alive_after_wake_seconds: _Optional[int] = ..., cpu_cores: _Optional[float] = ..., memory_mb: _Optional[int] = ..., storage_gb: _Optional[int] = ..., runner_image: _Optional[str] = ..., update_mask: _Optional[_Iterable[str]] = ...) -> None: ...
