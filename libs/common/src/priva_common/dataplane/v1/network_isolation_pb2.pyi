from priva_common.dataplane.v1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class NetworkIsolation(_message.Message):
    __slots__ = ("runner_deny_internal", "terminal_deny_internal", "deny_tenant_peers", "egress_mode", "egress_allowlist", "updated_at")
    RUNNER_DENY_INTERNAL_FIELD_NUMBER: _ClassVar[int]
    TERMINAL_DENY_INTERNAL_FIELD_NUMBER: _ClassVar[int]
    DENY_TENANT_PEERS_FIELD_NUMBER: _ClassVar[int]
    EGRESS_MODE_FIELD_NUMBER: _ClassVar[int]
    EGRESS_ALLOWLIST_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    runner_deny_internal: bool
    terminal_deny_internal: bool
    deny_tenant_peers: bool
    egress_mode: str
    egress_allowlist: _containers.RepeatedCompositeFieldContainer[EgressAllowEntry]
    updated_at: str
    def __init__(self, runner_deny_internal: _Optional[bool] = ..., terminal_deny_internal: _Optional[bool] = ..., deny_tenant_peers: _Optional[bool] = ..., egress_mode: _Optional[str] = ..., egress_allowlist: _Optional[_Iterable[_Union[EgressAllowEntry, _Mapping]]] = ..., updated_at: _Optional[str] = ...) -> None: ...

class EgressAllowEntry(_message.Message):
    __slots__ = ("host", "port")
    HOST_FIELD_NUMBER: _ClassVar[int]
    PORT_FIELD_NUMBER: _ClassVar[int]
    host: str
    port: int
    def __init__(self, host: _Optional[str] = ..., port: _Optional[int] = ...) -> None: ...

class SetNetworkIsolationRequest(_message.Message):
    __slots__ = ("runner_deny_internal", "terminal_deny_internal", "deny_tenant_peers", "egress_mode", "egress_allowlist", "update_mask")
    RUNNER_DENY_INTERNAL_FIELD_NUMBER: _ClassVar[int]
    TERMINAL_DENY_INTERNAL_FIELD_NUMBER: _ClassVar[int]
    DENY_TENANT_PEERS_FIELD_NUMBER: _ClassVar[int]
    EGRESS_MODE_FIELD_NUMBER: _ClassVar[int]
    EGRESS_ALLOWLIST_FIELD_NUMBER: _ClassVar[int]
    UPDATE_MASK_FIELD_NUMBER: _ClassVar[int]
    runner_deny_internal: bool
    terminal_deny_internal: bool
    deny_tenant_peers: bool
    egress_mode: str
    egress_allowlist: _containers.RepeatedCompositeFieldContainer[EgressAllowEntry]
    update_mask: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, runner_deny_internal: _Optional[bool] = ..., terminal_deny_internal: _Optional[bool] = ..., deny_tenant_peers: _Optional[bool] = ..., egress_mode: _Optional[str] = ..., egress_allowlist: _Optional[_Iterable[_Union[EgressAllowEntry, _Mapping]]] = ..., update_mask: _Optional[_Iterable[str]] = ...) -> None: ...
