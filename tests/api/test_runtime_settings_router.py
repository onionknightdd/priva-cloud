from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from priva_common.models.auth import UserRecord
from priva_common.models.resource import RuntimeSettingsUpdateRequest


USER = UserRecord(
    username="settings-user",
    password_hash="x",
    role="user",
    account_id="acct-settings",
)


def _settings(**overrides):
    return {
        "extra_env_enabled": False,
        "extra_env": {},
        "prompt_suggestion_enabled": False,
        "agent_teams_enabled": False,
        "cross_session_interaction_enabled": False,
        **overrides,
    }


@pytest.mark.asyncio
async def test_cross_session_patch_recycles_startup_fingerprints(monkeypatch):
    from priva_agent_runner.routers import user_config
    from priva_agent_runner.services.claude_sdk import session_runtime_pool as pool_module

    recycle = AsyncMock()
    monkeypatch.setattr(user_config, "read_runtime_settings", lambda: _settings())
    monkeypatch.setattr(
        user_config,
        "update_runtime_settings",
        lambda _patch: _settings(cross_session_interaction_enabled=True),
    )
    monkeypatch.setattr(pool_module.session_runtime_pool, "recycle_all", recycle)

    response = await user_config.patch_runtime_settings(
        RuntimeSettingsUpdateRequest(cross_session_interaction_enabled=True),
        USER,
    )
    assert response.cross_session_interaction_enabled is True
    recycle.assert_awaited_once_with()


def test_runtime_settings_contract_does_not_expose_pool_capacity():
    from priva_common.models.resource import RuntimeSettingsResponse

    response = RuntimeSettingsResponse(**_settings()).model_dump()

    assert "session_pool_size" not in response
    assert "session_pool_size" not in RuntimeSettingsUpdateRequest.model_fields
