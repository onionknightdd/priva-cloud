from priva_common.dataplane.v1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ResourceSpec(_message.Message):
    __slots__ = ("account_id", "cpu_cores", "memory_mb", "volume_gb", "updated_at")
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    CPU_CORES_FIELD_NUMBER: _ClassVar[int]
    MEMORY_MB_FIELD_NUMBER: _ClassVar[int]
    VOLUME_GB_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    account_id: str
    cpu_cores: float
    memory_mb: int
    volume_gb: int
    updated_at: str
    def __init__(self, account_id: _Optional[str] = ..., cpu_cores: _Optional[float] = ..., memory_mb: _Optional[int] = ..., volume_gb: _Optional[int] = ..., updated_at: _Optional[str] = ...) -> None: ...

class SetResourceSpecRequest(_message.Message):
    __slots__ = ("account_id", "cpu_cores", "memory_mb", "volume_gb", "update_mask")
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    CPU_CORES_FIELD_NUMBER: _ClassVar[int]
    MEMORY_MB_FIELD_NUMBER: _ClassVar[int]
    VOLUME_GB_FIELD_NUMBER: _ClassVar[int]
    UPDATE_MASK_FIELD_NUMBER: _ClassVar[int]
    account_id: str
    cpu_cores: float
    memory_mb: int
    volume_gb: int
    update_mask: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, account_id: _Optional[str] = ..., cpu_cores: _Optional[float] = ..., memory_mb: _Optional[int] = ..., volume_gb: _Optional[int] = ..., update_mask: _Optional[_Iterable[str]] = ...) -> None: ...

class ResourceSpecList(_message.Message):
    __slots__ = ("specs",)
    SPECS_FIELD_NUMBER: _ClassVar[int]
    specs: _containers.RepeatedCompositeFieldContainer[ResourceSpec]
    def __init__(self, specs: _Optional[_Iterable[_Union[ResourceSpec, _Mapping]]] = ...) -> None: ...
