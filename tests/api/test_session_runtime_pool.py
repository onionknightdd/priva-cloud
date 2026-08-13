from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest
from claude_agent_sdk import ResultMessage
from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

from priva_agent_runner.services.claude_sdk import run_registry as registry_module
from priva_agent_runner.services.claude_sdk.session_runtime_pool import (
    MIB,
    MAX_SESSION_RUNTIME_POOL_CAPACITY,
    MAX_SESSION_RUNTIME_ACTIVE_CAPACITY,
    PermissionBridge,
    RuntimeDisconnectedError,
    RuntimeMemorySnapshot,
    RuntimePoolCapacityError,
    RuntimePoolMemoryPressureError,
    RuntimeWriteScopeBusyError,
    SessionRuntimeBusyError,
    SessionRuntimePool,
    options_fingerprint,
    runtime_active_capacity_for_memory,
    runtime_capacity_for_memory,
)


@dataclass
class FakeOptions:
    cwd: str
    resume: str | None = None
    settings: str | None = None
    can_use_tool: Any = None
    hooks: Any = None
    permission_mode: str = "bypassPermissions"
    fork_session: bool = False


def _result(session_id: str) -> dict[str, Any]:
    return {
        "type": "result",
        "subtype": "success",
        "duration_ms": 1,
        "duration_api_ms": 1,
        "is_error": False,
        "num_turns": 1,
        "session_id": session_id,
        "result": "ok",
    }


class FakeQuery:
    def __init__(self) -> None:
        self.frames: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self.control_requests: list[dict[str, Any]] = []

    async def receive_messages(self):
        while True:
            frame = await self.frames.get()
            if frame is None:
                return
            yield frame

    async def _send_control_request(self, request, timeout=60.0):
        del timeout
        self.control_requests.append(request)
        return {"title": "Pool title"}


class FakeClient:
    def __init__(self, options: FakeOptions) -> None:
        self.options = options
        self._query = FakeQuery()
        self.connected = False
        self.disconnected = False
        self.prompts: list[Any] = []
        self.permission_modes: list[str] = []
        self.connect_permission_mode: str | None = None

    async def connect(self) -> None:
        self.connect_permission_mode = self.options.permission_mode
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True
        await self._query.frames.put(None)

    async def query(self, prompt, session_id="default") -> None:
        del session_id
        self.prompts.append(prompt)

    async def interrupt(self) -> None:
        return None

    async def set_permission_mode(self, mode) -> None:
        self.permission_modes.append(mode)

    async def set_model(self, model=None) -> None:
        del model


class FakeCoordinator:
    def __init__(self) -> None:
        self.cancelled = 0

    def cancel_all(self) -> None:
        self.cancelled += 1


async def _allow(tool_name, tool_input, context):
    del tool_name, tool_input, context
    return PermissionResultAllow()


def _factory_and_cleanup():
    clients: list[FakeClient] = []
    cleaned: list[FakeOptions] = []

    def factory(options):
        client = FakeClient(options)
        clients.append(client)
        return client

    def cleanup(options):
        cleaned.append(options)

    return clients, cleaned, factory, cleanup


def test_platform_pool_capacity_scales_with_memory_and_caps_at_five():
    assert runtime_capacity_for_memory(768 * MIB) == 1
    assert runtime_capacity_for_memory(1024 * MIB) == 2
    assert runtime_capacity_for_memory(1639 * MIB) == 5
    assert runtime_capacity_for_memory(8 * 1024 * MIB) == 5
    assert MAX_SESSION_RUNTIME_POOL_CAPACITY == 5
    assert runtime_active_capacity_for_memory(768 * MIB) == 1
    assert runtime_active_capacity_for_memory(1024 * MIB) == 1
    assert runtime_active_capacity_for_memory(1639 * MIB) == 3
    assert runtime_active_capacity_for_memory(2 * 1024 * MIB) == 3
    assert MAX_SESSION_RUNTIME_ACTIVE_CAPACITY == 3


@pytest.mark.asyncio
async def test_dynamic_capacity_refreshes_and_evicts_idle_on_soft_pressure():
    memory = [RuntimeMemorySnapshot(1639 * MIB, 400 * MIB, "test")]
    pool = SessionRuntimePool(memory_reader=lambda: memory[0])
    await pool.startup()
    assert pool.capacity == 5
    assert pool.stats()["active_capacity"] == 3
    clients, _, factory, cleanup = _factory_and_cleanup()

    async def warm(key: str):
        lease = await pool.acquire(
            key=key,
            options=FakeOptions(cwd=f"/workspace/{key}"),
            bridge=PermissionBridge(),
            permission_callback=_allow,
            coordinator=FakeCoordinator(),
            cancelled=asyncio.Event(),
            client_factory=factory,
            cleanup_options=cleanup,
        )
        assert await lease.release(keep_warm=True)

    await warm("session-a")
    await warm("session-b")
    assert pool.stats()["resident"] == 2

    memory[0] = RuntimeMemorySnapshot(
        1639 * MIB,
        int(1639 * MIB * 0.81),
        "test",
    )
    await pool.refresh_capacity()
    assert pool.stats()["resident"] == 0
    assert all(client.disconnected for client in clients)

    memory[0] = RuntimeMemorySnapshot(1024 * MIB, 400 * MIB, "test")
    await pool.refresh_capacity()
    assert pool.capacity == 2
    await pool.shutdown(grace_seconds=0)


