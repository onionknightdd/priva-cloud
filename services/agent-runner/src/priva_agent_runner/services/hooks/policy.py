"""Admin hook-policy snapshot (runner side).

As of D6 the runner no longer materializes admin scripts or injects programmatic
callbacks — admin hooks are delivered NATIVELY via the operator-rendered
managed-policy ConfigMap (``/etc/claude-code``) and executed by the CLI. Managed
hook scripts are self-contained (the risky-tools seed embeds its patterns since
v3), so the runner materializes no per-account hook context either. What remains
here is:

- ``get_policy_snapshot`` — the catalog view the Hooks tab reads (data-spine
  ``hook_policy`` rows, ~30s TTL cache, fail-open). Never on the fire hot path.
"""

from __future__ import annotations

import threading
import time

from priva_common.dataplane import HookPolicyRecord
from priva_common.logging import get_app_logger

logger = get_app_logger(__name__)

SNAPSHOT_TTL_SECONDS = 30.0

_lock = threading.Lock()
_cache: dict = {"at": 0.0, "items": None}  # items: list[HookPolicyRecord] | None


def get_policy_snapshot(force: bool = False) -> list[HookPolicyRecord]:
    """Enabled admin hook policies, cached for SNAPSHOT_TTL_SECONDS."""
    now = time.monotonic()
    with _lock:
        fresh = _cache["items"] is not None and (now - _cache["at"]) < SNAPSHOT_TTL_SECONDS
        if fresh and not force:
            return list(_cache["items"])
    try:
        from priva_common.dataplane import get_client

        items = get_client().hook_policies.list(enabled_only=True)
    except Exception as exc:
        with _lock:
            stale = _cache["items"]
        if stale is not None:
            logger.warning("hook-policy fetch failed, serving stale snapshot ({} rows): {}",
                           len(stale), exc)
            return list(stale)
        logger.warning("hook-policy fetch failed with no cached snapshot: {}", exc)
        return []
    with _lock:
        _cache["items"] = list(items)
        _cache["at"] = now
    return list(items)


def invalidate_snapshot() -> None:
    with _lock:
        _cache["items"] = None
        _cache["at"] = 0.0
