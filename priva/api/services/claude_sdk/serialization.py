"""Re-export shim — the wire serializer now lives in ``priva_common.serialization``.

Phase-0 extraction (code-split.md §6.1 step 6; §4.1 LIFT — the wire format is
shared pod↔connector). Importers keep using
``api.services.claude_sdk.serialization``; this forwards to the shared contract
layer. Delete this shim once the last ``api.*`` importer is gone.
"""
from priva_common.serialization import *  # noqa: F401,F403
