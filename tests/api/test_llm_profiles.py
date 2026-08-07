from __future__ import annotations

import json
import os

from priva_agent_runner.services.llm_profiles import (
    close_profile_settings_overlay,
    open_profile_settings_overlay,
    profile_store_path,
    resolve_model_reference,
    store,
)
from priva_common.models.llm_profiles import LlmProfile
from priva_common.user_env import write_settings_env


def test_legacy_env_migrates_to_default_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVA_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    write_settings_env({
        "ANTHROPIC_BASE_URL": "https://gateway.example/v1",
        "ANTHROPIC_AUTH_TOKEN": "secret",
        "ANTHROPIC_MODEL": "sonnet-x",
    })

    profiles, default_id = store.read("vision-x")

    assert default_id == "default"
    assert profiles[0].id == "default"
    assert profiles[0].vision_model == "vision-x"
    assert json.loads(profile_store_path().read_text())["profiles"][0]["auth_token"] == "secret"
    assert profile_store_path().stat().st_mode & 0o777 == 0o600


def test_model_reference_preserves_colons_in_model_ids():
    profiles = [LlmProfile(id="default", label="Default", base_url="https://x", auth_token="x", default_model="m")]
    profile, model = resolve_model_reference("ollama:llama3:8b", profiles=profiles, default_profile_id="default")
    assert profile.id == "default"
    assert model == "ollama:llama3:8b"


def test_profile_overlay_is_private_and_removed():
    profile = LlmProfile(id="p", label="P", base_url="https://x", auth_token="secret", default_model="m")
    path, manager = open_profile_settings_overlay(profile, model="m")
    try:
        overlay = json.loads(open(path).read())
        assert overlay["env"]["ANTHROPIC_AUTH_TOKEN"] == "secret"
        assert os.stat(path).st_mode & 0o777 == 0o600
    finally:
        close_profile_settings_overlay(manager)
    assert not os.path.exists(path)
