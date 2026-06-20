"""Per-user workspace path resolution.

Extracted from ``api/services/auth.py`` (Phase 2, code-split.md §6) so both
agent-runner and control-panel resolve the same ``$work_dir/<username>`` tree
without importing each other. Keeps the original ``UserRecord | None`` signature
(``None`` -> the shared ``anonymous`` workspace).
"""

from __future__ import annotations

import os

from .config import get_settings
from .models.auth import UserRecord


def get_user_workspace(user: UserRecord | None) -> str:
    settings = get_settings()
    base = os.path.expanduser(settings.server.work_dir)
    if user is None:
        workspace = os.path.join(base, "anonymous")
    else:
        workspace = os.path.join(base, user.username)
    os.makedirs(workspace, exist_ok=True)
    return workspace