@pytest.mark.asyncio
async def test_hard_memory_watermark_rejects_new_turn_and_cleans_options():
    memory = RuntimeMemorySnapshot(1639 * MIB, int(1639 * MIB * 0.91), "test")
    pool = SessionRuntimePool(memory_reader=lambda: memory)
    await pool.startup()
    _, cleaned, factory, cleanup = _factory_and_cleanup()
    options = FakeOptions(cwd="/workspace/pressure")

    with pytest.raises(RuntimePoolMemoryPressureError, match="hard watermark"):
        await pool.acquire(
            key="pressure-session",
            options=options,
            bridge=PermissionBridge(),
            permission_callback=_allow,
            coordinator=FakeCoordinator(),
            cancelled=asyncio.Event(),
            client_factory=factory,
            cleanup_options=cleanup,
        )

    assert cleaned == [options]
    assert pool.stats()["resident"] == 0
    await pool.shutdown(grace_seconds=0)


@pytest.mark.asyncio
async def test_dynamic_pool_keeps_five_warm_slots_but_bounds_heavy_turns_at_three():
    memory = RuntimeMemorySnapshot(1639 * MIB, 400 * MIB, "test")
    pool = SessionRuntimePool(memory_reader=lambda: memory)
    await pool.startup()
    clients, cleaned, factory, cleanup = _factory_and_cleanup()

    leases = []
    for index in range(3):
        leases.append(await pool.acquire(
            key=f"active-{index}",
            options=FakeOptions(cwd=f"/workspace/active-{index}"),
            bridge=PermissionBridge(),
            permission_callback=_allow,
            coordinator=FakeCoordinator(),
            cancelled=asyncio.Event(),
            client_factory=factory,
            cleanup_options=cleanup,
        ))

    rejected = FakeOptions(cwd="/workspace/active-3")
    with pytest.raises(RuntimePoolCapacityError, match="3 active turn slots"):
        await pool.acquire(
            key="active-3",
            options=rejected,
            bridge=PermissionBridge(),
            permission_callback=_allow,
            coordinator=FakeCoordinator(),
            cancelled=asyncio.Event(),
            client_factory=factory,
            cleanup_options=cleanup,
        )
    assert cleaned == [rejected]
    assert pool.stats()["capacity"] == 5
    assert pool.stats()["active_capacity"] == 3

    for lease in leases:
        await lease.release(keep_warm=True)
    assert pool.stats()["resident"] == 3
    await pool.shutdown(grace_seconds=0)
    assert all(client.disconnected for client in clients)


@pytest.mark.asyncio
async def test_native_peer_turn_cannot_bypass_dynamic_active_budget():
    memory = RuntimeMemorySnapshot(1639 * MIB, 400 * MIB, "test")
    pool = SessionRuntimePool(memory_reader=lambda: memory)
    await pool.startup()
    clients, _, factory, cleanup = _factory_and_cleanup()

    async def acquire(key: str):
        return await pool.acquire(
            key=key,
            options=FakeOptions(cwd=f"/workspace/{key}"),
            bridge=PermissionBridge(),
            permission_callback=_allow,
            coordinator=FakeCoordinator(),
            cancelled=asyncio.Event(),
            client_factory=factory,
            cleanup_options=cleanup,
        )

    # Establish four independently reachable inbox sockets, then make three
    # explicit turns active. A fourth native peer turn bypasses acquire(), so
    # the lifetime receiver itself must fail closed and retire that runtime.
    for index in range(4):
        lease = await acquire(f"peer-{index}")
        await lease.release(keep_warm=True)
    active = [await acquire(f"peer-{index}") for index in range(3)]
    await clients[3]._query.frames.put({
        "type": "user",
        "session_id": "peer-3",
        "origin": {"kind": "peer", "session_id": "sender"},
        "message": {
            "role": "user",
            "content": "<agent-message from=\"sender\">hello</agent-message>",
        },
    })

    for _ in range(50):
        if clients[3].disconnected and pool.stats()["resident"] == 3:
            break
        await asyncio.sleep(0)
    assert clients[3].disconnected
    assert pool.stats()["resident"] == 3

    for lease in active:
        await lease.release(keep_warm=True)
    await pool.shutdown(grace_seconds=0)


