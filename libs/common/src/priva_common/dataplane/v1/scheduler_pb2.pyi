from priva_common.dataplane.v1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Job(_message.Message):
    __slots__ = ("job_id", "account_id", "name", "prompt", "trigger", "job_type", "job_config", "timezone", "model", "status", "created_at", "updated_at")
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    PROMPT_FIELD_NUMBER: _ClassVar[int]
    TRIGGER_FIELD_NUMBER: _ClassVar[int]
    JOB_TYPE_FIELD_NUMBER: _ClassVar[int]
    JOB_CONFIG_FIELD_NUMBER: _ClassVar[int]
    TIMEZONE_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    account_id: str
    name: str
    prompt: str
    trigger: str
    job_type: str
    job_config: str
    timezone: str
    model: str
    status: str
    created_at: str
    updated_at: str
    def __init__(self, job_id: _Optional[str] = ..., account_id: _Optional[str] = ..., name: _Optional[str] = ..., prompt: _Optional[str] = ..., trigger: _Optional[str] = ..., job_type: _Optional[str] = ..., job_config: _Optional[str] = ..., timezone: _Optional[str] = ..., model: _Optional[str] = ..., status: _Optional[str] = ..., created_at: _Optional[str] = ..., updated_at: _Optional[str] = ...) -> None: ...

class JobRef(_message.Message):
    __slots__ = ("job_id",)
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    def __init__(self, job_id: _Optional[str] = ...) -> None: ...

class JobList(_message.Message):
    __slots__ = ("jobs",)
    JOBS_FIELD_NUMBER: _ClassVar[int]
    jobs: _containers.RepeatedCompositeFieldContainer[Job]
    def __init__(self, jobs: _Optional[_Iterable[_Union[Job, _Mapping]]] = ...) -> None: ...

class CreateJobRequest(_message.Message):
    __slots__ = ("account_id", "name", "prompt", "trigger", "job_type", "job_config", "timezone", "model", "status")
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    PROMPT_FIELD_NUMBER: _ClassVar[int]
    TRIGGER_FIELD_NUMBER: _ClassVar[int]
    JOB_TYPE_FIELD_NUMBER: _ClassVar[int]
    JOB_CONFIG_FIELD_NUMBER: _ClassVar[int]
    TIMEZONE_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    account_id: str
    name: str
    prompt: str
    trigger: str
    job_type: str
    job_config: str
    timezone: str
    model: str
    status: str
    def __init__(self, account_id: _Optional[str] = ..., name: _Optional[str] = ..., prompt: _Optional[str] = ..., trigger: _Optional[str] = ..., job_type: _Optional[str] = ..., job_config: _Optional[str] = ..., timezone: _Optional[str] = ..., model: _Optional[str] = ..., status: _Optional[str] = ...) -> None: ...

class UpdateJobRequest(_message.Message):
    __slots__ = ("job_id", "name", "prompt", "trigger", "job_type", "job_config", "timezone", "model", "status", "update_mask")
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    PROMPT_FIELD_NUMBER: _ClassVar[int]
    TRIGGER_FIELD_NUMBER: _ClassVar[int]
    JOB_TYPE_FIELD_NUMBER: _ClassVar[int]
    JOB_CONFIG_FIELD_NUMBER: _ClassVar[int]
    TIMEZONE_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    UPDATE_MASK_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    name: str
    prompt: str
    trigger: str
    job_type: str
    job_config: str
    timezone: str
    model: str
    status: str
    update_mask: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, job_id: _Optional[str] = ..., name: _Optional[str] = ..., prompt: _Optional[str] = ..., trigger: _Optional[str] = ..., job_type: _Optional[str] = ..., job_config: _Optional[str] = ..., timezone: _Optional[str] = ..., model: _Optional[str] = ..., status: _Optional[str] = ..., update_mask: _Optional[_Iterable[str]] = ...) -> None: ...

class SetJobStatusRequest(_message.Message):
    __slots__ = ("job_id", "status")
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    status: str
    def __init__(self, job_id: _Optional[str] = ..., status: _Optional[str] = ...) -> None: ...

