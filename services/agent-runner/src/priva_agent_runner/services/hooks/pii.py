"""Programmatic-only PII masking hook (builder step: PostToolUse).

Wired directly from builder.py — never appears in the hooks catalog. The
frontend toggle lives under Settings → Sensitive patterns
(runtime.pii_masking.enable). Relocated from the deleted built_in_hooks.py.
"""

from __future__ import annotations

from typing import Any


def make_pii_masking_hook(patterns: list[dict]):
    """Build a PostToolUse hook that replaces tool_output via ``updatedToolOutput``.

    Patterns are captured at agent-run startup, so toggling the setting takes
    effect on the next session. When no PII patterns hit, the hook returns
    ``{"continue": True}`` and the original output flows through unchanged.
    """
    from priva_common.sensitive_mask import mask_sensitive

    async def pii_masking_hook(
        input_data: Any, tool_use_id: str | None, context: Any
    ) -> dict:
        data = input_data if isinstance(input_data, dict) else {}
        tool_output = data.get("tool_output")
        if tool_output is None:
            return {"continue": True}
        masked, hits = mask_sensitive(patterns, tool_output)
        if hits == 0:
            return {"continue": True}
        return {
            "continue": True,
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "updatedToolOutput": masked,
            },
        }

    return pii_masking_hook
