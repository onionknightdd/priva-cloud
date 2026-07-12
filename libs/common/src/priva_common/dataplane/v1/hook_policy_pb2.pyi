from priva_common.dataplane.v1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class HookPolicy(_message.Message):
    __slots__ = ("id", "hook_type", "name", "description", "events", "matcher", "timeout_seconds", "interpreter", "script_body", "content_hash", "url", "headers_json", "allowed_env_vars", "mcp_server", "mcp_tool", "enabled", "enforced", "default_on", "predefined", "seed_version", "target", "updated_at", "updated_by", "enforced_events")
    ID_FIELD_NUMBER: _ClassVar[int]
    HOOK_TYPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    EVENTS_FIELD_NUMBER: _ClassVar[int]
    MATCHER_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_SECONDS_FIELD_NUMBER: _ClassVar[int]
    INTERPRETER_FIELD_NUMBER: _ClassVar[int]
    SCRIPT_BODY_FIELD_NUMBER: _ClassVar[int]
    CONTENT_HASH_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    HEADERS_JSON_FIELD_NUMBER: _ClassVar[int]
    ALLOWED_ENV_VARS_FIELD_NUMBER: _ClassVar[int]
    MCP_SERVER_FIELD_NUMBER: _ClassVar[int]
    MCP_TOOL_FIELD_NUMBER: _ClassVar[int]
    ENABLED_FIELD_NUMBER: _ClassVar[int]
    ENFORCED_FIELD_NUMBER: _ClassVar[int]
    DEFAULT_ON_FIELD_NUMBER: _ClassVar[int]
    PREDEFINED_FIELD_NUMBER: _ClassVar[int]
    SEED_VERSION_FIELD_NUMBER: _ClassVar[int]
    TARGET_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_BY_FIELD_NUMBER: _ClassVar[int]
    ENFORCED_EVENTS_FIELD_NUMBER: _ClassVar[int]
    id: str
    hook_type: str
    name: str
    description: str
    events: _containers.RepeatedScalarFieldContainer[str]
    matcher: str
    timeout_seconds: int
    interpreter: str
    script_body: str
    content_hash: str
    url: str
    headers_json: str
    allowed_env_vars: _containers.RepeatedScalarFieldContainer[str]
    mcp_server: str
    mcp_tool: str
    enabled: bool
    enforced: bool
    default_on: bool
    predefined: bool
    seed_version: int
    target: str
    updated_at: str
    updated_by: str
    enforced_events: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, id: _Optional[str] = ..., hook_type: _Optional[str] = ..., name: _Optional[str] = ..., description: _Optional[str] = ..., events: _Optional[_Iterable[str]] = ..., matcher: _Optional[str] = ..., timeout_seconds: _Optional[int] = ..., interpreter: _Optional[str] = ..., script_body: _Optional[str] = ..., content_hash: _Optional[str] = ..., url: _Optional[str] = ..., headers_json: _Optional[str] = ..., allowed_env_vars: _Optional[_Iterable[str]] = ..., mcp_server: _Optional[str] = ..., mcp_tool: _Optional[str] = ..., enabled: _Optional[bool] = ..., enforced: _Optional[bool] = ..., default_on: _Optional[bool] = ..., predefined: _Optional[bool] = ..., seed_version: _Optional[int] = ..., target: _Optional[str] = ..., updated_at: _Optional[str] = ..., updated_by: _Optional[str] = ..., enforced_events: _Optional[_Iterable[str]] = ...) -> None: ...

class HookPolicyList(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[HookPolicy]
    def __init__(self, items: _Optional[_Iterable[_Union[HookPolicy, _Mapping]]] = ...) -> None: ...

class ListHookPoliciesRequest(_message.Message):
    __slots__ = ("enabled_only",)
    ENABLED_ONLY_FIELD_NUMBER: _ClassVar[int]
    enabled_only: bool
    def __init__(self, enabled_only: _Optional[bool] = ...) -> None: ...

class HookPolicyRef(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class UpsertHookPolicyRequest(_message.Message):
    __slots__ = ("policy", "update_mask", "expect")
    POLICY_FIELD_NUMBER: _ClassVar[int]
    UPDATE_MASK_FIELD_NUMBER: _ClassVar[int]
    EXPECT_FIELD_NUMBER: _ClassVar[int]
    policy: HookPolicy
    update_mask: _containers.RepeatedScalarFieldContainer[str]
    expect: str
    def __init__(self, policy: _Optional[_Union[HookPolicy, _Mapping]] = ..., update_mask: _Optional[_Iterable[str]] = ..., expect: _Optional[str] = ...) -> None: ...
