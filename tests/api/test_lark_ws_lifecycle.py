"""Lifecycle & leak regression for the lark WS transport rework.

Drives a REAL lark_oapi ws.Client against a local fake Feishu WS server (only
``_get_conn_url`` is patched), because the bugs under regression live in the SDK
boundary itself:
  - stop() used to "call" the async ``_disconnect`` via to_thread — creating the
    coroutine without ever running it — so the socket survived teardown as a zombie
    that kept stealing events from any replacement connection (other environment).
  - the SDK's module-global event loop allowed exactly ONE live WS per process, and
    could never be re-entered after a teardown (re-arm → "loop is already running").

Assertions are behavioural: the fake server must SEE the close, the lark thread must
die, two apps must hold concurrent connections, and repeated arm/teardown cycles must
not grow threads or fds.
"""

import asyncio
import gc
import json
import os
import sys
import threading
import time

import pytest

# priva_channel_connector isn't pip-installed; add its src to the path (same shim as
# test_connector.py). lark_oapi IS required here — these tests exercise the real SDK.
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_CONN_SRC = os.path.join(_REPO, "services", "channel-connector", "src")
if _CONN_SRC not in sys.path:
    sys.path.insert(0, _CONN_SRC)

pytest.importorskip("lark_oapi")
websockets = pytest.importorskip("websockets")

from priva_channel_connector import pending  # noqa: E402
from priva_channel_connector.lark_ws import LarkTransport  # noqa: E402
from priva_channel_connector.pending import PendingPrompt  # noqa: E402


async def _noop_msg(_msg):
    pass


class _FakeFeishu:
    """Accepts WS connections and reads frames until the peer closes. The live
    connection set is the test's ground truth for 'did the client really close'."""

    def __init__(self):
        self.conns = set()

    async def handler(self, conn, path=None):  # path: websockets<11 compat
        self.conns.add(conn)
        try:
            async for _ in conn:
                pass
        except Exception:
            pass
        finally:
            self.conns.discard(conn)


