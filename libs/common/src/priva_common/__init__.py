"""priva_common — the shared contract layer for Priva Cloud services.

Holds code that crosses service boundaries and must have exactly one home:
the agent wire format (serialization), shared models/DTOs, the data-plane
gRPC client, the Redis catalog key definitions, crypto, the settings loader,
and observability helpers.

Phase-0 extraction target — see docs/architecture/code-split.md §6.

Rule: this package MUST NOT import any service package. Services depend on
priva_common; never the reverse.
"""

__version__ = "0.0.0"
