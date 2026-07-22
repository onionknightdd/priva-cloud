from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import priva_control_panel.routers.terminal as terminal
from priva_common.models.auth import UserRecord
from priva_control_panel.services.auth import require_active_account


USER = UserRecord(
    username="alice",
    password_hash="x",
    account_id="acct-1",
    status="active",
)


@pytest.fixture(autouse=True)
def reset_capability_cache():
    terminal.clear_terminal_capability_cache()
    yield
    terminal.clear_terminal_capability_cache()


def _defaults(percent: int = 25):
    return SimpleNamespace(
        terminal_resource_percent=percent,
        terminal_max_sessions=3,
        terminal_idle_timeout_seconds=1800,
        terminal_max_lifetime_seconds=14400,
    )


def _run_capability(monkeypatch, *, percent=25, status=None):
    defaults = _defaults(percent)
    monkeypatch.setattr(
        terminal,
        "get_client",
        lambda: SimpleNamespace(runner_defaults=SimpleNamespace(get=lambda: defaults)),
    )
    monkeypatch.setattr(
        terminal.provisioner,
        "_status",
        lambda account_id: status or {},
    )
    return asyncio.run(terminal.terminal_capability(USER))


def test_zero_phase_is_enabled_and_wakeable(monkeypatch):
    result = _run_capability(monkeypatch, status={
        "terminal": {"phase": "Zero", "resourcePercent": 25},
    })
    assert result.enabled is True
    assert result.phase == "Zero"
    assert result.max_sessions == 3


def test_pending_runner_restart_is_not_offered(monkeypatch):
    result = _run_capability(monkeypatch, status={
        "terminal": {"phase": "PendingRunnerRestart", "resourcePercent": 0},
    })
    assert result.enabled is False
    assert result.phase == "PendingRunnerRestart"


def test_missing_operator_status_fails_closed(monkeypatch):
    result = _run_capability(monkeypatch, status={})
    assert result.enabled is False
    assert result.phase == "Pending"


def test_disabled_policy_skips_kubernetes_lookup(monkeypatch):
    defaults = _defaults(0)
    monkeypatch.setattr(
        terminal,
        "get_client",
        lambda: SimpleNamespace(runner_defaults=SimpleNamespace(get=lambda: defaults)),
    )

    def unexpected_status(_account_id):
        raise AssertionError("disabled capability must not read AgentTenant status")

    monkeypatch.setattr(terminal.provisioner, "_status", unexpected_status)
    result = asyncio.run(terminal.terminal_capability(USER))
    assert result.enabled is False
    assert result.phase == "Disabled"


def test_capability_caches_global_policy_and_account_status(monkeypatch):
    calls = {"defaults": 0, "status": 0}

    def get_defaults():
        calls["defaults"] += 1
        return _defaults()

    def get_status(_account_id):
        calls["status"] += 1
        return {"terminal": {"phase": "Zero", "resourcePercent": 25}}

    monkeypatch.setattr(
        terminal,
        "get_client",
        lambda: SimpleNamespace(runner_defaults=SimpleNamespace(get=get_defaults)),
    )
    monkeypatch.setattr(terminal.provisioner, "_status", get_status)
    asyncio.run(terminal.terminal_capability(USER))
    asyncio.run(terminal.terminal_capability(USER))
    assert calls == {"defaults": 1, "status": 1}


def test_inactive_and_unprovisioned_accounts_are_rejected():
    disabled = USER.model_copy(update={"status": "disabled"})
    with pytest.raises(HTTPException) as disabled_error:
        asyncio.run(require_active_account(disabled))
    assert disabled_error.value.status_code == 403

    unprovisioned = USER.model_copy(update={"account_id": None})
    with pytest.raises(HTTPException) as account_error:
        asyncio.run(require_active_account(unprovisioned))
    assert account_error.value.status_code == 403