class Run(_message.Message):
    __slots__ = ("run_id", "job_id", "job_name", "account_id", "session_id", "started_at", "finished_at", "status", "duration_ms", "is_error", "error_message", "num_turns", "result_summary")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    JOB_NAME_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    STARTED_AT_FIELD_NUMBER: _ClassVar[int]
    FINISHED_AT_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    IS_ERROR_FIELD_NUMBER: _ClassVar[int]
    ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    NUM_TURNS_FIELD_NUMBER: _ClassVar[int]
    RESULT_SUMMARY_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    job_id: str
    job_name: str
    account_id: str
    session_id: str
    started_at: str
    finished_at: str
    status: str
    duration_ms: int
    is_error: bool
    error_message: str
    num_turns: int
    result_summary: str
    def __init__(self, run_id: _Optional[str] = ..., job_id: _Optional[str] = ..., job_name: _Optional[str] = ..., account_id: _Optional[str] = ..., session_id: _Optional[str] = ..., started_at: _Optional[str] = ..., finished_at: _Optional[str] = ..., status: _Optional[str] = ..., duration_ms: _Optional[int] = ..., is_error: _Optional[bool] = ..., error_message: _Optional[str] = ..., num_turns: _Optional[int] = ..., result_summary: _Optional[str] = ...) -> None: ...

class StartRunRequest(_message.Message):
    __slots__ = ("run_id", "job_id", "job_name", "account_id", "session_id", "started_at", "status")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    JOB_NAME_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    STARTED_AT_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    job_id: str
    job_name: str
    account_id: str
    session_id: str
    started_at: str
    status: str
    def __init__(self, run_id: _Optional[str] = ..., job_id: _Optional[str] = ..., job_name: _Optional[str] = ..., account_id: _Optional[str] = ..., session_id: _Optional[str] = ..., started_at: _Optional[str] = ..., status: _Optional[str] = ...) -> None: ...

class FinishRunRequest(_message.Message):
    __slots__ = ("run_id", "finished_at", "status", "duration_ms", "is_error", "error_message", "num_turns", "result_summary")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    FINISHED_AT_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    IS_ERROR_FIELD_NUMBER: _ClassVar[int]
    ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    NUM_TURNS_FIELD_NUMBER: _ClassVar[int]
    RESULT_SUMMARY_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    finished_at: str
    status: str
    duration_ms: int
    is_error: bool
    error_message: str
    num_turns: int
    result_summary: str
    def __init__(self, run_id: _Optional[str] = ..., finished_at: _Optional[str] = ..., status: _Optional[str] = ..., duration_ms: _Optional[int] = ..., is_error: _Optional[bool] = ..., error_message: _Optional[str] = ..., num_turns: _Optional[int] = ..., result_summary: _Optional[str] = ...) -> None: ...

class ListRunsRequest(_message.Message):
    __slots__ = ("account_id", "limit", "before", "after", "job_id", "status")
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    BEFORE_FIELD_NUMBER: _ClassVar[int]
    AFTER_FIELD_NUMBER: _ClassVar[int]
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    account_id: str
    limit: int
    before: str
    after: str
    job_id: str
    status: str
    def __init__(self, account_id: _Optional[str] = ..., limit: _Optional[int] = ..., before: _Optional[str] = ..., after: _Optional[str] = ..., job_id: _Optional[str] = ..., status: _Optional[str] = ...) -> None: ...

class RunPage(_message.Message):
    __slots__ = ("runs", "next_cursor", "prev_cursor", "total")
    RUNS_FIELD_NUMBER: _ClassVar[int]
    NEXT_CURSOR_FIELD_NUMBER: _ClassVar[int]
    PREV_CURSOR_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    runs: _containers.RepeatedCompositeFieldContainer[Run]
    next_cursor: str
    prev_cursor: str
    total: int
    def __init__(self, runs: _Optional[_Iterable[_Union[Run, _Mapping]]] = ..., next_cursor: _Optional[str] = ..., prev_cursor: _Optional[str] = ..., total: _Optional[int] = ...) -> None: ...
