"""Validated per-account runtime settings stored in ``.priva.user.yml``.

LLM credentials deliberately do not live here. They stay in the profile store
and are exposed to Claude Code through the runner's private settings overlay.
"""

from __future__ import annotations

import re
from typing import Any

from . import skill_exclude as _user_yaml

_KEY = "runtime_settings"
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_ENV_ITEMS = 64
_MAX_KEY_CHARS = 128
_MAX_VALUE_CHARS = 8192
_MAX_TOTAL_BYTES = 64 * 1024

_PROTECTED_EXACT = {
    "HOME",
    "PATH",
    "PYTHONPATH",
    "NODE_OPTIONS",
    "LD_PRELOAD",
    "DYLD_INSERT_LIBRARIES",
    "VIRTUAL_ENV",
    "UV_CACHE_DIR",
    "PIP_CACHE_DIR",
    "CLAUDE_CONFIG_DIR",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION",
}
_PROTECTED_PREFIXES = ("PRIVA_", "CLAUDE_CODE_", "CODEX_")


def is_protected_env_key(key: str) -> bool:
    return key in _PROTECTED_EXACT or key.startswith(_PROTECTED_PREFIXES)


def validate_extra_env(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("extra_env must be an object")
    if len(value) > _MAX_ENV_ITEMS:
        raise ValueError(f"extra_env supports at most {_MAX_ENV_ITEMS} entries")

    normalized: dict[str, str] = {}
    total = 0
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or not _ENV_KEY_RE.fullmatch(raw_key):
            raise ValueError(f"Invalid environment variable name: {raw_key!r}")
        if len(raw_key) > _MAX_KEY_CHARS:
            raise ValueError(f"Environment variable name is longer than {_MAX_KEY_CHARS} characters")
        if is_protected_env_key(raw_key):
            raise ValueError(f"Environment variable is managed by Priva and cannot be overridden: {raw_key}")
        if not isinstance(raw_value, str):
            raise ValueError(f"Environment variable {raw_key} must have a string value")
        if len(raw_value) > _MAX_VALUE_CHARS:
            raise ValueError(f"Environment variable {raw_key} exceeds {_MAX_VALUE_CHARS} characters")
        total += len(raw_key.encode("utf-8")) + len(raw_value.encode("utf-8"))
        if total > _MAX_TOTAL_BYTES:
            raise ValueError("extra_env exceeds the 64 KiB total limit")
        normalized[raw_key] = raw_value
    return normalized


def read_runtime_settings() -> dict[str, Any]:
    raw = _user_yaml.get_user_yaml_key(_KEY, {})
    raw = raw if isinstance(raw, dict) else {}
    try:
        extra_env = validate_extra_env(raw.get("extra_env", {}))
    except ValueError:
        # Old or manually edited invalid values never reach a subprocess.
        extra_env = {}
    return {
        "extra_env_enabled": bool(raw.get("extra_env_enabled", False)),
        "extra_env": extra_env,
        "prompt_suggestion_enabled": bool(raw.get("prompt_suggestion_enabled", False)),
    }


def update_runtime_settings(patch: dict[str, Any]) -> dict[str, Any]:
    current = read_runtime_settings()
    if "extra_env_enabled" in patch:
        current["extra_env_enabled"] = bool(patch["extra_env_enabled"])
    if "extra_env" in patch:
        current["extra_env"] = validate_extra_env(patch["extra_env"])
    if "prompt_suggestion_enabled" in patch:
        current["prompt_suggestion_enabled"] = bool(patch["prompt_suggestion_enabled"])
    _user_yaml.save_user_yaml_key(_KEY, current)
    return current
