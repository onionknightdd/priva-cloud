from priva_common.dataplane.v1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class RunnerDefaults(_message.Message):
    __slots__ = ("idle_grace_seconds", "min_alive_after_wake_seconds", "cpu_cores", "memory_mb", "storage_gb", "runner_image", "updated_at", "terminal_resource_percent", "terminal_max_sessions", "terminal_idle_timeout_seconds", "terminal_max_lifetime_seconds", "terminal_scale_down_grace_seconds")
    IDLE_GRACE_SECONDS_FIELD_NUMBER: _ClassVar[int]
    MIN_ALIVE_AFTER_WAKE_SECONDS_FIELD_NUMBER: _ClassVar[int]
    CPU_CORES_FIELD_NUMBER: _ClassVar[int]
    MEMORY_MB_FIELD_NUMBER: _ClassVar[int]
    STORAGE_GB_FIELD_NUMBER: _ClassVar[int]
    RUNNER_IMAGE_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    TERMINAL_RESOURCE_PERCENT_FIELD_NUMBER: _ClassVar[int]
    TERMINAL_MAX_SESSIONS_FIELD_NUMBER: _ClassVar[int]
    TERMINAL_IDLE_TIMEOUT_SECONDS_FIELD_NUMBER: _ClassVar[int]
    TERMINAL_MAX_LIFETIME_SECONDS_FIELD_NUMBER: _ClassVar[int]
    TERMINAL_SCALE_DOWN_GRACE_SECONDS_FIELD_NUMBER: _ClassVar[int]
    idle_grace_seconds: int
    min_alive_after_wake_seconds: int
    cpu_cores: float
    memory_mb: int
    storage_gb: int
    runner_image: str
    updated_at: str
    terminal_resource_percent: int
    terminal_max_sessions: int
    terminal_idle_timeout_seconds: int
    terminal_max_lifetime_seconds: int
    terminal_scale_down_grace_seconds: int
    def __init__(self, idle_grace_seconds: _Optional[int] = ..., min_alive_after_wake_seconds: _Optional[int] = ..., cpu_cores: _Optional[float] = ..., memory_mb: _Optional[int] = ..., storage_gb: _Optional[int] = ..., runner_image: _Optional[str] = ..., updated_at: _Optional[str] = ..., terminal_resource_percent: _Optional[int] = ..., terminal_max_sessions: _Optional[int] = ..., terminal_idle_timeout_seconds: _Optional[int] = ..., terminal_max_lifetime_seconds: _Optional[int] = ..., terminal_scale_down_grace_seconds: _Optional[int] = ...) -> None: ...

class SetRunnerDefaultsRequest(_message.Message):
    __slots__ = ("idle_grace_seconds", "min_alive_after_wake_seconds", "cpu_cores", "memory_mb", "storage_gb", "runner_image", "update_mask", "terminal_resource_percent", "terminal_max_sessions", "terminal_idle_timeout_seconds", "terminal_max_lifetime_seconds", "terminal_scale_down_grace_seconds")
    IDLE_GRACE_SECONDS_FIELD_NUMBER: _ClassVar[int]
    MIN_ALIVE_AFTER_WAKE_SECONDS_FIELD_NUMBER: _ClassVar[int]
    CPU_CORES_FIELD_NUMBER: _ClassVar[int]
    MEMORY_MB_FIELD_NUMBER: _ClassVar[int]
    STORAGE_GB_FIELD_NUMBER: _ClassVar[int]
    RUNNER_IMAGE_FIELD_NUMBER: _ClassVar[int]
    UPDATE_MASK_FIELD_NUMBER: _ClassVar[int]
    TERMINAL_RESOURCE_PERCENT_FIELD_NUMBER: _ClassVar[int]
    TERMINAL_MAX_SESSIONS_FIELD_NUMBER: _ClassVar[int]
    TERMINAL_IDLE_TIMEOUT_SECONDS_FIELD_NUMBER: _ClassVar[int]
    TERMINAL_MAX_LIFETIME_SECONDS_FIELD_NUMBER: _ClassVar[int]
    TERMINAL_SCALE_DOWN_GRACE_SECONDS_FIELD_NUMBER: _ClassVar[int]
    idle_grace_seconds: int
    min_alive_after_wake_seconds: int
    cpu_cores: float
    memory_mb: int
    storage_gb: int
    runner_image: str
    update_mask: _containers.RepeatedScalarFieldContainer[str]
    terminal_resource_percent: int
    terminal_max_sessions: int
    terminal_idle_timeout_seconds: int
    terminal_max_lifetime_seconds: int
    terminal_scale_down_grace_seconds: int
    def __init__(self, idle_grace_seconds: _Optional[int] = ..., min_alive_after_wake_seconds: _Optional[int] = ..., cpu_cores: _Optional[float] = ..., memory_mb: _Optional[int] = ..., storage_gb: _Optional[int] = ..., runner_image: _Optional[str] = ..., update_mask: _Optional[_Iterable[str]] = ..., terminal_resource_percent: _Optional[int] = ..., terminal_max_sessions: _Optional[int] = ..., terminal_idle_timeout_seconds: _Optional[int] = ..., terminal_max_lifetime_seconds: _Optional[int] = ..., terminal_scale_down_grace_seconds: _Optional[int] = ...) -> None: ...