@pytest.mark.asyncio
async def test_runtime_is_sticky_across_results_and_keeps_one_receiver():
    pool = SessionRuntimePool(capacity=2)
    await pool.startup(2)
    clients, cleaned, factory, cleanup = _factory_and_cleanup()

    first_options = FakeOptions(cwd="/workspace")
    first = await pool.acquire(
        key="pending:run-1",
        options=first_options,
        bridge=PermissionBridge(),
        permission_callback=_allow,
        coordinator=FakeCoordinator(),
        cancelled=asyncio.Event(),
        client_factory=factory,
        cleanup_options=cleanup,
    )
    assert first.client._query is clients[0]._query
    assert clients[0].connect_permission_mode == "plan"
    assert first_options.permission_mode == "bypassPermissions"
    await first.client.query("first")
    await clients[0]._query.frames.put(_result("session-1"))
    messages = [message async for message in first.client.receive_response()]
    assert isinstance(messages[-1], ResultMessage)
    await first.remap_session("session-1")
    assert await first.release(keep_warm=True)
    assert clients[0].permission_modes == ["bypassPermissions", "plan"]

    duplicate_options = FakeOptions(cwd="/workspace", resume="session-1")
    second = await pool.acquire(
        key="session-1",
        options=duplicate_options,
        bridge=PermissionBridge(),
        permission_callback=_allow,
        coordinator=FakeCoordinator(),
        cancelled=asyncio.Event(),
        client_factory=factory,
        cleanup_options=cleanup,
    )
    assert second.runtime is first.runtime
    assert clients[0].permission_modes == [
        "bypassPermissions",
        "plan",
        "bypassPermissions",
    ]
    assert len(clients) == 1
    assert cleaned == [duplicate_options]
    await clients[0]._query.frames.put(_result("session-1"))
    assert isinstance(
        [message async for message in second.client.receive_response()][-1],
        ResultMessage,
    )
    assert await second.release(keep_warm=True)
    assert clients[0].permission_modes == [
        "bypassPermissions",
        "plan",
        "bypassPermissions",
        "plan",
    ]

    stats = pool.stats()
    assert stats["capacity"] == 2
    assert stats["capacity_mode"] == "fixed-test"
    assert stats["resident"] == 1
    assert stats["idle"] == 1
    assert stats["active"] == 0
    assert stats["closing"] is False
    assert stats["list_agents_capable"] is None
    assert stats["memory"]["hard_ratio"] == 0.9
    await pool.shutdown(grace_seconds=0)
    assert clients[0].disconnected
    assert cleaned == [duplicate_options, first_options]


@pytest.mark.asyncio
async def test_capacity_rejects_when_pinned_then_evicts_lru_idle_runtime():
    pool = SessionRuntimePool(capacity=2)
    await pool.startup(2)
    clients, cleaned, factory, cleanup = _factory_and_cleanup()

    async def acquire(key: str):
        return await pool.acquire(
            key=key,
            options=FakeOptions(cwd=f"/workspace/{key}"),
            bridge=PermissionBridge(),
            permission_callback=_allow,
            coordinator=FakeCoordinator(),
            cancelled=asyncio.Event(),
            client_factory=factory,
            cleanup_options=cleanup,
        )

    first = await acquire("session-a")
    second = await acquire("session-b")
    with pytest.raises(RuntimePoolCapacityError):
        await acquire("session-c")

    assert await first.release(keep_warm=True)
    third = await acquire("session-c")
    assert clients[0].disconnected
    assert pool.stats()["resident"] == 2

    await second.release(keep_warm=True)
    await third.release(keep_warm=True)
    await pool.shutdown(grace_seconds=0)
    assert all(client.disconnected for client in clients)
    assert len(cleaned) == 4  # rejected options + three resident options


@pytest.mark.asyncio
async def test_capacity_shrink_retires_a_runtime_that_was_still_starting():
    pool = SessionRuntimePool(capacity=2)
    await pool.startup(2)
    clients: list[FakeClient] = []
    cleaned: list[FakeOptions] = []
    connect_gate = asyncio.Event()
    both_connecting = asyncio.Event()

    class SlowConnectClient(FakeClient):
        async def connect(self) -> None:
            if len(clients) >= 2:
                both_connecting.set()
            await connect_gate.wait()
            await super().connect()

    def factory(options):
        client = SlowConnectClient(options)
        clients.append(client)
        return client

    async def acquire(key: str):
        return await pool.acquire(
            key=key,
            options=FakeOptions(cwd=f"/workspace/{key}"),
            bridge=PermissionBridge(),
            permission_callback=_allow,
            coordinator=FakeCoordinator(),
            cancelled=asyncio.Event(),
            client_factory=factory,
            cleanup_options=cleaned.append,
        )

    first_task = asyncio.create_task(acquire("session-a"))
    second_task = asyncio.create_task(acquire("session-b"))
    await both_connecting.wait()
    await pool.configure(1)
    connect_gate.set()
    first, second = await asyncio.gather(first_task, second_task)

    retiring = next(lease for lease in (first, second) if lease.runtime._retire_on_idle)
    retained = second if retiring is first else first
    assert not await retiring.release(keep_warm=True)
    assert await retained.release(keep_warm=True)
    assert pool.stats()["resident"] == 1

    await pool.shutdown(grace_seconds=0)
    assert all(client.disconnected for client in clients)


