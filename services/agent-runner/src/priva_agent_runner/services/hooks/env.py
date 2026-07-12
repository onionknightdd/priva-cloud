"""Constructed environment for hook subprocesses / http hooks.

Hooks NEVER inherit the runner's environment (which carries platform JWT/HMAC
secrets, Anthropic keys and data-spine DSNs). Every fire builds a fresh env:

    base allowlist (PATH HOME LANG LC_* TMPDIR TZ TERM)
  + per-row allowed_env_vars passthrough (deny-list still wins)
  + constructed Priva/Claude context vars (CLAUDE_*, PRIVA_HOOK_DIR, ...)

Applies to admin policy hooks AND user-configured hooks — one discipline.
"""

from __future__ import annotations

import os
import re

# Inherited verbatim when present. TERM keeps interactive-ish tools from
# misbehaving; everything else is locale/tmp plumbing scripts legitimately need.
_BASE_ALLOWED = ("PATH", "HOME", "LANG", "TMPDIR", "TZ", "TERM")
_BASE_PREFIXES = ("LC_",)

# Secrets never cross into a hook, even if explicitly allowlisted.
_DENY_PREFIXES = ("ANTHROPIC_",)
_DENY_SUBSTRINGS = ("JWT", "HMAC", "DSN", "SECRET", "PASSWORD")

_ENV_REF = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")


def is_denied_env(name: str) -> bool:
    upper = name.upper()
    if upper.startswith(_DENY_PREFIXES):
        return True
    return any(s in upper for s in _DENY_SUBSTRINGS)


def build_hook_env(
    allowed_env_vars: list[str] | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Fresh hook environment: base allowlist + allowed passthrough + context."""
    env: dict[str, str] = {}
    for key in _BASE_ALLOWED:
        if key in os.environ:
            env[key] = os.environ[key]
    for key, value in os.environ.items():
        if key.startswith(_BASE_PREFIXES):
            env[key] = value
    for name in allowed_env_vars or []:
        if is_denied_env(name):
            continue
        if name in os.environ:
            env[name] = os.environ[name]
    if extra:
        env.update(extra)
    return env


def resolve_env_refs(value: str, env: dict[str, str]) -> str:
    """Expand ``$VAR`` / ``${VAR}`` refs against the CONSTRUCTED env only
    (http header values like "Bearer $WEBHOOK_TOKEN" — secrets stay out of
    the stored policy). Unknown refs resolve to the empty string."""

    def _sub(m: re.Match) -> str:
        return env.get(m.group(1) or m.group(2), "")

    return _ENV_REF.sub(_sub, value)
