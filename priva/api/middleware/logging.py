"""Re-export shim — logging/observability now lives in ``priva_common.logging``.

Phase-0 extraction (code-split.md §6.1 step 3). Importers keep using
``api.middleware.logging``; this forwards to the shared contract layer. Delete
this shim once the last ``api.*`` importer is gone.
"""
from priva_common.logging import *  # noqa: F401,F403
