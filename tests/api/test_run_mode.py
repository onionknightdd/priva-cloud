from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from priva_agent_runner.routers import agent as agent_router
from priva_agent_runner.services.claude_sdk import session_meta
from priva_common.models.agent import AgentRunRequest, WsInitFrame
from priva_common.models.auth import UserRecord


def _run(awaitable):
    return asyncio.run(awaitable)


def test_run_request_defaults_new_sessions_to_agent_but_tracks_omission():
    omitted = AgentRunRequest(message="hello")
    explicit = AgentRunRequest(message="hello", run_mode="code")

    assert omitted.run_mode == "agent"
    assert "run_mode" not in omitted.model_fields_set
    assert explicit.run_mode == "code"
    assert "run_mode" in explicit.model_fields_set


@pytest.mark.parametrize(
    "value",
    ["auto", "disable", ["GitHub", "Scheduler"], [], None],
)
def test_mcp_server_selection_accepts_only_the_public_contract(value):
    assert AgentRunRequest(message="hello", mcp_servers=value).mcp_servers == value
    assert WsInitFrame(message="hello", mcp_servers=value).mcp_servers == value


@pytest.mark.parametrize("value", ["all", "GitHub", 1, {"GitHub": {}}])
def test_mcp_server_selection_rejects_legacy_or_ambiguous_values(value):
    with pytest.raises(ValidationError):
        AgentRunRequest(message="hello", mcp_servers=value)
    with pytest.raises(ValidationError):
        WsInitFrame(message="hello", mcp_servers=value)


def test_missing_legacy_session_mode_repairs_to_locked_code(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))

    mode = _run(session_meta.ensure_existing_session_run_mode("legacy-session"))

    assert mode == "code"
    assert session_meta.read_meta()["sessions"]["legacy-session"]["run_mode"] == "code"


def test_invalid_legacy_session_mode_repairs_to_code(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    session_meta._write_raw({
        **session_meta._empty(),
        "sessions": {"legacy-session": {"run_mode": "unknown"}},
    })

    mode = _run(session_meta.ensure_existing_session_run_mode("legacy-session"))

    assert mode == "code"
    assert session_meta.read_meta()["sessions"]["legacy-session"]["run_mode"] == "code"


def test_session_mode_is_immutable_and_metadata_survives_flag_clear(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))

    assert _run(session_meta.claim_new_session_run_mode("session-1", "agent")) == "agent"
    with pytest.raises(session_meta.RunModeMismatchError) as exc:
        _run(session_meta.ensure_existing_session_run_mode("session-1", requested="code"))

    assert exc.value.expected == "agent"
    assert exc.value.requested == "code"
    _run(session_meta.set_session_flags("session-1", pinned=False, archived=False))
    _run(session_meta.set_session_tags("session-1", []))
    assert session_meta.read_meta()["sessions"]["session-1"]["run_mode"] == "agent"


def test_fork_inherits_parent_mode_and_legacy_parent_is_repaired(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))

    inherited = _run(session_meta.inherit_session_run_mode("legacy-parent", "child"))

    assert inherited == "code"
    sessions = session_meta.read_meta()["sessions"]
    assert sessions["legacy-parent"]["run_mode"] == "code"
    assert sessions["child"]["run_mode"] == "code"


def test_batch_repair_preserves_existing_session_metadata(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    session_meta._write_raw({
        **session_meta._empty(),
        "sessions": {
            "legacy": {"pinned": True, "tags": ["keep"]},
            "agent": {"run_mode": "agent", "archived": True},
        },
    })

    data = _run(session_meta.ensure_existing_session_run_modes(["legacy", "agent"]))

    assert data["sessions"]["legacy"] == {
        "pinned": True,
        "tags": ["keep"],
        "run_mode": "code",
    }
    assert data["sessions"]["agent"] == {"run_mode": "agent", "archived": True}


def test_resume_mode_mismatch_is_an_http_conflict(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    _run(session_meta.claim_new_session_run_mode("session-1", "code"))

    with pytest.raises(HTTPException) as exc:
        _run(agent_router._resolve_request_run_mode("session-1", "agent"))

    assert exc.value.status_code == 409
    assert exc.value.detail == {
        "code": "RunModeMismatch",
        "message": "Session run mode is locked to 'code'; requested 'agent'",
        "expected_run_mode": "code",
        "requested_run_mode": "agent",
    }


def test_sync_run_forwards_disallowed_tools_and_never_permission_feedback(tmp_path):
    request = AgentRunRequest(
        message="hello",
        run_mode="agent",
        disallowed_tools=["Bash"],
        enable_permission_feedback=True,
    )
    http_request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/sandbox/agent/run",
        "headers": [],
    })
    http_request.state.auth_method = "jwt"
    user = UserRecord(username="alice", password_hash="")
    runner = AsyncMock(return_value={
        "messages": [],
        "attempts": 1,
        "run_mode": "agent",
    })

    with (
        patch.object(agent_router, "agent_run", runner),
        patch.object(agent_router, "_resolve_run_cwd", return_value=str(tmp_path)),
        patch.object(agent_router, "_resolve_run_add_dirs", return_value=[]),
        patch.object(agent_router, "_validate_attachments", return_value=None),
        patch.object(agent_router, "_validate_images", return_value=None),
    ):
        response = _run(agent_router.run_agent(http_request, request, user))

    assert response.run_mode == "agent"
    assert runner.await_args.kwargs["extra_disallowed_tools"] == ["Bash"]
    assert "enable_permission_feedback" not in runner.await_args.kwargs


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        ("RuntimePoolCapacityError", 429),
        ("SessionRuntimeBusyError", 409),
        ("RuntimeWriteScopeBusyError", 409),
        ("RuntimePoolShuttingDownError", 503),
    ],
)
def test_sync_run_maps_pool_admission_failures(error, status_code, tmp_path):
    from priva_agent_runner.services.claude_sdk import session_runtime_pool as pool_module

    request = AgentRunRequest(message="hello", run_mode="agent")
    http_request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/sandbox/agent/run",
        "headers": [],
    })
    http_request.state.auth_method = "jwt"
    user = UserRecord(username="alice", password_hash="")
    runner = AsyncMock(side_effect=getattr(pool_module, error)("pool unavailable"))

    with (
        patch.object(agent_router, "agent_run", runner),
        patch.object(agent_router, "_resolve_run_cwd", return_value=str(tmp_path)),
        patch.object(agent_router, "_resolve_run_add_dirs", return_value=[]),
        patch.object(agent_router, "_validate_attachments", return_value=None),
        patch.object(agent_router, "_validate_images", return_value=None),
    ):
        with pytest.raises(HTTPException) as exc:
            _run(agent_router.run_agent(http_request, request, user))

    assert exc.value.status_code == status_code
    assert exc.value.detail == "pool unavailable"
