"""Re-export shim — envelope crypto now lives in ``priva_common.crypto``.

Phase-0 extraction (code-split.md §6.1 step 4). Importers keep using
``api.utils.crypto``; this forwards to the shared contract layer. Delete this
shim once the last ``api.*`` importer is gone.
"""
from priva_common.crypto import *  # noqa: F401,F403