@pytest.mark.asyncio
async def test_session_maintenance_reservation_blocks_new_admission_until_release():
    pool = SessionRuntimePool(capacity=1)
    await pool.startup(1)
    clients, _, factory, cleanup = _factory_and_cleanup()

    first = await pool.acquire(
        key="maintenance-session",
        options=FakeOptions(cwd="/workspace/maintenance"),
        bridge=PermissionBridge(),
        permission_callback=_allow,
        coordinator=FakeCoordinator(),
        cancelled=asyncio.Event(),
        client_factory=factory,
        cleanup_options=cleanup,
    )
    await first.release(keep_warm=True)
    maintenance = await pool.reserve_session(
        "maintenance-session",
        cancel_active=False,
    )
    assert maintenance is not None
    assert clients[0].disconnected

    with pytest.raises(SessionRuntimeBusyError, match="reserved for maintenance"):
        await pool.acquire(
            key="maintenance-session",
            options=FakeOptions(cwd="/workspace/maintenance"),
            bridge=PermissionBridge(),
            permission_callback=_allow,
            coordinator=FakeCoordinator(),
            cancelled=asyncio.Event(),
            client_factory=factory,
            cleanup_options=cleanup,
        )

    await maintenance.release()
    second = await pool.acquire(
        key="maintenance-session",
        options=FakeOptions(cwd="/workspace/maintenance"),
        bridge=PermissionBridge(),
        permission_callback=_allow,
        coordinator=FakeCoordinator(),
        cancelled=asyncio.Event(),
        client_factory=factory,
        cleanup_options=cleanup,
    )
    await second.release(keep_warm=True)
    await pool.shutdown(grace_seconds=0)


@pytest.mark.asyncio
async def test_session_maintenance_wins_against_runtime_still_connecting():
    pool = SessionRuntimePool(capacity=1)
    await pool.startup(1)
    clients: list[FakeClient] = []
    connect_started = asyncio.Event()
    connect_gate = asyncio.Event()

    class SlowConnectClient(FakeClient):
        async def connect(self) -> None:
            connect_started.set()
            await connect_gate.wait()
            await super().connect()

    def factory(options):
        client = SlowConnectClient(options)
        clients.append(client)
        return client

    acquire_task = asyncio.create_task(pool.acquire(
        key="starting-maintenance",
        options=FakeOptions(cwd="/workspace/maintenance"),
        bridge=PermissionBridge(),
        permission_callback=_allow,
        coordinator=FakeCoordinator(),
        cancelled=asyncio.Event(),
        client_factory=factory,
        cleanup_options=lambda _options: None,
    ))
    await connect_started.wait()
    maintenance_task = asyncio.create_task(pool.reserve_session(
        "starting-maintenance",
        cancel_active=True,
        grace_seconds=1.0,
    ))
    await asyncio.sleep(0)
    connect_gate.set()

    with pytest.raises(SessionRuntimeBusyError, match="reserved during startup"):
        await acquire_task
    maintenance = await maintenance_task
    assert maintenance is not None
    assert clients[0].disconnected
    await maintenance.release()
    await pool.shutdown(grace_seconds=0)


@pytest.mark.asyncio
async def test_forced_maintenance_close_wakes_active_turn_consumer():
    pool = SessionRuntimePool(capacity=1)
    await pool.startup(1)
    _, _, factory, cleanup = _factory_and_cleanup()
    lease = await pool.acquire(
        key="forced-maintenance",
        options=FakeOptions(cwd="/workspace/maintenance"),
        bridge=PermissionBridge(),
        permission_callback=_allow,
        coordinator=FakeCoordinator(),
        cancelled=None,
        client_factory=factory,
        cleanup_options=cleanup,
    )
    consumer = asyncio.create_task(
        anext(lease.client.receive_response().__aiter__())
    )
    await asyncio.sleep(0)

    maintenance = await pool.reserve_session(
        "forced-maintenance",
        cancel_active=True,
        grace_seconds=0,
    )
    assert maintenance is not None
    with pytest.raises(RuntimeDisconnectedError, match="was closed"):
        await consumer
    await maintenance.release()
    await pool.shutdown(grace_seconds=0)


@pytest.mark.asyncio
async def test_warm_idle_runtime_does_not_hold_activity_lease():
    before, _ = registry_module.activity.snapshot()
    record = registry_module.run_registry.create(session_id="activity-session")
    assert registry_module.activity.snapshot()[0] == before + 1

    pool = SessionRuntimePool(capacity=1)
    await pool.startup(1)
    _, _, factory, cleanup = _factory_and_cleanup()
    lease = await pool.acquire(
        key="activity-session",
        options=FakeOptions(cwd="/workspace/activity"),
        bridge=PermissionBridge(),
        permission_callback=_allow,
        coordinator=FakeCoordinator(),
        cancelled=asyncio.Event(),
        client_factory=factory,
        cleanup_options=cleanup,
    )
    await lease.release(keep_warm=True)
    registry_module.run_registry.finish(record, "completed")

    assert pool.stats()["resident"] == 1
    assert pool.stats()["idle"] == 1
    assert registry_module.activity.snapshot()[0] == before
    await pool.shutdown(grace_seconds=0)


