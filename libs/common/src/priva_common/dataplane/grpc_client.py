"""gRPC transport — structured but NOT implemented in Phase 1.

`grpc` is imported lazily here so the in-process transport never loads it. When
K8s/scale arrives, build a DataplaneClient whose sub-services marshal the DTOs
to the generated pb2 messages and dial `settings.dataspine.grpc_dsn`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from priva_common.config import Settings
    from priva_common.dataplane.client import DataplaneClient


def build_grpc_client(settings: "Settings") -> "DataplaneClient":
    import grpc  # noqa: F401  (lazy; proves the dep is present, fails clearly if not)

    raise NotImplementedError(
        "data-spine gRPC transport is structured but not implemented in Phase 1; "
        "set dataspine.transport='in_process'"
    )
