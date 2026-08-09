from __future__ import annotations

import pytest

from priva_common import runtime_settings


@pytest.mark.parametrize(
    "key",
    [
        "PATH",
        "HOME",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CONFIG_DIR",
        "CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION",
        "CLAUDE_CODE_FUTURE_CONTROL",
        "PRIVA_INTERNAL",
        "CODEX_HOME",
        "VIRTUAL_ENV",
    ],
)
def test_managed_environment_variables_cannot_be_overridden(key):
    with pytest.raises(ValueError, match="cannot be overridden"):
        runtime_settings.validate_extra_env({key: "unsafe"})


@pytest.mark.parametrize("key", ["", "1INVALID", "HAS-DASH", "HAS SPACE"])
def test_environment_variable_names_are_validated(key):
    with pytest.raises(ValueError, match="Invalid environment variable name"):
        runtime_settings.validate_extra_env({key: "value"})


def test_empty_values_and_unicode_are_preserved():
    assert runtime_settings.validate_extra_env({
        "EMPTY_VALUE": "",
        "UNICODE_VALUE": "中文",
    }) == {
        "EMPTY_VALUE": "",
        "UNICODE_VALUE": "中文",
    }


def test_environment_limits_are_enforced():
    with pytest.raises(ValueError, match="at most 64"):
        runtime_settings.validate_extra_env({f"KEY_{index}": "x" for index in range(65)})
    with pytest.raises(ValueError, match="8192"):
        runtime_settings.validate_extra_env({"TOO_LARGE": "x" * 8193})


def test_runtime_settings_patch_is_partial_and_durable(monkeypatch):
    stored = {
        "runtime_settings": {
            "extra_env_enabled": False,
            "extra_env": {"FIRST": "one"},
            "prompt_suggestion_enabled": False,
        },
    }

    monkeypatch.setattr(
        runtime_settings._user_yaml,
        "get_user_yaml_key",
        lambda key, default=None: stored.get(key, default),
    )

    def save(key, value):
        stored[key] = value

    monkeypatch.setattr(runtime_settings._user_yaml, "save_user_yaml_key", save)

    after_prompt = runtime_settings.update_runtime_settings({
        "prompt_suggestion_enabled": True,
    })
    assert after_prompt == {
        "extra_env_enabled": False,
        "extra_env": {"FIRST": "one"},
        "prompt_suggestion_enabled": True,
    }

    after_env = runtime_settings.update_runtime_settings({
        "extra_env_enabled": True,
        "extra_env": {"SECOND": "two"},
    })
    assert after_env == {
        "extra_env_enabled": True,
        "extra_env": {"SECOND": "two"},
        "prompt_suggestion_enabled": True,
    }
    assert stored["runtime_settings"] == after_env


def test_invalid_manually_edited_environment_is_never_injected(monkeypatch):
    monkeypatch.setattr(
        runtime_settings._user_yaml,
        "get_user_yaml_key",
        lambda *_args, **_kwargs: {
            "extra_env_enabled": True,
            "extra_env": {"PATH": "/untrusted"},
            "prompt_suggestion_enabled": True,
        },
    )

    assert runtime_settings.read_runtime_settings() == {
        "extra_env_enabled": True,
        "extra_env": {},
        "prompt_suggestion_enabled": True,
    }