@pytest.mark.asyncio
async def test_shutdown_rejects_new_work_cancels_active_and_reaps_client():
    from priva_agent_runner.services.claude_sdk.session_runtime_pool import (
        RuntimePoolShuttingDownError,
    )

    pool = SessionRuntimePool(capacity=1)
    await pool.startup(1)
    clients, cleaned, factory, cleanup = _factory_and_cleanup()
    cancelled = asyncio.Event()
    await pool.acquire(
        key="shutdown-session",
        options=FakeOptions(cwd="/workspace/shutdown"),
        bridge=PermissionBridge(),
        permission_callback=_allow,
        coordinator=FakeCoordinator(),
        cancelled=cancelled,
        client_factory=factory,
        cleanup_options=cleanup,
    )

    await pool.shutdown(grace_seconds=0)
    assert cancelled.is_set()
    assert clients[0].disconnected
    assert len(cleaned) == 1
    assert pool.stats()["resident"] == 0
    with pytest.raises(RuntimePoolShuttingDownError):
        await pool.acquire(
            key="too-late",
            options=FakeOptions(cwd="/workspace/too-late"),
            bridge=PermissionBridge(),
            permission_callback=_allow,
            coordinator=FakeCoordinator(),
            cancelled=asyncio.Event(),
            client_factory=factory,
            cleanup_options=cleanup,
        )


@pytest.mark.asyncio
async def test_shutdown_cancels_and_reaps_runtime_still_connecting():
    pool = SessionRuntimePool(capacity=1)
    await pool.startup(1)
    clients: list[FakeClient] = []
    cleaned: list[FakeOptions] = []
    connect_started = asyncio.Event()

    class NeverConnectedClient(FakeClient):
        async def connect(self) -> None:
            connect_started.set()
            await asyncio.Event().wait()

    def factory(options):
        client = NeverConnectedClient(options)
        clients.append(client)
        return client

    options = FakeOptions(cwd="/workspace/starting-shutdown")
    acquire_task = asyncio.create_task(
        pool.acquire(
            key="starting-shutdown",
            options=options,
            bridge=PermissionBridge(),
            permission_callback=_allow,
            coordinator=FakeCoordinator(),
            cancelled=asyncio.Event(),
            client_factory=factory,
            cleanup_options=cleaned.append,
        )
    )
    await connect_started.wait()

    await pool.shutdown(grace_seconds=0)

    assert acquire_task.cancelled()
    assert clients[0].disconnected
    assert cleaned == [options]
    assert not pool._starting
    assert not pool._starting_tasks


@pytest.mark.asyncio
async def test_cancelled_eviction_keeps_slot_reserved_and_finishes_physical_close():
    pool = SessionRuntimePool(capacity=1)
    await pool.startup(1)
    clients, cleaned, factory, cleanup = _factory_and_cleanup()

    first = await pool.acquire(
        key="session-a",
        options=FakeOptions(cwd="/workspace/a"),
        bridge=PermissionBridge(),
        permission_callback=_allow,
        coordinator=FakeCoordinator(),
        cancelled=asyncio.Event(),
        client_factory=factory,
        cleanup_options=cleanup,
    )
    await first.release(keep_warm=True)

    close_started = asyncio.Event()
    allow_close = asyncio.Event()

    async def slow_disconnect() -> None:
        close_started.set()
        await allow_close.wait()
        clients[0].disconnected = True
        await clients[0]._query.frames.put(None)

    clients[0].disconnect = slow_disconnect  # type: ignore[method-assign]
    replacement_options = FakeOptions(cwd="/workspace/b")
    replacement = asyncio.create_task(
        pool.acquire(
            key="session-b",
            options=replacement_options,
            bridge=PermissionBridge(),
            permission_callback=_allow,
            coordinator=FakeCoordinator(),
            cancelled=asyncio.Event(),
            client_factory=factory,
            cleanup_options=cleanup,
        )
    )
    await close_started.wait()
    replacement.cancel()
    with pytest.raises(asyncio.CancelledError):
        await replacement

    # The cancelled request's reserved slot remains counted until the old CLI
    # is physically gone; otherwise a cancellation storm can exceed the cap.
    with pytest.raises(RuntimePoolCapacityError):
        await pool.acquire(
            key="session-c",
            options=FakeOptions(cwd="/workspace/c"),
            bridge=PermissionBridge(),
            permission_callback=_allow,
            coordinator=FakeCoordinator(),
            cancelled=asyncio.Event(),
            client_factory=factory,
            cleanup_options=cleanup,
        )

    allow_close.set()
    for _ in range(20):
        if clients[0].disconnected and not pool._starting:
            break
        await asyncio.sleep(0)
    assert clients[0].disconnected
    assert not pool._starting
    assert cleaned.count(replacement_options) == 1

    third = await pool.acquire(
        key="session-c",
        options=FakeOptions(cwd="/workspace/c"),
        bridge=PermissionBridge(),
        permission_callback=_allow,
        coordinator=FakeCoordinator(),
        cancelled=asyncio.Event(),
        client_factory=factory,
        cleanup_options=cleanup,
    )
    await third.release(keep_warm=True)
    await pool.shutdown(grace_seconds=0)


