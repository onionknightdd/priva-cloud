"""priva_data_spine — the data-spine service implementation.

Backs the priva_common.dataplane contract with SQLite (Phase 1, in-process).
The composition root (monolith startup) calls `compose()` once to build the
repo + service impls and register them as the in-process DataplaneClient.

    from priva_data_spine import compose
    compose()  # then priva_common.dataplane.get_client() works

Depends on priva_common; never the reverse (code-split.md §6).
"""

from __future__ import annotations

from priva_data_spine.repo import Repository, SqliteRepo
from priva_data_spine.service import (
    build_inprocess_client,
    build_repo,
    compose,
)

__all__ = ["compose", "build_repo", "build_inprocess_client", "Repository", "SqliteRepo"]
