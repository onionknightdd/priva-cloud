"""priva_common.dataplane — the one shared data-plane client.

Import surface for every service:
    from priva_common.dataplane import get_client
    client = get_client()
    user = client.accounts.get_by_username("alice")

Composition root (monolith startup / data-spine server) registers the
in-process implementation:
    from priva_common.dataplane import set_inprocess_handlers

Generated gRPC stubs live in `priva_common.dataplane.v1` (loaded only by the
deferred gRPC transport). This package imports no service — services depend on
it, never the reverse (code-split.md §6).
"""

from __future__ import annotations

from priva_common.dataplane.client import (
    AccountClient,
    AdminClient,
    BindingClient,
    BindingRecord,
    DataplaneClient,
    PendingRegistrationRecord,
    QuotaClient,
    QuotaRecord,
    RegistrationClient,
    ResourceSpecClient,
    ResourceSpecRecord,
    RunnerDefaultsClient,
    RunnerDefaultsRecord,
    RunPage,
    SchedulerClient,
    SecretClient,
    SecretRecord,
    UNSET,
)
from priva_common.dataplane.factory import get_client, set_inprocess_handlers

__all__ = [
    "get_client",
    "set_inprocess_handlers",
    "DataplaneClient",
    "AccountClient",
    "BindingClient",
    "QuotaClient",
    "SchedulerClient",
    "AdminClient",
    "SecretClient",
    "ResourceSpecClient",
    "RunnerDefaultsClient",
    "RegistrationClient",
    "BindingRecord",
    "QuotaRecord",
    "RunPage",
    "SecretRecord",
    "ResourceSpecRecord",
    "RunnerDefaultsRecord",
    "PendingRegistrationRecord",
    "UNSET",
]
