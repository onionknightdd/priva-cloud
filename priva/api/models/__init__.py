"""Re-export shim — shared DTOs now live in ``priva_common.models``.

Phase-0 extraction (code-split.md §6.1 step 5; §4.7 LIFT). Importers keep using
``api.models`` and ``api.models.<submodule>``; each path forwards to the shared
contract layer. Delete these shims once the last ``api.*`` importer is gone.
"""
from priva_common.models import *  # noqa: F401,F403
