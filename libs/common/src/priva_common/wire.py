"""Wire-format constants shared across services (Phase-0 §6.1 step 6).

``serialization`` is the pod↔connector wire format; constants it shares with the
agent runner (e.g. the synthetic-record sentinel) live here so neither side
imports the other's package. ``priva_common`` imports no service (§6 boundary).
"""
from __future__ import annotations

# Marker model name stamped on synthetic transcript records that the retry layer
# injects and later strips. Defined here (not in the pod's claude_sdk.retry) so
# the shared serializer carries no dependency on a service package.
SYNTHETIC_MODEL = "<synthetic>"