async def _wait(predicate, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def _patch_conn_url(monkeypatch, port: int) -> None:
    from lark_oapi.ws import client as wsc
    monkeypatch.setattr(
        wsc.Client, "_get_conn_url",
        lambda self: f"ws://127.0.0.1:{port}/?device_id=test&service_id=1",
    )


def _no_lark_threads() -> bool:
    return not [t for t in threading.enumerate() if t.name.startswith("lark-ws-")]


def _fd_count() -> int:
    return len(os.listdir("/dev/fd"))


# --- socket + thread teardown ---------------------------------------------
def test_stop_closes_socket_and_thread(monkeypatch):
    fake = _FakeFeishu()

    async def main():
        server = await websockets.serve(fake.handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        _patch_conn_url(monkeypatch, port)
        t = LarkTransport("acct-close", "app", "secret", "feishu", _noop_msg)
        await t.start()
        await _wait(lambda: len(fake.conns) == 1, 5, "client to connect")
        await t.stop()
        # The server observing the close is the proof _disconnect actually ran —
        # the original bug left this connection open forever.
        await _wait(lambda: len(fake.conns) == 0, 5, "server to see the close")
        assert _no_lark_threads(), "lark ws thread survived stop()"
        server.close()
        await server.wait_closed()

    asyncio.run(main())


def test_stop_immediately_after_start_does_not_hang(monkeypatch):
    fake = _FakeFeishu()

    async def main():
        server = await websockets.serve(fake.handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        _patch_conn_url(monkeypatch, port)
        t = LarkTransport("acct-race", "app", "secret", "feishu", _noop_msg)
        await t.start()
        await t.stop()  # no wait: races thread startup on purpose
        await _wait(lambda: len(fake.conns) == 0, 5, "no lingering connection")
        assert _no_lark_threads()
        server.close()
        await server.wait_closed()

    asyncio.run(main())


# --- multi-app concurrency (the one-WS-per-process regression) -------------
def test_two_transports_hold_concurrent_connections(monkeypatch):
    fake = _FakeFeishu()

    async def main():
        server = await websockets.serve(fake.handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        _patch_conn_url(monkeypatch, port)
        t1 = LarkTransport("acct-a", "app-a", "secret", "feishu", _noop_msg)
        t2 = LarkTransport("acct-b", "app-b", "secret", "feishu", _noop_msg)
        await t1.start()
        await _wait(lambda: len(fake.conns) == 1, 5, "first app to connect")
        # With the SDK's global loop this second start() died with
        # 'This event loop is already running'.
        await t2.start()
        await _wait(lambda: len(fake.conns) == 2, 5, "both apps connected at once")
        await t1.stop()
        await _wait(lambda: len(fake.conns) == 1, 5, "only the stopped app to drop")
        await t2.stop()
        await _wait(lambda: len(fake.conns) == 0, 5, "all connections closed")
        assert _no_lark_threads()
        server.close()
        await server.wait_closed()

    asyncio.run(main())


# --- re-arm + leak across cycles -------------------------------------------
def test_no_thread_or_fd_leak_across_arm_teardown_cycles(monkeypatch):
    fake = _FakeFeishu()

    async def main():
        server = await websockets.serve(fake.handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        _patch_conn_url(monkeypatch, port)

        async def cycle(i: int) -> None:
            t = LarkTransport(f"acct-{i}", "app", "secret", "feishu", _noop_msg)
            await t.start()
            await _wait(lambda: len(fake.conns) == 1, 5, f"cycle {i} connect")
            await t.stop()
            await _wait(lambda: len(fake.conns) == 0, 5, f"cycle {i} close")

        # Warmup absorbs one-time lazy allocations (imports, executor threads).
        for i in range(2):
            await cycle(i)
        gc.collect()
        fds_baseline = _fd_count()
        for i in range(2, 10):
            await cycle(i)
        gc.collect()
        assert _no_lark_threads(), "lark ws threads accumulated across cycles"
        fds_now = _fd_count()
        assert fds_now <= fds_baseline + 2, (
            f"fd leak across arm/teardown cycles: {fds_baseline} -> {fds_now}")
        server.close()
        await server.wait_closed()

    asyncio.run(main())


# --- inbound gate after stop ------------------------------------------------
def test_dispatch_gated_when_stopping():
    received = []

    async def on_msg(m):
        received.append(m)

    t = LarkTransport("acct-gate", "app", "secret", "feishu", on_msg)

    class _Obj:
        pass

    data = _Obj()
    data.event = _Obj()
    data.event.message = _Obj()
    data.event.message.message_type = "text"
    data.event.message.content = json.dumps({"text": "hi"})
    data.event.message.chat_id = "c1"
    data.event.message.message_id = "m1"
    data.event.sender = _Obj()
    data.event.sender.sender_id = _Obj()
    data.event.sender.sender_id.open_id = "ou_x"

    async def main():
        t._loop = asyncio.get_running_loop()
        t._dispatch(data)
        await asyncio.sleep(0.05)
        assert len(received) == 1
        t._stopping = True
        t._dispatch(data)
        await asyncio.sleep(0.05)
        assert len(received) == 1, "inbound processed after stop()"

    asyncio.run(main())


def test_card_action_gated_when_stopping():
    t = LarkTransport("acct-gate2", "app", "secret", "feishu", _noop_msg)
    t._stopping = True
    resp = t._dispatch_card_action(None)
    assert resp is not None  # neutral toast, not a dispatched action


# --- pending registry TTL ----------------------------------------------------
def _mk_prompt(request_id: str, message_id: str) -> PendingPrompt:
    return PendingPrompt(
        request_id=request_id, session_id="s", account_id="a", username=None,
        chat_id="c", kind="ask_user", questions=[], message_id=message_id,
    )


def test_pending_ttl_sweep_expires_stale_entries():
    p = _mk_prompt("r-ttl-1", "m-ttl-1")
    pending.register(p)
    assert pending.get_by_request("r-ttl-1") is p
    p.registered_at -= pending._TTL_SECONDS + 1
    pending._sweep()
    assert pending.get_by_request("r-ttl-1") is None
    assert pending.get_by_message("m-ttl-1") is None
    assert p.status == "expired"


def test_pending_register_sweeps_opportunistically():
    stale = _mk_prompt("r-ttl-2", "m-ttl-2")
    pending.register(stale)
    stale.registered_at -= pending._TTL_SECONDS + 1
    fresh = _mk_prompt("r-ttl-3", "m-ttl-3")
    pending.register(fresh)  # register piggybacks the sweep
    assert pending.get_by_request("r-ttl-2") is None
    assert pending.get_by_request("r-ttl-3") is fresh
    pending.discard(fresh)


def test_worker_expire_prompts_discards_and_unlinks():
    from priva_channel_connector.sse import StreamState
    from priva_channel_connector.worker import AppWorker

    state = StreamState()
    p = _mk_prompt("r-exp-1", "m-exp-1")
    p.state = state
    state.pending_prompt = p
    pending.register(p)
    AppWorker._expire_prompts(object(), [p])
    assert pending.get_by_request("r-exp-1") is None
    assert p.status == "expired"
    assert state.pending_prompt is None
