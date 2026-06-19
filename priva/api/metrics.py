"""Re-export shim — Prometheus metrics now live in ``priva_common.metrics``.

Phase-0 extraction (code-split.md §6.1 step 2). Importers keep using
``api.metrics``; this forwards the same metric singletons from the shared
contract layer. Delete this shim once the last ``api.*`` importer is gone.
"""
from priva_common.metrics import *  # noqa: F401,F403
