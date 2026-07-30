"""Per-Pod capability used only for the internal runtime drain endpoint.

The capability is stored in the tenant Deployment and injected into that
Deployment's runtime process. It is intentionally independent of the platform
service-identity signing key: an Operator which has just rotated signing keys
must still be able to close admission on a Pod which trusts the previous key.

A tenant can read its own Pod environment and therefore can drain only its own
runtime (a self-DoS it could already cause). The random value prevents one
tenant from draining another tenant's Pod.
"""

ENV = "PRIVA_INTERNAL_DRAIN_TOKEN"
HEADER = "X-Priva-Drain-Token"

__all__ = ["ENV", "HEADER"]
