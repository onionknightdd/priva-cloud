"""Re-export shim — the settings loader now lives in ``priva_common.config``.

Phase-0 extraction (code-split.md §6.1 step 1). Importers keep using
``api.services.config``; this forwards to the shared contract layer. Delete this
shim once the last ``api.*`` importer is gone.
"""
from priva_common.config import *  # noqa: F401,F403
