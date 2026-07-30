"""The runner token is permanent, but account lifecycle authorization is live."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from priva_common.models.auth import UserRecord
from priva_agent_runner import deps


def _user(status: str) -> UserRecord:
    return UserRecord(
        username="alice",
        password_hash="x",
        role="user",
        account_id="acct-1",
        status=status,
    )


def _install_identity(monkeypatch, user: UserRecord) -> None:
    monkeypatch.setenv("ACCOUNT_ID", "acct-1")
    monkeypatch.setattr(
        deps,
        "verify",
        lambda _token: {"account_id": "acct-1", "username": "alice"},
    )
    monkeypatch.setattr(
        deps,
        "get_user_store",
        lambda: SimpleNamespace(get_user=lambda _username: user),
    )


def test_existing_runner_token_still_authenticates_an_active_account(monkeypatch):
    _install_identity(monkeypatch, _user("active"))

    assert deps._resolve("already-issued-permanent-token").account_id == "acct-1"


@pytest.mark.parametrize("status", ["disabled", "purged"])
def test_existing_runner_token_is_fenced_by_live_account_status(monkeypatch, status):
    _install_identity(monkeypatch, _user(status))

    with pytest.raises(HTTPException) as exc:
        deps._resolve("already-issued-permanent-token")

    assert exc.value.status_code == 403
    assert exc.value.detail == "Account is not active"
