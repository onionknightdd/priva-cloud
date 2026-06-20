from priva_common.dataplane.v1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Binding(_message.Message):
    __slots__ = ("binding_id", "account_id", "session_uuid", "first_run_done", "feishu_chat_id", "bound_at", "rebound_at")
    BINDING_ID_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    SESSION_UUID_FIELD_NUMBER: _ClassVar[int]
    FIRST_RUN_DONE_FIELD_NUMBER: _ClassVar[int]
    FEISHU_CHAT_ID_FIELD_NUMBER: _ClassVar[int]
    BOUND_AT_FIELD_NUMBER: _ClassVar[int]
    REBOUND_AT_FIELD_NUMBER: _ClassVar[int]
    binding_id: str
    account_id: str
    session_uuid: str
    first_run_done: bool
    feishu_chat_id: str
    bound_at: str
    rebound_at: str
    def __init__(self, binding_id: _Optional[str] = ..., account_id: _Optional[str] = ..., session_uuid: _Optional[str] = ..., first_run_done: _Optional[bool] = ..., feishu_chat_id: _Optional[str] = ..., bound_at: _Optional[str] = ..., rebound_at: _Optional[str] = ...) -> None: ...

class BindingRef(_message.Message):
    __slots__ = ("binding_id",)
    BINDING_ID_FIELD_NUMBER: _ClassVar[int]
    binding_id: str
    def __init__(self, binding_id: _Optional[str] = ...) -> None: ...

class BindingList(_message.Message):
    __slots__ = ("bindings",)
    BINDINGS_FIELD_NUMBER: _ClassVar[int]
    bindings: _containers.RepeatedCompositeFieldContainer[Binding]
    def __init__(self, bindings: _Optional[_Iterable[_Union[Binding, _Mapping]]] = ...) -> None: ...

class BindRequest(_message.Message):
    __slots__ = ("account_id", "session_uuid", "feishu_chat_id")
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    SESSION_UUID_FIELD_NUMBER: _ClassVar[int]
    FEISHU_CHAT_ID_FIELD_NUMBER: _ClassVar[int]
    account_id: str
    session_uuid: str
    feishu_chat_id: str
    def __init__(self, account_id: _Optional[str] = ..., session_uuid: _Optional[str] = ..., feishu_chat_id: _Optional[str] = ...) -> None: ...

class RebindRequest(_message.Message):
    __slots__ = ("account_id", "session_uuid", "feishu_chat_id")
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    SESSION_UUID_FIELD_NUMBER: _ClassVar[int]
    FEISHU_CHAT_ID_FIELD_NUMBER: _ClassVar[int]
    account_id: str
    session_uuid: str
    feishu_chat_id: str
    def __init__(self, account_id: _Optional[str] = ..., session_uuid: _Optional[str] = ..., feishu_chat_id: _Optional[str] = ...) -> None: ...