@pytest.mark.asyncio
async def test_cancelled_close_waiter_does_not_cancel_shared_disconnect():
    pool = SessionRuntimePool(capacity=1)
    await pool.startup(1)
    clients, cleaned, factory, cleanup = _factory_and_cleanup()
    lease = await pool.acquire(
        key="close-session",
        options=FakeOptions(cwd="/workspace/close"),
        bridge=PermissionBridge(),
        permission_callback=_allow,
        coordinator=FakeCoordinator(),
        cancelled=asyncio.Event(),
        client_factory=factory,
        cleanup_options=cleanup,
    )
    await lease.release(keep_warm=True)

    close_started = asyncio.Event()
    allow_close = asyncio.Event()

    async def slow_disconnect() -> None:
        close_started.set()
        await allow_close.wait()
        clients[0].disconnected = True
        await clients[0]._query.frames.put(None)

    clients[0].disconnect = slow_disconnect  # type: ignore[method-assign]
    waiter = asyncio.create_task(lease.runtime.close())
    await close_started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    allow_close.set()
    await lease.runtime.close()
    assert clients[0].disconnected
    assert cleaned.count(lease.runtime.options) == 1


@pytest.mark.asyncio
async def test_cancelled_release_waiter_does_not_strand_permission_binding():
    pool = SessionRuntimePool(capacity=1)
    await pool.startup(1)
    clients, cleaned, factory, cleanup = _factory_and_cleanup()
    bridge = PermissionBridge()
    lease = await pool.acquire(
        key="release-session",
        options=FakeOptions(cwd="/workspace/release"),
        bridge=bridge,
        permission_callback=_allow,
        coordinator=FakeCoordinator(),
        cancelled=asyncio.Event(),
        client_factory=factory,
        cleanup_options=cleanup,
    )

    finish_started = asyncio.Event()
    allow_finish = asyncio.Event()
    original_finish = lease.runtime.finish_turn

    async def slow_finish(*, tainted: bool = False, keep_warm: bool) -> None:
        finish_started.set()
        await allow_finish.wait()
        await original_finish(tainted=tainted, keep_warm=keep_warm)

    lease.runtime.finish_turn = slow_finish  # type: ignore[method-assign]
    waiter = asyncio.create_task(lease.release(keep_warm=True))
    await finish_started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    allow_finish.set()
    # Repeated release joins the same task; it does not skip unfinished
    # cleanup merely because the first waiter was cancelled.
    assert await lease.release(keep_warm=True)
    assert lease.runtime.state == "idle"
    assert bridge._callback is None
    assert clients[0].disconnected is False

    await pool.shutdown(grace_seconds=0)
    assert clients[0].disconnected
    assert cleaned.count(lease.runtime.options) == 1


@pytest.mark.asyncio
async def test_idle_receiver_eof_reaps_dead_slot_without_future_same_session_use():
    pool = SessionRuntimePool(capacity=1)
    await pool.startup(1)
    clients, cleaned, factory, cleanup = _factory_and_cleanup()
    first = await pool.acquire(
        key="eof-session",
        options=FakeOptions(cwd="/workspace/eof"),
        bridge=PermissionBridge(),
        permission_callback=_allow,
        coordinator=FakeCoordinator(),
        cancelled=asyncio.Event(),
        client_factory=factory,
        cleanup_options=cleanup,
    )
    await first.release(keep_warm=True)

    await clients[0]._query.frames.put(None)
    for _ in range(50):
        if pool.stats()["resident"] == 0 and clients[0].disconnected:
            break
        await asyncio.sleep(0)
    assert pool.stats()["resident"] == 0
    assert clients[0].disconnected

    # A different session can immediately use the reclaimed capacity.
    second = await pool.acquire(
        key="after-eof",
        options=FakeOptions(cwd="/workspace/after-eof"),
        bridge=PermissionBridge(),
        permission_callback=_allow,
        coordinator=FakeCoordinator(),
        cancelled=asyncio.Event(),
        client_factory=factory,
        cleanup_options=cleanup,
    )
    await second.release(keep_warm=True)
    await pool.shutdown(grace_seconds=0)
    assert len(cleaned) == 2


@pytest.mark.asyncio
async def test_unattributed_late_assistant_frame_poisons_idle_runtime():
    pool = SessionRuntimePool(capacity=1)
    await pool.startup(1)
    clients, _, factory, cleanup = _factory_and_cleanup()
    lease = await pool.acquire(
        key="late-frame-session",
        options=FakeOptions(cwd="/workspace/late"),
        bridge=PermissionBridge(),
        permission_callback=_allow,
        coordinator=FakeCoordinator(),
        cancelled=asyncio.Event(),
        client_factory=factory,
        cleanup_options=cleanup,
    )
    await lease.release(keep_warm=True)

    await clients[0]._query.frames.put({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "late"}]},
    })
    for _ in range(50):
        if pool.stats()["resident"] == 0 and clients[0].disconnected:
            break
        await asyncio.sleep(0)
    assert pool.stats()["resident"] == 0
    assert clients[0].disconnected
    await pool.shutdown(grace_seconds=0)


