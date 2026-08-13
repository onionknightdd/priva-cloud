"""WebSocket run ownership survives a browser follower disconnect."""

from __future__ import annotations

import asyncio
import threading
import time
from fastapi import FastAPI
from fastapi.testclient import TestClient

from priva_common.models.auth import UserRecord


USER = UserRecord(
    username="ws-user",
    password_hash="x",
    role="user",
    account_id="acct-ws",
)


def _receive_until(socket, event: str) -> dict:
    while True:
        frame = socket.receive_json()
        if frame.get("event") == event:
            return frame


def _wait(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_disconnect_keeps_run_alive_and_attach_replays_then_follows_live(
    tmp_path, monkeypatch
):
    from priva_agent_runner.routers import agent as router_mod

    cwd = tmp_path / "workspace"
    cwd.mkdir()
    continue_after_disconnect = threading.Event()
    finish_live = threading.Event()
    saw_cancel_after_disconnect: list[bool] = []

    async def fake_agent_run_events(
        *_args,
        emit,
        cancelled,
        new_session_id=None,
        **_kwargs,
    ):
        assert new_session_id
        await emit("stream_init", {
            "stream_id": new_session_id,
            "include_partial_messages": False,
            "run_mode": "agent",
        })
        await emit("system", {
            "subtype": "init",
            "data": {"session_id": new_session_id},
        })
        await emit("assistant_message", {
            "content": [{"type": "text", "text": "before disconnect"}],
        })
        while not continue_after_disconnect.is_set():
            await asyncio.sleep(0.01)
        saw_cancel_after_disconnect.append(cancelled.is_set())
        await emit("tool_use", {
            "content": [{"type": "tool_use", "id": "late", "name": "Read"}],
        })
        while not finish_live.is_set():
            await asyncio.sleep(0.01)
        await emit("result", {
            "session_id": new_session_id,
            "is_error": False,
            "usage": {},
        })

    monkeypatch.setattr(router_mod, "account_from_ws", lambda _ws: USER)
    monkeypatch.setattr(router_mod, "get_user_workspace", lambda _user: str(cwd))
    monkeypatch.setattr(router_mod, "agent_run_events", fake_agent_run_events)

    app = FastAPI()
    app.include_router(router_mod.router)

    with TestClient(app) as client:
        with client.websocket_connect(
            "/api/sandbox/agent/ws/run", subprotocols=["priva.ws.v1"]
        ) as first:
            first.send_json({
                "type": "init",
                "message": "keep running",
                "cwd": str(cwd),
            })
            stream_init = _receive_until(first, "stream_init")
            session_id = stream_init["data"]["stream_id"]
            before = _receive_until(first, "assistant_message")
            last_seq = before["seq"]

        record = router_mod.run_registry.get(session_id=session_id)
        assert record is not None
        assert record.live
        assert not record.cancelled.is_set()

        # Produce an event while no browser is attached. It must remain in the
        # bounded replay tail, not cancel or orphan the backend turn.
        continue_after_disconnect.set()
        assert _wait(lambda: any(kind == "tool_use" for _, kind, _ in record.events))
        assert saw_cancel_after_disconnect == [False]

        with client.websocket_connect(
            "/api/sandbox/agent/ws/run", subprotocols=["priva.ws.v1"]
        ) as attached:
            attached.send_json({
                "type": "attach",
                "session_id": session_id,
                "since_seq": last_seq,
            })
            attach_ok = _receive_until(attached, "attach_ok")
            assert attach_ok["data"]["session_id"] == session_id
            assert attach_ok["data"]["status"] == "running"

            replayed = _receive_until(attached, "tool_use")
            assert replayed["seq"] > last_seq
            assert replayed["data"]["content"][0]["id"] == "late"

            finish_live.set()
            live_result = _receive_until(attached, "result")
            assert live_result["data"]["session_id"] == session_id

        assert _wait(lambda: record.status == "completed")
        assert not record.live


def test_new_session_is_attachable_before_cli_system_init(tmp_path, monkeypatch):
    from priva_agent_runner.routers import agent as router_mod

    cwd = tmp_path / "workspace-preinit"
    cwd.mkdir()
    allow_system_init = threading.Event()

    async def delayed_init(
        *_args,
        emit,
        cancelled,
        new_session_id=None,
        **_kwargs,
    ):
        assert new_session_id
        while not allow_system_init.is_set():
            assert not cancelled.is_set()
            await asyncio.sleep(0.01)
        await emit("stream_init", {
            "stream_id": new_session_id,
            "include_partial_messages": False,
            "run_mode": "agent",
        })
        await emit("system", {
            "subtype": "init",
            "data": {"session_id": new_session_id},
        })
        await emit("result", {
            "session_id": new_session_id,
            "is_error": False,
            "usage": {},
        })

    monkeypatch.setattr(router_mod, "account_from_ws", lambda _ws: USER)
    monkeypatch.setattr(router_mod, "get_user_workspace", lambda _user: str(cwd))
    monkeypatch.setattr(router_mod, "agent_run_events", delayed_init)

    app = FastAPI()
    app.include_router(router_mod.router)
    path = "/api/sandbox/agent/ws/run"

    with TestClient(app) as client:
        with client.websocket_connect(path, subprotocols=["priva.ws.v1"]) as first:
            first.send_json({
                "type": "init",
                "message": "disconnect before init",
                "cwd": str(cwd),
            })
            accepted = _receive_until(first, "attach_ok")
            session_id = accepted["data"]["session_id"]
            run_id = accepted["data"]["run_id"]

        record = router_mod.run_registry.get(session_id=session_id)
        assert record is not None and record.live
        assert record.session_id == session_id

        # The stable UUID is assigned by Priva before launching Claude, so a
        # refresh can attach even while the CLI has not emitted system.init.
        with client.websocket_connect(path, subprotocols=["priva.ws.v1"]) as attached:
            attached.send_json({
                "type": "attach",
                "session_id": session_id,
                "run_id": run_id,
                "since_seq": 0,
            })
            attach_ok = _receive_until(attached, "attach_ok")
            assert attach_ok["data"]["session_id"] == session_id
            allow_system_init.set()
            stream_init = _receive_until(attached, "stream_init")
            assert stream_init["data"]["stream_id"] == session_id
            system = _receive_until(attached, "system")
            assert system["data"]["data"]["session_id"] == session_id
            _receive_until(attached, "result")

        assert _wait(lambda: record.status == "completed")


def test_pending_permission_survives_detach_and_can_be_resolved_after_attach(
    tmp_path, monkeypatch
):
    from priva_agent_runner.routers import agent as router_mod

    cwd = tmp_path / "workspace-permission"
    cwd.mkdir()
    permission_resolved = threading.Event()
    received_decision: list[str] = []

    class FakeCoordinator:
        def __init__(self, session_id):
            self.pending = {"permission-1": object()}
            self.request_data = {
                "request_id": "permission-1",
                "tool_name": "Bash",
                "input": {"command": "true"},
                "session_id": session_id,
            }

        def pending_request_snapshots(self):
            return [dict(self.request_data)] if self.pending else []

        def resolve(self, request_id, decision, _message="", _updated_input=None):
            assert request_id in self.pending
            self.pending.pop(request_id)
            received_decision.append(decision)
            permission_resolved.set()

        def cancel_all(self):
            self.pending.clear()

    async def permission_run(
        *_args,
        emit,
        cancelled,
        coordinator_out,
        new_session_id=None,
        **_kwargs,
    ):
        assert new_session_id
        coordinator_out[0] = FakeCoordinator(new_session_id)
        await emit("stream_init", {
            "stream_id": new_session_id,
            "include_partial_messages": False,
            "run_mode": "agent",
        })
        await emit("permission_request", {
            "request_id": "permission-1",
            "tool_name": "Bash",
            "input": {"command": "true"},
            "session_id": new_session_id,
        })
        while not permission_resolved.is_set():
            assert not cancelled.is_set()
            await asyncio.sleep(0.01)
        await emit("result", {
            "session_id": new_session_id,
            "is_error": False,
            "usage": {},
        })

    monkeypatch.setattr(router_mod, "account_from_ws", lambda _ws: USER)
    monkeypatch.setattr(router_mod, "get_user_workspace", lambda _user: str(cwd))
    monkeypatch.setattr(router_mod, "agent_run_events", permission_run)

    app = FastAPI()
    app.include_router(router_mod.router)
    path = "/api/sandbox/agent/ws/run"

    with TestClient(app) as client:
        with client.websocket_connect(path, subprotocols=["priva.ws.v1"]) as first:
            first.send_json({
                "type": "init",
                "message": "request permission",
                "cwd": str(cwd),
            })
            stream_init = _receive_until(first, "stream_init")
            session_id = stream_init["data"]["stream_id"]
            request = _receive_until(first, "permission_request")
            assert request["data"]["request_id"] == "permission-1"

        record = router_mod.run_registry.get(session_id=session_id)
        assert record is not None and record.pending_permission
        assert not record.cancelled.is_set()
        # Simulate a long disconnect that evicted the request event itself.
        # The coordinator, not this bounded tail, must remain authoritative.
        record.events.clear()
        record._event_sizes.clear()
        record._event_bytes = 0
        record.first_seq = record.next_seq
        last_seq = record.next_seq - 1

        with client.websocket_connect(path, subprotocols=["priva.ws.v1"]) as attached:
            attached.send_json({
                "type": "attach",
                "session_id": session_id,
                "since_seq": last_seq,
            })
            _receive_until(attached, "attach_ok")
            replayed = _receive_until(attached, "permission_request")
            assert replayed["data"]["request_id"] == "permission-1"
            attached.send_json({
                "type": "permission_response",
                "request_id": "permission-1",
                "decision": "allow",
            })
            result = _receive_until(attached, "result")
            assert result["data"]["session_id"] == session_id

        assert received_decision == ["allow"]
        assert _wait(lambda: record.status == "completed")


def test_queue_backpressure_rejects_only_the_command_not_the_live_run(
    tmp_path, monkeypatch
):
    from priva_agent_runner.routers import agent as router_mod

    cwd = tmp_path / "workspace-queue"
    cwd.mkdir()

    async def queue_run(
        *_args,
        emit,
        cancelled,
        queue_out,
        new_session_id=None,
        **_kwargs,
    ):
        assert new_session_id
        queue_out[0] = asyncio.Queue(maxsize=1)
        await emit("stream_init", {
            "stream_id": new_session_id,
            "include_partial_messages": False,
            "run_mode": "agent",
        })
        while not cancelled.is_set():
            await asyncio.sleep(0.01)

    monkeypatch.setattr(router_mod, "account_from_ws", lambda _ws: USER)
    monkeypatch.setattr(router_mod, "get_user_workspace", lambda _user: str(cwd))
    monkeypatch.setattr(router_mod, "agent_run_events", queue_run)

    app = FastAPI()
    app.include_router(router_mod.router)
    path = "/api/sandbox/agent/ws/run"

    with TestClient(app) as client:
        with client.websocket_connect(path, subprotocols=["priva.ws.v1"]) as socket:
            socket.send_json({
                "type": "init",
                "message": "queue pressure",
                "cwd": str(cwd),
            })
            stream_init = _receive_until(socket, "stream_init")
            session_id = stream_init["data"]["stream_id"]
            socket.send_json({"type": "queue", "id": "q-1", "text": "first"})
            accepted = _receive_until(socket, "queued")
            assert accepted["data"]["id"] == "q-1"

            socket.send_json({"type": "queue", "id": "q-2", "text": "second"})
            rejected = _receive_until(socket, "queue_rejected")
            assert rejected["data"]["id"] == "q-2"
            assert "limit" in rejected["data"]["message"]

            record = router_mod.run_registry.get(session_id=session_id)
            assert record is not None and record.live
            # The command rejection is non-terminal: the same socket can still
            # explicitly abort the registry-owned run.
            socket.send_json({"type": "abort"})

        assert _wait(lambda: record.status == "aborted")
