"""Render enforced hook policies into a Claude Code managed-settings payload.

Pure functions (no k8s, no I/O) that turn the global set of enforced+enabled
command hook policies into the ``data`` map of the ``claude-managed-policy``
ConfigMap. The operator mounts that map at ``/etc/claude-code`` (whole dir), so
the CLI loads ``managed-settings.json`` natively — admin hooks then fire in BOTH
SDK runs and terminal ``claude`` sessions, tamper-proof on the read-only mount.

Layout decision — FLAT ConfigMap keys, not nested dirs:
    ConfigMap keys cannot contain '/', and the only way to nest is
    ``items[].path`` in the *pod* volume — which would couple the per-account
    pod template to the (global, frequently-edited) policy set and force a
    pod-spec patch on every policy change, defeating ConfigMap hot-sync. Flat
    keys mounted whole preserve the ``{id}-{hash8}`` content-addressing in the
    filename and hot-sync with zero pod churn.

Each hook runs through the baked wrapper (``priva_common.managed_hook_wrapper``)
which does the env scrub + fire-logging; the rendered command is therefore
``python3 /etc/claude-code/_wrapper.py <id> <interp> <script> [ALLOWED_ENV...]``.
v1 renders command hooks only (http/mcp_tool are validation-gated upstream).
"""

from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import Path
from typing import Iterable

from . import managed_hook_wrapper

MANAGED_DIR = "/etc/claude-code"
WRAPPER_KEY = "_wrapper.py"
SETTINGS_KEY = "managed-settings.json"
_SCRIPT_PREFIX = "mh-"


def _wrapper_source() -> str:
    return Path(managed_hook_wrapper.__file__).read_text(encoding="utf-8")


def _ext(interpreter: str) -> str:
    return "sh" if (interpreter or "").strip() in ("bash", "sh") else "py"


def script_key(policy) -> str:
    """Flat, content-addressed ConfigMap key for a policy's script."""
    h8 = (policy.content_hash or "")[:8]
    return f"{_SCRIPT_PREFIX}{policy.hook_type}-{policy.id}-{h8}.{_ext(policy.interpreter)}"


def render_command(policy, key: str) -> str:
    """`python3 <wrapper> <id> <interpreter> <script> [ALLOWED_ENV ...]` (shell-safe)."""
    interp = (policy.interpreter or "bash").strip()
    parts = [
        "python3",
        f"{MANAGED_DIR}/{WRAPPER_KEY}",
        shlex.quote(policy.id),
        shlex.quote(interp),
        f"{MANAGED_DIR}/{key}",
    ]
    parts += [shlex.quote(v) for v in (policy.allowed_env_vars or [])]
    return " ".join(parts)


def render_config_map_data(
    policies: Iterable, *, baseline: dict | None = None
) -> dict[str, str]:
    """ConfigMap ``data`` for the enforced command policies.

    Caller passes ONLY the policies to enforce (enforced & enabled & command);
    filtering lives in the operator so this stays a pure transform. ``baseline``
    seeds extra managed-settings keys (deny rules, cleanupPeriodDays, ...) —
    deliberately NOT allowManagedHooksOnly, which would also suppress the native
    user hooks in settings.json.
    """
    data: dict[str, str] = {WRAPPER_KEY: _wrapper_source()}
    hooks_block: dict[str, list] = {}

    for policy in policies:
        if policy.hook_type != "command" or not policy.script_body:
            continue
        key = script_key(policy)
        data[key] = policy.script_body
        command = render_command(policy, key)
        entry = {
            "hooks": [
                {"type": "command", "command": command, "timeout": policy.timeout_seconds or 30}
            ]
        }
        if policy.matcher:
            entry["matcher"] = policy.matcher
        # Per-event activation: fire only on the enforced subset. Fallback to
        # the full event list for records predating enforced_events.
        active = [e for e in policy.events
                  if e in set(getattr(policy, "enforced_events", None) or policy.events)]
        for event in active:
            hooks_block.setdefault(event, []).append(entry)

    settings: dict = dict(baseline or {})
    if hooks_block:
        settings["hooks"] = hooks_block
    data[SETTINGS_KEY] = json.dumps(settings, indent=2, sort_keys=True) + "\n"
    return data


def merge_generations(new_data: dict[str, str], old_data: dict[str, str] | None, keep: int = 2) -> dict[str, str]:
    """Retain up to ``keep`` script generations per policy id across renders.

    An in-flight session loaded a managed-settings.json referencing script hash
    A; a policy edit swaps the ConfigMap to hash B and would delete A, so that
    session's next fire hits a missing script (non-blocking skip). Keeping the
    previous generation's script keys bridges the gap until the session ends.
    Only script keys are carried; managed-settings.json + wrapper are always the
    new generation.
    """
    if not old_data:
        return dict(new_data)
    merged = dict(new_data)

    def _id_of(key: str) -> str | None:
        if not key.startswith(_SCRIPT_PREFIX):
            return None
        stem = key[len(_SCRIPT_PREFIX):].rsplit(".", 1)[0]
        # stem = "<hook_type>-<id>-<hash8>"; id may contain hyphens, hash8 is last.
        return stem.rsplit("-", 1)[0]

    by_id: dict[str, list[str]] = {}
    for key in old_data:
        pid = _id_of(key)
        if pid is not None and key not in merged:
            by_id.setdefault(pid, []).append(key)
    live_ids = {_id_of(k) for k in new_data if k.startswith(_SCRIPT_PREFIX)}
    for pid, keys in by_id.items():
        if pid not in live_ids:
            continue  # policy gone entirely -> drop its stale scripts
        for key in keys[: max(0, keep - 1)]:
            merged[key] = old_data[key]
    return merged


def content_digest(data: dict[str, str]) -> str:
    """Stable digest of ConfigMap data for idempotent patching."""
    return hashlib.sha256(
        json.dumps(data, sort_keys=True).encode("utf-8")
    ).hexdigest()