@pytest.mark.asyncio
async def test_concurrent_reuse_is_reserved_before_permission_bind_awaits():
    pool = SessionRuntimePool(capacity=1)
    await pool.startup(1)
    clients, cleaned, factory, cleanup = _factory_and_cleanup()
    first_bridge = PermissionBridge()

    first = await pool.acquire(
        key="session-a",
        options=FakeOptions(cwd="/workspace"),
        bridge=first_bridge,
        permission_callback=_allow,
        coordinator=FakeCoordinator(),
        cancelled=asyncio.Event(),
        client_factory=factory,
        cleanup_options=cleanup,
    )
    await first.release(keep_warm=True)

    original_bind = first_bridge.bind
    entered = asyncio.Event()
    resume_bind = asyncio.Event()

    async def slow_bind(callback, coordinator):
        entered.set()
        await resume_bind.wait()
        return await original_bind(callback, coordinator)

    first_bridge.bind = slow_bind  # type: ignore[method-assign]

    winner_task = asyncio.create_task(
        pool.acquire(
            key="session-a",
            options=FakeOptions(cwd="/workspace"),
            bridge=PermissionBridge(),
            permission_callback=_allow,
            coordinator=FakeCoordinator(),
            cancelled=asyncio.Event(),
            client_factory=factory,
            cleanup_options=cleanup,
        )
    )
    await entered.wait()
    with pytest.raises(SessionRuntimeBusyError):
        await pool.acquire(
            key="session-a",
            options=FakeOptions(cwd="/workspace"),
            bridge=PermissionBridge(),
            permission_callback=_allow,
            coordinator=FakeCoordinator(),
            cancelled=asyncio.Event(),
            client_factory=factory,
            cleanup_options=cleanup,
        )
    resume_bind.set()
    winner = await winner_task
    assert not clients[0].disconnected
    await winner.release(keep_warm=True)
    await pool.shutdown(grace_seconds=0)


@pytest.mark.asyncio
async def test_overlapping_write_scopes_serialize_but_plan_turn_can_read():
    pool = SessionRuntimePool(capacity=2)
    await pool.startup(2)
    _, _, factory, cleanup = _factory_and_cleanup()

    async def acquire(key: str, cwd: str, mode: str):
        return await pool.acquire(
            key=key,
            options=FakeOptions(cwd=cwd, permission_mode=mode),
            bridge=PermissionBridge(),
            permission_callback=_allow,
            coordinator=FakeCoordinator(),
            cancelled=asyncio.Event(),
            client_factory=factory,
            cleanup_options=cleanup,
        )

    writer = await acquire("writer", "/workspace/project", "bypassPermissions")
    reader = await acquire("reader", "/workspace/project/subdir", "plan")
    await reader.release(keep_warm=True)

    with pytest.raises(RuntimeWriteScopeBusyError):
        await acquire("second-writer", "/workspace/project/subdir", "default")

    await writer.release(keep_warm=True)
    await pool.shutdown(grace_seconds=0)


@pytest.mark.asyncio
async def test_permission_bridge_fails_closed_when_unbound():
    bridge = PermissionBridge()
    denied = await bridge("Bash", {"command": "true"}, None)
    assert isinstance(denied, PermissionResultDeny)

    coordinator = FakeCoordinator()
    generation = await bridge.bind(_allow, coordinator)
    assert isinstance(await bridge("Read", {"file_path": "/tmp/a"}, None), PermissionResultAllow)
    assert await bridge.unbind(generation)
    assert coordinator.cancelled == 1
    assert isinstance(await bridge("Read", {"file_path": "/tmp/a"}, None), PermissionResultDeny)


@pytest.mark.asyncio
async def test_idle_native_turn_holds_activity_only_until_result(monkeypatch):
    entered = 0
    left = 0

    def enter():
        nonlocal entered
        entered += 1

    def leave():
        nonlocal left
        left += 1

    monkeypatch.setattr(registry_module.activity, "enter", enter)
    monkeypatch.setattr(registry_module.activity, "leave", leave)

    pool = SessionRuntimePool(capacity=1)
    await pool.startup(1)
    clients, _, factory, cleanup = _factory_and_cleanup()
    lease = await pool.acquire(
        key="peer-session-a",
        options=FakeOptions(cwd="/workspace"),
        bridge=PermissionBridge(),
        permission_callback=_allow,
        coordinator=FakeCoordinator(),
        cancelled=asyncio.Event(),
        client_factory=factory,
        cleanup_options=cleanup,
    )
    await lease.release(keep_warm=True)
    await clients[0]._query.frames.put({
        "type": "system",
        "subtype": "init",
        "tools": ["Read", "ListAgents", "SendMessage"],
    })
    await clients[0]._query.frames.put({
        "type": "user",
        "message": {"content": "peer"},
        "origin": {"kind": "peer", "session_id": "sender-session"},
    })
    await clients[0]._query.frames.put(_result("peer-session-a"))
    for _ in range(20):
        if left:
            break
        await asyncio.sleep(0)
    assert (entered, left) == (1, 1)
    assert pool.stats()["idle"] == 1
    assert pool.stats()["list_agents_capable"] is True
    record = registry_module.run_registry.get(session_id="peer-session-a")
    assert record is not None
    assert record.status == "completed"
    peer_event = next(data for _, kind, data in record.events if kind == "user_message")
    assert peer_event["origin"] == {
        "kind": "peer",
        "session_id": "sender-session",
    }
    assert any(kind == registry_module.RUN_END_EVENT for _, kind, _ in record.events)
    await pool.shutdown(grace_seconds=0)


