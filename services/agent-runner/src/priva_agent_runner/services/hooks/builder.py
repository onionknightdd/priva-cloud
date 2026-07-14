"""Build the programmatic hooks payload for a Priva agent run.

As of D6 there is NO programmatic admin-hook path. Admin hook policies fire
exclusively NATIVELY via the mounted managed-policy ConfigMap (``/etc/claude-code``)
— the CLI executes them in SDK runs AND terminal sessions. The operator renders
that ConfigMap from the data-spine snapshot; the runner no longer materializes
scripts or injects admin callbacks, and there is no fallback (if the managed
mount is absent, admin hooks do not fire — the deploy must land the ConfigMap).

User-configured hooks are likewise native (CLI-loaded ``settings.json`` at the
user + project scope, D5). So this builder assembles only the two PROGRAMMATIC
concerns that are not policy-driven:

1. System callbacks: the hook execution logger (+ FileCanvas reminder).
2. PII masking (Settings → Sensitive patterns) — rewrites tool output in-process.
"""

from __future__ import annotations

from claude_agent_sdk.types import HookMatcher

from priva_common.logging import get_app_logger
from priva_common.user_store import get_user_store

from .callbacks import make_hook_execution_logger

logger = get_app_logger(__name__)


def build_hooks(username: str, cwd: str, auth_method: str = "jwt") -> dict[str, list[HookMatcher]]:
    """Return programmatic hooks to inject into ``ClaudeAgentOptions.hooks``.

    Admin AND user hooks are native (managed ConfigMap / settings.json) and are
    NOT assembled here. Only the system logger and PII masking are programmatic.
    """
    hooks: dict[str, list[HookMatcher]] = {}

    # 1. System callbacks (hook_execution_logger only)
    enable_file_canvas_reminder = auth_method == "jwt"
    hooks.setdefault("PostToolUse", []).append(
        HookMatcher(matcher=None, hooks=[make_hook_execution_logger(enable_file_canvas_reminder)]),
    )

    # 2. PII masking (programmatic-only — not exposed in the Hooks tab).
    # Replaces tool output via PostToolUseHookSpecificOutput.updatedToolOutput
    # before it reaches the model, when admin has enabled the toggle in
    # Settings → Sensitive patterns AND configured at least one pattern.
    try:
        runtime = get_user_store().get_runtime_config()
        pii_cfg = runtime.get("pii_masking") or {}
        if pii_cfg.get("enable") and pii_cfg.get("patterns"):
            from .pii import make_pii_masking_hook

            hooks.setdefault("PostToolUse", []).append(
                HookMatcher(matcher=None, hooks=[make_pii_masking_hook(list(pii_cfg["patterns"]))]),
            )
    except Exception as exc:
        logger.warning("Failed to enable PII masking hook for user '{}': {}", username, exc)

    return hooks
