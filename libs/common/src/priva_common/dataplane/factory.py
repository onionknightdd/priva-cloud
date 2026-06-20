"""Transport selection + the in-process handler registry.

The in-process client cannot be imported by `priva_common` (that would import a
service and break the §6 boundary). Instead the composition root (the monolith
at startup, or data-spine's own server) builds the service impls and registers
the assembled DataplaneClient here via `set_inprocess_handlers`. Swapped
call-sites then call `get_client()`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from priva_common.config import Settings
    from priva_common.dataplane.client import DataplaneClient

_inprocess: "DataplaneClient | None" = None


def set_inprocess_handlers(client: "DataplaneClient") -> None:
    """Register the assembled in-process DataplaneClient (called once at startup)."""
    global _inprocess
    _inprocess = client


def get_client(settings: "Settings | None" = None) -> "DataplaneClient":
    from priva_common.config import get_settings

    s = settings or get_settings()
    if s.dataspine.transport == "grpc":
        from priva_common.dataplane.grpc_client import build_grpc_client

        return build_grpc_client(s)
    # in_process
    if _inprocess is None:
        raise RuntimeError(
            "data-plane in-process handlers not registered — call "
            "priva_common.dataplane.set_inprocess_handlers(...) at startup "
            "(e.g. priva_data_spine.compose())"
        )
    return _inprocess