@pytest.mark.asyncio
async def test_peer_frames_queued_after_result_are_adopted_before_reuse():
    pool = SessionRuntimePool(capacity=1)
    await pool.startup(1)
    clients, _, factory, cleanup = _factory_and_cleanup()
    lease = await pool.acquire(
        key="peer-session-late",
        options=FakeOptions(cwd="/workspace"),
        bridge=PermissionBridge(),
        permission_callback=_allow,
        coordinator=FakeCoordinator(),
        cancelled=asyncio.Event(),
        client_factory=factory,
        cleanup_options=cleanup,
    )

    # The per-turn iterator stops at the first Result. A native peer can be
    # queued behind it before RuntimeClientContext.__aexit__ releases the turn.
    await clients[0]._query.frames.put(_result("peer-session-late"))
    await clients[0]._query.frames.put({
        "type": "user",
        "message": {"content": "late peer"},
        "origin": {"kind": "peer", "session_id": "sender-late"},
    })
    await clients[0]._query.frames.put(_result("peer-session-late"))
    messages = [message async for message in lease.client.receive_response()]
    assert isinstance(messages[-1], ResultMessage)
    for _ in range(20):
        if lease.runtime._active_queue and lease.runtime._active_queue.qsize() >= 2:
            break
        await asyncio.sleep(0)

    assert await lease.release(keep_warm=True)
    record = registry_module.run_registry.get(session_id="peer-session-late")
    assert record is not None
    assert record.status == "completed"
    assert clients[0].disconnected is False
    await pool.shutdown(grace_seconds=0)


@pytest.mark.asyncio
async def test_prompt_suggestion_tail_defers_peer_frame_to_native_run():
    pool = SessionRuntimePool(capacity=1)
    await pool.startup(1)
    clients, _, factory, cleanup = _factory_and_cleanup()
    lease = await pool.acquire(
        key="peer-after-suggestion-window",
        options=FakeOptions(cwd="/workspace"),
        bridge=PermissionBridge(),
        permission_callback=_allow,
        coordinator=FakeCoordinator(),
        cancelled=asyncio.Event(),
        client_factory=factory,
        cleanup_options=cleanup,
    )
    await clients[0]._query.frames.put(_result("peer-after-suggestion-window"))
    await clients[0]._query.frames.put({
        "type": "user",
        "message": {"content": "peer during suggestion tail"},
        "origin": {"kind": "peer", "session_id": "sender-tail"},
    })
    await clients[0]._query.frames.put(_result("peer-after-suggestion-window"))

    explicit_items = [
        item
        async for item in lease.client.iter_response_items(
            prompt_suggestions_enabled=True
        )
    ]
    assert len(explicit_items) == 1
    assert isinstance(explicit_items[0], ResultMessage)
    assert await lease.release(keep_warm=True)

    record = registry_module.run_registry.get(
        session_id="peer-after-suggestion-window"
    )
    assert record is not None and record.status == "completed"
    peer_event = next(data for _, kind, data in record.events if kind == "user_message")
    assert peer_event["origin"]["session_id"] == "sender-tail"
    await pool.shutdown(grace_seconds=0)


def test_fork_flag_is_one_shot_not_part_of_sticky_runtime_fingerprint():
    create = FakeOptions(cwd="/workspace", fork_session=True)
    resume = FakeOptions(cwd="/workspace", resume="fork-target")
    assert options_fingerprint(create) == options_fingerprint(resume)


def test_runtime_fingerprint_includes_programmatic_hook_closure_values():
    def hook_with(pattern: str):
        async def callback(*_args):
            return pattern

        return callback

    first = FakeOptions(cwd="/workspace", hooks={"PostToolUse": [hook_with("secret-a")]})
    same = FakeOptions(cwd="/workspace", hooks={"PostToolUse": [hook_with("secret-a")]})
    changed = FakeOptions(cwd="/workspace", hooks={"PostToolUse": [hook_with("secret-b")]})

    assert options_fingerprint(first) == options_fingerprint(same)
    assert options_fingerprint(first) != options_fingerprint(changed)
