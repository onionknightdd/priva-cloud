from priva_common.dataplane.v1 import common_pb2 as _common_pb2
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class Health(_message.Message):
    __slots__ = ("status",)
    STATUS_FIELD_NUMBER: _ClassVar[int]
    status: str
    def __init__(self, status: _Optional[str] = ...) -> None: ...

class Ready(_message.Message):
    __slots__ = ("ready", "detail")
    READY_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    ready: bool
    detail: str
    def __init__(self, ready: _Optional[bool] = ..., detail: _Optional[str] = ...) -> None: ...

class StatsResponse(_message.Message):
    __slots__ = ("accounts", "jobs", "runs")
    ACCOUNTS_FIELD_NUMBER: _ClassVar[int]
    JOBS_FIELD_NUMBER: _ClassVar[int]
    RUNS_FIELD_NUMBER: _ClassVar[int]
    accounts: int
    jobs: int
    runs: int
    def __init__(self, accounts: _Optional[int] = ..., jobs: _Optional[int] = ..., runs: _Optional[int] = ...) -> None: ...
