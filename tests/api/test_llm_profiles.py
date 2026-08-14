from __future__ import annotations

import json
import os

import pytest

from priva_agent_runner.routers import credentials
from priva_agent_runner.services.llm_profiles import (
    close_profile_settings_overlay,
    open_profile_settings_overlay,
    profile_store_path,
    resolve_model,
    resolve_model_reference,
    store,
)
from priva_common.models.auth import UserRecord
from priva_common.models.llm_profiles import (
    LlmProfile,
    LlmProfileCreateRequest,
    LlmProfileUpdateRequest,
    ModelCapabilities,
)
from priva_common.models.resource import ModelInfo
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


def test_resolved_model_keeps_1m_for_cli_but_exposes_base_profile_id(monkeypatch):
    profiles = [
        LlmProfile(
            id="gateway",
            label="Gateway",
            base_url="https://x",
            auth_token="x",
            default_model="fallback",
        )
    ]
    monkeypatch.setattr(store, "read", lambda _vision_model=None: (profiles, "gateway"))

    resolved = resolve_model("gateway:ollama:llama3:8b[1M]")

    assert resolved.profile.id == "gateway"
    assert resolved.model == "ollama:llama3:8b[1m]"
    assert resolved.model_id == "ollama:llama3:8b"
    assert resolved.capabilities == {"context": "1m"}


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


@pytest.mark.asyncio
async def test_model_discovery_falls_back_to_provider_root(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self.text = "not found"
            self._payload = payload or {}

        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, headers):
            calls.append((url, headers))
            if url == "https://api.example.com/models":
                return FakeResponse(200, {"data": [{"id": "deepseek-v4-flash"}]})
            return FakeResponse(404)

    monkeypatch.setattr(credentials.httpx, "AsyncClient", FakeAsyncClient)
    profile = LlmProfile(
        id="deepseek",
        label="DeepSeek",
        base_url="https://api.example.com/anthropic",
        auth_token="secret",
    )

    models = await credentials._fetch_models(profile)

    assert [model.id for model in models] == ["deepseek-v4-flash"]
    assert calls[-1][0] == "https://api.example.com/models"
    assert calls[-1][1]["Authorization"] == "Bearer secret"
    assert calls[-1][1]["x-api-key"] == "secret"


@pytest.mark.asyncio
async def test_draft_profile_test_uses_unsaved_values(monkeypatch):
    seen = []

    async def fake_fetch(profile, timeout=15.0):
        seen.append((profile, timeout))
        return [ModelInfo(id="deepseek-v4-flash")]

    monkeypatch.setattr(credentials, "_fetch_models", fake_fetch)
    response = await credentials.test_profile_draft(
        LlmProfileCreateRequest(
            id="deepseek",
            label="DeepSeek",
            base_url="https://api.example.com/anthropic",
            auth_token="secret",
        ),
        UserRecord(username="alice", password_hash="hash"),
    )

    assert [model.id for model in response.models] == ["deepseek-v4-flash"]
    assert seen[0][0].base_url == "https://api.example.com/anthropic"
    assert seen[0][0].auth_token == "secret"


@pytest.mark.asyncio
async def test_profile_endpoint_changes_preserve_model_capability_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVA_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(credentials, "_migrate_legacy_vision", lambda: None)
    store.save([
        LlmProfile(
            id="p",
            label="P",
            base_url="https://old.example",
            auth_token="old-token",
            default_model="model-a",
            model_capabilities={"model-a": ModelCapabilities(image=True)},
        )
    ], "p")

    await credentials.update_profile(
        "p",
        LlmProfileUpdateRequest(
            base_url="https://new.example",
            auth_token="new-token",
        ),
        UserRecord(username="alice", password_hash="hash"),
    )

    updated = store.get("p")
    assert updated.base_url == "https://new.example"
    assert updated.auth_token == "new-token"
    assert updated.model_capabilities["model-a"].image is True
