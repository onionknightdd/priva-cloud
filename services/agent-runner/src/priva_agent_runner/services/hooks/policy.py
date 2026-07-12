"""Admin hook-policy snapshot + managed-hook context (runner side).

As of D6 the runner no longer materializes admin scripts or injects programmatic
callbacks — admin hooks are delivered NATIVELY via the operator-rendered
managed-policy ConfigMap (``/etc/claude-code``) and executed by the CLI. What
remains here is:

- ``get_policy_snapshot`` — the catalog view the Hooks tab reads (data-spine
  ``hook_policy`` rows, ~30s TTL cache, fail-open). Never on the fire hot path.
- ``write_managed_hook_context`` — materializes ``risky_tools.json`` into the
  per-account context dir the NATIVE risky-tools hook reads (the global managed
  command passes ``PRIVA_HOOK_DIR`` through the fire-log wrapper, so one global
  script serves per-account context from a fixed absolute path). The managed hook
  can fire in terminal ``claude`` too, so this is refreshed at pod startup and at
  each session build.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from priva_common.dataplane import HookPolicyRecord
from priva_common.logging import get_app_logger
from priva_common.paths import priva_home

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


# --- managed-hook context -----------------------------------------------------

def _write_atomic(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def managed_hook_context_dir() -> Path:
    """Per-account writable dir the MANAGED risky-tools hook reads its context
    from — ``$PRIVA_HOOK_DIR`` (operator sets it to /workspace/.priva/hook-context),
    else ``$PRIVA_HOME/hook-context`` for local dev. The global managed command
    passes PRIVA_HOOK_DIR through the fire-log wrapper, so a single global script
    reads per-account context from this fixed absolute path."""
    raw = os.environ.get("PRIVA_HOOK_DIR")
    return Path(raw) if raw else priva_home() / "hook-context"


def write_managed_hook_context() -> None:
    """Materialize ``risky_tools.json`` into the managed-hook context dir.

    The managed (native) risky-tools hook can fire in terminal ``claude`` too —
    outside any Priva session — so this is written at pod startup and refreshed
    at each session build."""
    try:
        from priva_common.user_store import get_user_store

        runtime = get_user_store().get_runtime_config()
        risky_list = runtime.get("risky_tool_list") or []
        ctx = managed_hook_context_dir()
        ctx.mkdir(parents=True, exist_ok=True)
        _write_atomic(ctx / "risky_tools.json",
                      json.dumps([str(r) for r in risky_list], ensure_ascii=False))
    except Exception as exc:
        logger.warning("failed to write managed hook context: {}", exc)
