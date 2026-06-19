"""Re-export shim — pagination helpers now live in ``priva_common._pagination``.

Phase-0 extraction (code-split.md §6.1 step 4). Importers keep using
``api.services._pagination``; this forwards to the shared contract layer. Delete
this shim once the last ``api.*`` importer is gone.
"""
from priva_common._pagination import *  # noqa: F401,F403
