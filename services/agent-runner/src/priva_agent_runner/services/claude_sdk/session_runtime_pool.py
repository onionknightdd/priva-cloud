"""Bounded, session-sticky Claude SDK runtime pool.

The pool owns the physical ``ClaudeSDKClient``/CLI lifetime.  A logical run
borrows a runtime for one or more turns, while the runtime remains connected
after the final ``ResultMessage`` so Claude Code's native session inbox stays
reachable.  A runtime has exactly one lifetime raw-message receiver; per-turn
consumers never read the SDK stream directly.

Idle runtimes deliberately do *not* hold an ``activity`` lease.  They are warm
cache entries, not active work, so the operator may still scale the account Pod
to zero.  Shutdown closes every client explicitly before the event loop exits.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import os
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, Literal

from claude_agent_sdk import ResultMessage
from claude_agent_sdk._internal.message_parser import parse_message
from claude_agent_sdk.types import PermissionResultDeny

from priva_common.logging import get_app_logger
from priva_common.serialization import get_event_label, serialize_message

from .bounded_queue import BoundedAsyncQueue
from .run_registry import RUN_END_EVENT, RunAlreadyActiveError, RunRecord, run_registry

logger = get_app_logger(__name__)

RuntimeState = Literal[
    "starting", "idle", "running", "draining", "closing", "dead"
]

MAX_TURN_FRAMES = 128
MAX_TURN_FRAME_BYTES = 16 * 1024 * 1024
PROMPT_SUGGESTION_DRAIN_SECONDS = 10.0
DISCONNECT_TIMEOUT_SECONDS = 20.0

# Pool sizing is a platform policy, not a user preference.  A resident idle CLI
# is much cheaper than an actively executing turn, so resident capacity follows
# the cgroup memory limit while live memory pressure remains the final admission
# guard.  The current 1639 MiB runner tier computes to five resident sessions;
# smaller tiers shrink automatically and no tier can exceed five.
MIB = 1024 * 1024
MAX_SESSION_RUNTIME_POOL_CAPACITY = 5
DEFAULT_SESSION_RUNTIME_POOL_CAPACITY = 2
SESSION_RUNTIME_SERVICE_RESERVE_BYTES = 512 * MIB
SESSION_RUNTIME_IDLE_BUDGET_BYTES = 192 * MIB
SESSION_RUNTIME_ACTIVE_BUDGET_BYTES = 320 * MIB
MAX_SESSION_RUNTIME_ACTIVE_CAPACITY = 3
SESSION_RUNTIME_SOFT_MEMORY_RATIO = 0.80
SESSION_RUNTIME_HARD_MEMORY_RATIO = 0.90

# cgroup v2 is used by current Kubernetes nodes; the v1 paths keep local/dev
# clusters deterministic without adding a psutil dependency.
_CGROUP_LIMIT_PATHS = (
    Path("/sys/fs/cgroup/memory.max"),
    Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
)
_CGROUP_CURRENT_PATHS = (
    Path("/sys/fs/cgroup/memory.current"),
    Path("/sys/fs/cgroup/memory/memory.usage_in_bytes"),
)
# A native peer can start a turn without a browser present. Plan mode preserves
# useful read-only collaboration without letting project/user allow rules turn
# ``dontAsk`` into unattended writes in a shared, non-Git workspace.
IDLE_PERMISSION_MODE = "plan"


@dataclasses.dataclass(frozen=True)
class RuntimeMemorySnapshot:
    """Container memory values used for pool sizing and admission."""

    limit_bytes: int | None
    current_bytes: int | None
    source: str


def _read_cgroup_value(paths: tuple[Path, ...], *, is_limit: bool) -> int | None:
    for path in paths:
        try:
            raw = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not raw or raw == "max":
            return None
        try:
            value = int(raw)
        except ValueError:
            continue
        # cgroup v1 represents "unlimited" with a value close to INT64_MAX.
        if value <= 0 or (is_limit and value >= 1 << 60):
            return None
        return value
    return None


def read_runtime_memory() -> RuntimeMemorySnapshot:
    """Read the effective cgroup budget without relying on Kubernetes APIs."""

    limit = _read_cgroup_value(_CGROUP_LIMIT_PATHS, is_limit=True)
    current = _read_cgroup_value(_CGROUP_CURRENT_PATHS, is_limit=False)
    if limit is not None:
        return RuntimeMemorySnapshot(limit, current, "cgroup")

    # A developer process may run outside a constrained cgroup.  Host memory is
    # still a better automatic signal than a hard-coded production tier.
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        page_count = int(os.sysconf("SC_PHYS_PAGES"))
        host_limit = page_size * page_count
    except (OSError, TypeError, ValueError):
        host_limit = None
    return RuntimeMemorySnapshot(host_limit, current, "host" if host_limit else "fallback")


def runtime_capacity_for_memory(limit_bytes: int | None) -> int:
    """Map a memory limit to 1..5 warm runtimes.

    Keep at least 512 MiB for the Python service, stream buffers, attachments,
    MCP handlers and tool subprocess spikes.  Each additional warm Claude
    CLI/Node process receives a conservative 192 MiB resident budget; active
    workload spikes are handled separately by the live watermarks.
    """

    if limit_bytes is None:
        return DEFAULT_SESSION_RUNTIME_POOL_CAPACITY
    usable = max(0, limit_bytes - SESSION_RUNTIME_SERVICE_RESERVE_BYTES)
    computed = usable // SESSION_RUNTIME_IDLE_BUDGET_BYTES
    return max(1, min(MAX_SESSION_RUNTIME_POOL_CAPACITY, int(computed)))


def runtime_active_capacity_for_memory(limit_bytes: int | None) -> int:
    """Return the independent heavy-turn concurrency budget.

    Five warm inbox sockets are useful and cheap enough for the current Pod,
    but five tool-heavy Claude turns are not the same memory profile. Keep the
    existing platform-wide concurrency ceiling of three and reserve a larger
    320 MiB increment per active turn. Live cgroup watermarks remain the final
    guard when model/tool subprocesses use more than this planning estimate.
    """

    if limit_bytes is None:
        return min(DEFAULT_SESSION_RUNTIME_POOL_CAPACITY, MAX_SESSION_RUNTIME_ACTIVE_CAPACITY)
    usable = max(0, limit_bytes - SESSION_RUNTIME_SERVICE_RESERVE_BYTES)
    computed = usable // SESSION_RUNTIME_ACTIVE_BUDGET_BYTES
    return max(1, min(MAX_SESSION_RUNTIME_ACTIVE_CAPACITY, int(computed)))


@dataclasses.dataclass(frozen=True)
class RuntimeMessageEnvelope:
    """Parsed SDK message plus raw metadata discarded by the public parser."""

    message: Any
    origin: dict[str, Any]


class RuntimePoolError(RuntimeError):
    """Base error for deterministic pool admission failures."""


class RuntimePoolCapacityError(RuntimePoolError):
    """All resident slots are pinned by live turns."""


class RuntimePoolMemoryPressureError(RuntimePoolCapacityError):
    """A new turn/runtime would cross the container memory safety watermark."""


class SessionRuntimeBusyError(RuntimePoolError):
    """The requested logical session already owns a turn."""


class RuntimePoolShuttingDownError(RuntimePoolError):
    """New work is rejected after graceful shutdown starts."""


class RuntimeDisconnectedError(RuntimePoolError):
    """The CLI stream ended while a turn was active."""


class RuntimeFrameLimitError(RuntimePoolError):
    """A CLI frame exceeded the per-runtime retained-memory budget."""


class RuntimeSessionCollisionError(RuntimePoolError):
    """A newly-created CLI session id is already owned by another runtime."""


class RuntimeWriteScopeBusyError(RuntimePoolError):
    """Another write-capable turn owns an overlapping workspace."""


class PermissionBridge:
    """Lifetime SDK callback with generation-scoped per-run bindings.

    ``ClaudeSDKClient`` captures ``can_use_tool`` at connect time.  Reusing the
    client therefore requires an indirection that can be rebound for each run.
    An unbound or late callback always denies; it can never fall through to the
    next run's coordinator.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._generation = 0
        self._callback: Callable[[str, dict[str, Any], Any], Awaitable[Any]] | None = None
        self._coordinator: Any = None
        self._inflight = 0
        self._inflight_zero = asyncio.Event()
        self._inflight_zero.set()

    async def bind(
        self,
        callback: Callable[[str, dict[str, Any], Any], Awaitable[Any]],
        coordinator: Any,
    ) -> int:
        async with self._lock:
            if self._callback is not None:
                raise SessionRuntimeBusyError("permission bridge already bound")
            self._generation += 1
            self._callback = callback
            self._coordinator = coordinator
            return self._generation

    async def __call__(self, tool_name: str, tool_input: dict[str, Any], context: Any):
        async with self._lock:
            callback = self._callback
            if callback is None:
                return PermissionResultDeny(
                    message="No active user run is available to approve this tool call"
                )
            self._inflight += 1
            self._inflight_zero.clear()
        try:
            return await callback(tool_name, tool_input, context)
        finally:
            async with self._lock:
                self._inflight = max(0, self._inflight - 1)
                if self._inflight == 0:
                    self._inflight_zero.set()

    async def unbind(self, generation: int, *, timeout: float = 5.0) -> bool:
        """CAS-unbind and drain callbacks; return False if draining timed out."""
        async with self._lock:
            if generation != self._generation:
                return False
            coordinator = self._coordinator
            self._callback = None
            self._coordinator = None

        # A callback may have incremented ``_inflight`` just before creating its
        # coordinator Future.  Repeated cancellation closes that narrow window.
        deadline = time.monotonic() + timeout
        while not self._inflight_zero.is_set():
            if coordinator is not None:
                try:
                    coordinator.cancel_all()
                except Exception:
                    logger.warning("permission coordinator cancellation failed", exc_info=True)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                await asyncio.wait_for(self._inflight_zero.wait(), timeout=min(0.05, remaining))
            except asyncio.TimeoutError:
                pass
        if coordinator is not None:
            try:
                coordinator.cancel_all()
            except Exception:
                logger.warning("permission coordinator final cancellation failed", exc_info=True)
        return True


def _callable_identity(value: Any) -> str:
    return f"{getattr(value, '__module__', type(value).__module__)}:{getattr(value, '__qualname__', type(value).__qualname__)}"


def _stable_value(value: Any) -> Any:
    """Produce a secret-safe, address-free value for runtime fingerprinting."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return {"sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
    if isinstance(value, Mapping):
        return {
            str(key): _stable_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_stable_value(item) for item in value]
        return sorted(items, key=repr) if isinstance(value, (set, frozenset)) else items
    if dataclasses.is_dataclass(value):
        return {
            field.name: _stable_value(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if callable(value):
        # Programmatic hooks (PII masking, auth-specific Canvas reminders and
        # SDK MCP handlers) are rebuilt as fresh closures every run. Function
        # identity alone would incorrectly reuse a runtime after a captured
        # rule changes. Capture stable closure values while representing nested
        # callables by identity to avoid recursive function graphs.
        captured: list[Any] = []
        for cell in getattr(value, "__closure__", ()) or ():
            try:
                item = cell.cell_contents
            except ValueError:  # empty closure cell
                captured.append("<empty>")
                continue
            captured.append(
                {"callable": _callable_identity(item)}
                if callable(item)
                else _stable_value(item)
            )
        result: dict[str, Any] = {"callable": _callable_identity(value)}
        if captured:
            result["closure"] = captured
        return result

    # In-process SDK MCP Server objects contain handler closures with fresh
    # addresses on every options build.  Their public identity plus the request
    # parameters already captured elsewhere is the stable configuration.
    public: dict[str, Any] = {"type": f"{type(value).__module__}.{type(value).__qualname__}"}
    for attr in ("name", "version", "instructions"):
        attr_value = getattr(value, attr, None)
        if attr_value is not None:
            public[attr] = _stable_value(attr_value)
    return public


def _file_signature(path: Path) -> dict[str, Any] | None:
    try:
        stat = path.stat()
        if not path.is_file():
            return None
        return {"path": str(path), "mtime_ns": stat.st_mtime_ns, "size": stat.st_size}
    except OSError:
        return None


def options_fingerprint(options: Any) -> str:
    """Fingerprint every startup-affecting option while excluding run identity."""
    ignored = {
        "can_use_tool",
        "stderr",
        "resume",
        "session_id",
        # Fork is a one-shot CREATE operation. The target runtime is keyed by
        # its preallocated new UUID; retaining this flag in the fingerprint
        # would force that healthy runtime to restart on its first resume.
        "fork_session",
        # Per-connect overlay paths are random; fingerprint their content below.
        "settings",
    }
    if dataclasses.is_dataclass(options):
        values = {
            field.name: _stable_value(getattr(options, field.name))
            for field in dataclasses.fields(options)
            if field.name not in ignored
        }
    else:
        values = {
            key: _stable_value(value)
            for key, value in vars(options).items()
            if key not in ignored and not key.startswith("_priva_overlay")
        }

    settings_path = getattr(options, "settings", None)
    if settings_path:
        try:
            payload = Path(settings_path).read_bytes()
            values["settings_payload"] = {
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
        except OSError:
            values["settings_payload"] = "missing"

    cwd = Path(str(getattr(options, "cwd", "") or ".")).resolve()
    config_home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    watched = [
        cwd / ".mcp.json",
        cwd / ".claude" / "settings.json",
        cwd / ".claude" / "settings.local.json",
        config_home / "settings.json",
        config_home / ".claude.json",
    ]
    values["startup_files"] = [sig for path in watched if (sig := _file_signature(path))]
    values["priva_profile_id"] = getattr(options, "_priva_profile_id", None)
    values["priva_model_id"] = getattr(options, "_priva_model_id", None)
    values["priva_model_capabilities"] = _stable_value(
        getattr(options, "_priva_model_capabilities", {})
    )
    values["prompt_suggestions"] = bool(
        getattr(options, "_priva_prompt_suggestion_enabled", False)
    )
    values["vision_image_paths"] = _stable_value(
        getattr(options, "_priva_vision_image_paths", ())
    )
    canonical = json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _write_scope(options: Any) -> tuple[Path, ...]:
    paths = [getattr(options, "cwd", None), *(getattr(options, "add_dirs", None) or [])]
    canonical: list[Path] = []
    for raw in paths:
        if not raw:
            continue
        path = Path(str(raw)).expanduser().resolve(strict=False)
        if path not in canonical:
            canonical.append(path)
    return tuple(canonical)


def _write_scopes_overlap(left: tuple[Path, ...], right: tuple[Path, ...]) -> bool:
    return any(
        a == b or a in b.parents or b in a.parents
        for a in left
        for b in right
    )


class RuntimeTurnClient:
    """The per-run view of a persistent physical SDK client."""

    def __init__(
        self,
        runtime: "SessionRuntime",
        queue: BoundedAsyncQueue[dict[str, Any]],
    ):
        self._runtime = runtime
        self._queue = queue
        self.options = runtime.options

    @property
    def _query(self) -> Any:
        """Expose the physical control channel without adding another reader.

        Session title generation uses ``_query._send_control_request``.  That
        control path is multiplexed by the SDK's transport reader and is safe
        to share; only ``receive_messages`` must remain single-consumer.
        """
        return getattr(self._runtime.client, "_query", None)

    async def query(self, prompt: Any, session_id: str = "default") -> None:
        if session_id == "default":
            await self._runtime.client.query(prompt)
        else:
            await self._runtime.client.query(prompt, session_id=session_id)

    async def interrupt(self) -> None:
        await self._runtime.client.interrupt()

    async def set_permission_mode(self, mode: Any) -> None:
        await self._runtime.client.set_permission_mode(mode)

    async def set_model(self, model: str | None = None) -> None:
        await self._runtime.client.set_model(model)

    async def receive_response(self) -> AsyncIterator[Any]:
        if not self._runtime.managed_receiver:
            async for message in self._runtime.client.receive_response():
                yield message
            return
        async for item in self.iter_response_items(prompt_suggestions_enabled=False):
            if isinstance(item, RuntimeMessageEnvelope):
                yield item.message
            elif not isinstance(item, dict):
                yield item

    async def iter_response_items(
        self,
        *,
        prompt_suggestions_enabled: bool,
    ) -> AsyncIterator[Any]:
        if not self._runtime.managed_receiver:
            async for message in self._runtime.client.receive_response():
                yield message
            return

        result_seen = False
        deadline: float | None = None
        while True:
            try:
                if self._runtime._deferred_frames:
                    raw = self._runtime._deferred_frames.popleft()
                elif result_seen:
                    assert deadline is not None
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return
                    raw = await asyncio.wait_for(self._queue.get(), timeout=remaining)
                else:
                    raw = await self._queue.get()
            except asyncio.TimeoutError:
                return

            runtime_error = raw.get("__priva_runtime_error__")
            if runtime_error:
                if raw.get("__priva_runtime_nonretry__"):
                    raise RuntimeFrameLimitError(str(runtime_error))
                raise RuntimeDisconnectedError(str(runtime_error))

            if raw.get("type") == "prompt_suggestion":
                suggestion = raw.get("suggestion")
                if isinstance(suggestion, str) and suggestion.strip():
                    payload: dict[str, Any] = {"suggestion": suggestion}
                    for key in ("session_id", "uuid"):
                        if isinstance(raw.get(key), str) and raw[key]:
                            payload[key] = raw[key]
                    yield payload
                    if result_seen:
                        return
                continue

            if result_seen:
                # Result belongs to the completed turn. Any later substantive
                # frame is either a background continuation or a native peer
                # turn; leave it for the next pump/release boundary instead of
                # misattributing it to the prompt-suggestion tail.
                self._runtime._deferred_frames.append(raw)
                return

            message = parse_message(raw)
            if message is None:
                continue
            origin = raw.get("origin")
            if isinstance(origin, dict):
                yield RuntimeMessageEnvelope(message=message, origin=dict(origin))
            else:
                yield message
            if isinstance(message, ResultMessage):
                if not prompt_suggestions_enabled:
                    return
                result_seen = True
                deadline = time.monotonic() + PROMPT_SUGGESTION_DRAIN_SECONDS


class SessionRuntime:
    """One physical Claude CLI process sticky to one logical session."""

    def __init__(
        self,
        *,
        key: str,
        options: Any,
        bridge: PermissionBridge,
        fingerprint: str,
        client_factory: Callable[[Any], Any],
        cleanup_options: Callable[[Any], None],
        retire_callback: Callable[["SessionRuntime"], Awaitable[None]],
        peer_admission: Callable[["SessionRuntime"], bool],
    ) -> None:
        self.runtime_id = uuid.uuid4().hex
        self.key = key
        self.session_id: str | None = key if not key.startswith("pending:") else None
        self.options = options
        self.bridge = bridge
        self.fingerprint = fingerprint
        self.client_factory = client_factory
        self.cleanup_options = cleanup_options
        self.retire_callback = retire_callback
        self.peer_admission = peer_admission
        self.client: Any = None
        self.state: RuntimeState = "starting"
        self.last_used = time.monotonic()
        self.receiver_task: asyncio.Task | None = None
        self.managed_receiver = False
        self._active_queue: BoundedAsyncQueue[dict[str, Any]] | None = None
        self._deferred_frames: deque[dict[str, Any]] = deque()
        self._cancelled: asyncio.Event | None = None
        self._bridge_generation: int | None = None
        self._entered_client = False
        self._healthy = True
        self._retire_on_idle = False
        self.list_agents_capable: bool | None = None
        self._unsolicited_active = False
        self._unsolicited_record: RunRecord | None = None
        self._unsolicited_cancel_task: asyncio.Task | None = None
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None
        self._cleanup_requested = False
        self._options_cleaned = False
        self._resident = False
        self._retire_task: asyncio.Task[None] | None = None
        # Every physical CLI first binds its inbox in the safe idle mode. The
        # requested user mode is restored only after this runtime owns a turn.
        self._current_permission_mode = IDLE_PERMISSION_MODE
        self.write_scope = _write_scope(options)
        self._turn_write_capable = False

    @property
    def evictable(self) -> bool:
        return self.state == "idle" and self._active_queue is None

    @property
    def healthy(self) -> bool:
        return self._healthy and self.state != "dead"

    async def connect(self) -> None:
        requested_mode = getattr(self.options, "permission_mode", "default")
        self.options.permission_mode = IDLE_PERMISSION_MODE
        try:
            self.client = self.client_factory(self.options)
            if hasattr(self.client, "connect"):
                await self.client.connect()
            else:  # legacy/fake-client compatibility used by focused unit tests
                await self.client.__aenter__()
                self._entered_client = True
            query = getattr(self.client, "_query", None)
            if query is not None and hasattr(query, "receive_messages"):
                self.managed_receiver = True
                self.receiver_task = asyncio.create_task(
                    self._receiver_loop(query),
                    name=f"claude-runtime-recv-{self.runtime_id[:8]}",
                )
            self.state = "idle"
            self.last_used = time.monotonic()
        except BaseException:
            self._healthy = False
            self.state = "dead"
            await self.close(cleanup_options=False)
            raise
        finally:
            # Runtime fingerprint/retry state describe the user's effective
            # turn mode; the physical CLI has already snapshotted plan mode.
            self.options.permission_mode = requested_mode

    async def start_turn(
        self,
        callback: Callable[[str, dict[str, Any], Any], Awaitable[Any]],
        coordinator: Any,
        cancelled: asyncio.Event | None,
        permission_mode: str,
    ) -> RuntimeTurnClient:
        if self.state not in {"idle", "starting"} or self._active_queue is not None:
            raise SessionRuntimeBusyError(f"session runtime {self.key} is {self.state}")
        if self._deferred_frames:
            raise SessionRuntimeBusyError(
                f"session runtime {self.key} has deferred frames"
            )
        if permission_mode != self._current_permission_mode:
            setter = getattr(self.client, "set_permission_mode", None)
            if setter is not None:
                await setter(permission_mode)
            self._current_permission_mode = permission_mode
        # A peer may have started while the permission-mode control request was
        # in flight. Never send the browser query into that native turn.
        if self.state not in {"idle", "starting"} or self._active_queue is not None:
            setter = getattr(self.client, "set_permission_mode", None)
            if setter is not None:
                await setter(IDLE_PERMISSION_MODE)
            self._current_permission_mode = IDLE_PERMISSION_MODE
            raise SessionRuntimeBusyError(
                f"session runtime {self.key} received a peer turn during acquire"
            )
        queue = BoundedAsyncQueue[dict[str, Any]](
            maxsize=MAX_TURN_FRAMES,
            max_bytes=MAX_TURN_FRAME_BYTES,
        )
        self._active_queue = queue
        self._cancelled = cancelled
        self._turn_write_capable = permission_mode != IDLE_PERMISSION_MODE
        self._bridge_generation = await self.bridge.bind(callback, coordinator)
        self.state = "running"
        self.last_used = time.monotonic()
        return RuntimeTurnClient(self, queue)

    async def finish_turn(self, *, tainted: bool = False, keep_warm: bool) -> None:
        security_quiesced = await self.quiesce_turn_security()
        tainted = tainted or not security_quiesced
        queue = self._active_queue
        self._active_queue = None
        self._cancelled = None
        self._turn_write_capable = False
        if self._deferred_frames or (queue is not None and not queue.empty()):
            unread: list[dict[str, Any]] = list(self._deferred_frames)
            self._deferred_frames.clear()
            while queue is not None and not queue.empty():
                try:
                    raw = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if raw.get("type") == "prompt_suggestion":
                    continue
                if raw.get("type") == "system" and raw.get("subtype") == "session_state_changed":
                    continue
                unread.append(raw)
            if unread:
                # A native peer can arrive immediately after the explicit
                # turn's Result, before the service releases its binding. Keep
                # those frames in order and adopt them as a new RunRecord. Any
                # other late payload is ambiguous and poisons the runtime.
                handled = [self._observe_unsolicited(raw) for raw in unread]
                if not all(handled):
                    tainted = True
                    logger.warning(
                        "runtime {} ended with {} ambiguous unread frame(s); retiring",
                        self.runtime_id[:8],
                        handled.count(False),
                    )
        if not tainted and keep_warm and not self._unsolicited_active:
            self.state = "idle"
        elif tainted:
            self._healthy = False
            self.state = "dead"
        self.last_used = time.monotonic()

    async def quiesce_turn_security(self) -> bool:
        """Drop the user binding and enter safe idle permissions immediately.

        Title/metadata settling may continue after the last Result. A peer can
        already reach the native inbox during that window, so waiting until
        context-manager exit would briefly expose the just-finished user's
        permission mode and coordinator to a peer-origin turn.
        """
        safe = True
        generation = self._bridge_generation
        self._bridge_generation = None
        if generation is not None:
            safe = await self.bridge.unbind(generation)
        if self._current_permission_mode != IDLE_PERMISSION_MODE:
            try:
                setter = getattr(self.client, "set_permission_mode", None)
                if setter is not None:
                    await setter(IDLE_PERMISSION_MODE)
                self._current_permission_mode = IDLE_PERMISSION_MODE
            except Exception:
                safe = False
                logger.warning(
                    "runtime {} could not enter safe idle permission mode",
                    self.runtime_id[:8], exc_info=True,
                )
        return safe

    async def _receiver_loop(self, query: Any) -> None:
        try:
            async for raw in query.receive_messages():
                if not isinstance(raw, dict):
                    continue
                self._observe_runtime_metadata(raw)
                queue = self._active_queue
                if queue is not None:
                    await queue.put(raw)
                    continue
                if not self._observe_unsolicited(raw):
                    raise RuntimeDisconnectedError(
                        f"unattributed {raw.get('type', 'unknown')} frame arrived while idle"
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._healthy = False
            self.state = "dead"
            queue = self._active_queue
            if queue is not None:
                try:
                    queue.put_nowait({
                        "__priva_runtime_error__": str(exc) or repr(exc),
                        "__priva_runtime_nonretry__": isinstance(
                            exc, asyncio.QueueFull
                        ),
                    })
                except asyncio.QueueFull:
                    pass
            logger.warning(
                "runtime {} receiver stopped: {}", self.runtime_id[:8], exc,
                exc_info=True,
            )
        finally:
            if self.state not in {"closing", "dead"}:
                self._healthy = False
                self.state = "dead"
            if self.state == "dead":
                self._schedule_retirement()

    def _schedule_retirement(self) -> None:
        if not self._resident or self._retire_task is not None:
            return
        self._retire_task = asyncio.create_task(
            self.retire_callback(self),
            name=f"claude-runtime-retire-{self.runtime_id[:8]}",
        )

        def log_retirement_failure(task: asyncio.Task[None]) -> None:
            if task.cancelled():
                return
            try:
                task.result()
            except Exception:
                logger.warning(
                    "runtime {} automatic retirement failed",
                    self.runtime_id[:8],
                    exc_info=True,
                )

        self._retire_task.add_done_callback(log_retirement_failure)

    def _observe_unsolicited(self, raw: dict[str, Any]) -> bool:
        """Drain native peer turns while no browser-origin run is attached.

        Claude Code itself persists these frames to the transcript.  Until the
        cross-session Inbox UI lands, this drain protects the SDK's bounded
        receive buffer and prevents a peer turn from overlapping a user turn.
        """
        msg_type = raw.get("type")
        subtype = raw.get("subtype")
        if msg_type == "prompt_suggestion" or (
            msg_type == "system" and subtype == "session_state_changed"
        ):
            return True
        if not self._unsolicited_active:
            origin = raw.get("origin")
            content = (raw.get("message") or {}).get("content")
            peer_origin = isinstance(origin, dict) and origin.get("kind") == "peer"
            peer_envelope = (
                msg_type == "user"
                and isinstance(content, str)
                and (
                    "<agent-message" in content
                    or "<teammate-message" in content
                    or "Another Claude session sent a message:" in content
                )
            )
            if not peer_origin and not peer_envelope:
                # Idle status/control frames are not turns and must not create
                # phantom running sessions in the UI. Substantive frames are
                # different: attributing a late assistant/result/user payload
                # to the next browser turn would cross the turn boundary, so
                # fail closed and rebuild the physical runtime.
                return msg_type not in {
                    "assistant", "result", "stream_event", "user"
                }
            if not self.peer_admission(self):
                # Native inbox delivery bypasses ``SessionRuntimePool.acquire``.
                # Reject at the first peer-origin frame rather than allowing
                # warm sockets to exceed the heavy-turn or memory budget.
                self._healthy = False
                self.state = "dead"
                logger.warning(
                    "runtime {} rejected a native peer turn under resource pressure",
                    self.runtime_id[:8],
                )
                return False
            self._unsolicited_active = True
            self.state = "draining"
            created_record = False
            try:
                record = run_registry.create(
                    session_id=self.session_id,
                    run_mode=getattr(self.options, "_priva_run_mode", "agent"),
                )
                created_record = True
            except RunAlreadyActiveError:
                # The user-origin driver can release the runtime a few event-loop
                # ticks before its RunRegistry finally block. A peer message in
                # that narrow window is a native continuation of that same live
                # stream, so adopt its record instead of losing the turn.
                record = (
                    run_registry.live_for_session(self.session_id)
                    if self.session_id
                    else None
                )
                if record is None:
                    self._healthy = False
                    self._unsolicited_active = False
                    self.state = "dead"
                    logger.error(
                        "runtime {} could not claim native peer run for session {}",
                        self.runtime_id[:8], self.session_id,
                    )
                    return False
            self._unsolicited_record = record
            self._cancelled = record.cancelled
            self._turn_write_capable = False
            self._unsolicited_cancel_task = asyncio.create_task(
                self._watch_unsolicited_cancel(record),
                name=f"claude-peer-cancel-{self.runtime_id[:8]}",
            )
            if created_record:
                record.task = self._unsolicited_cancel_task
            logger.info(
                "runtime {} started native peer run {} for session {}",
                self.runtime_id[:8], record.run_id[:8], self.session_id,
            )

        record = self._unsolicited_record
        if record is None:
            return False
        if msg_type == "queue-operation":
            record.record_event("queue_operation", dict(raw))
        else:
            try:
                message = parse_message(raw)
                event_label = get_event_label(message) if message is not None else None
                if event_label is not None:
                    data = serialize_message(message)
                    # SDK 0.2.134 drops the raw peer provenance. Preserve it on
                    # the wire so the UI never presents an inbound peer turn as
                    # an ordinary user-authored message.
                    origin = raw.get("origin")
                    if isinstance(origin, dict):
                        data["origin"] = dict(origin)
                    data["native_peer_turn"] = True
                    if event_label == "result":
                        data["run_mode"] = record.run_mode
                    record.record_event(event_label, data)
            except Exception:
                logger.warning(
                    "runtime {} could not normalize unsolicited frame type={}",
                    self.runtime_id[:8], msg_type, exc_info=True,
                )
        if msg_type == "result":
            self._unsolicited_active = False
            self.state = "idle" if self._healthy else "dead"
            self.last_used = time.monotonic()
            self._cancelled = None
            if self._unsolicited_cancel_task is not None:
                self._unsolicited_cancel_task.cancel()
                self._unsolicited_cancel_task = None
            run_registry.finish(
                record,
                "error" if raw.get("is_error") else "completed",
            )
            record.record_event(RUN_END_EVENT, {"status": record.status})
            self._unsolicited_record = None
            logger.info(
                "runtime {} completed native peer run {}",
                self.runtime_id[:8], record.run_id[:8],
            )
        return True

    def _observe_runtime_metadata(self, raw: dict[str, Any]) -> None:
        if raw.get("type") != "system" or raw.get("subtype") != "init":
            return
        tools = raw.get("tools")
        if not isinstance(tools, list):
            nested = raw.get("data")
            tools = nested.get("tools") if isinstance(nested, dict) else None
        if isinstance(tools, list):
            names = {str(tool) for tool in tools}
            self.list_agents_capable = bool(
                {"ListAgents", "ListPeers"}.intersection(names)
            )

    async def _watch_unsolicited_cancel(self, record: RunRecord) -> None:
        try:
            await record.cancelled.wait()
            if self.client is not None:
                await self.client.interrupt()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "runtime {} could not interrupt native peer run {}",
                self.runtime_id[:8], record.run_id[:8], exc_info=True,
            )

    def request_cancel(self) -> None:
        if self._cancelled is not None:
            self._cancelled.set()

    def _cleanup_options_once(self) -> None:
        if self._options_cleaned or not self._cleanup_requested:
            return
        # Set the guard before invoking user-supplied cleanup. A failing
        # context-manager __exit__ must not be retried by every close waiter.
        self._options_cleaned = True
        try:
            self.cleanup_options(self.options)
        except Exception:
            logger.warning("runtime option cleanup failed", exc_info=True)

    async def close(self, *, cleanup_options: bool = True) -> None:
        """Close one physical client exactly once, even if a waiter is cancelled.

        Eviction, request cancellation and lifespan shutdown may all race to
        retire the same runtime. The physical close runs in its own shared task
        so cancelling any caller cannot strand the CLI subprocess after the
        runtime has already been removed from the pool.
        """
        if cleanup_options:
            self._cleanup_requested = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(
                self._close_physical(),
                name=f"claude-runtime-close-{self.runtime_id[:8]}",
            )
        try:
            await asyncio.shield(self._close_task)
        finally:
            # If the physical close already finished before a later caller
            # requested cleanup, that caller still owns the exactly-once step.
            if self._close_task.done():
                self._cleanup_options_once()

    async def _close_physical(self) -> None:
        self._closed = True
        was_unsolicited = self._unsolicited_active
        self._unsolicited_active = False
        unsolicited_record = self._unsolicited_record
        self._unsolicited_record = None
        self.state = "closing"
        self.request_cancel()
        # A sync HTTP turn has no external cancellation Event. If maintenance
        # or shutdown force-closes its runtime, wake the per-turn consumer
        # explicitly; cancelling the lifetime receiver alone would leave that
        # consumer blocked forever on its private queue.
        active_queue = self._active_queue
        if active_queue is not None:
            while not active_queue.empty():
                try:
                    active_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            active_queue.put_nowait({
                "__priva_runtime_error__": "session runtime was closed",
            })
        generation = self._bridge_generation
        self._bridge_generation = None
        if generation is not None:
            await self.bridge.unbind(generation)
        if self.receiver_task is not None:
            self.receiver_task.cancel()
        if self._unsolicited_cancel_task is not None:
            self._unsolicited_cancel_task.cancel()
            self._unsolicited_cancel_task = None
        try:
            if self.client is not None:
                if hasattr(self.client, "disconnect"):
                    await asyncio.wait_for(
                        self.client.disconnect(), timeout=DISCONNECT_TIMEOUT_SECONDS
                    )
                elif self._entered_client:
                    await self.client.__aexit__(None, None, None)
        except asyncio.TimeoutError:
            logger.warning("runtime {} disconnect timed out", self.runtime_id[:8])
        except Exception:
            logger.warning("runtime {} disconnect failed", self.runtime_id[:8], exc_info=True)
        if self.receiver_task is not None:
            try:
                await self.receiver_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self.receiver_task = None
        if was_unsolicited:
            if unsolicited_record is not None and unsolicited_record.live:
                run_registry.finish(unsolicited_record, "aborted")
                unsolicited_record.record_event(
                    RUN_END_EVENT, {"status": unsolicited_record.status}
                )
        self._active_queue = None
        self._deferred_frames.clear()
        self._turn_write_capable = False
        self._healthy = False
        self.state = "dead"
        self._cleanup_options_once()


class RuntimeLease:
    def __init__(self, manager: "SessionRuntimePool", runtime: SessionRuntime, client: RuntimeTurnClient):
        self.manager = manager
        self.runtime = runtime
        self.client = client
        self._released = False
        self._release_task: asyncio.Task[bool] | None = None

    async def remap_session(self, session_id: str) -> None:
        await self.manager.remap(self.runtime, session_id)

    async def release(
        self,
        *,
        keep_warm: bool,
        tainted: bool = False,
        preserve_options: bool = False,
    ) -> bool:
        if not self._released:
            self._released = True
            self._release_task = asyncio.create_task(
                self.manager.release(
                    self.runtime,
                    keep_warm=keep_warm,
                    tainted=tainted,
                    preserve_options=preserve_options,
                ),
                name=f"claude-runtime-release-{self.runtime.runtime_id[:8]}",
            )
        assert self._release_task is not None
        return await asyncio.shield(self._release_task)


class SessionMaintenanceLease:
    """Exclusive session reservation for transcript mutation operations."""

    def __init__(
        self,
        manager: "SessionRuntimePool",
        key: str,
        tasks: tuple[asyncio.Task[Any], ...],
    ) -> None:
        self.manager = manager
        self.key = key
        self.tasks = tasks
        self._released = False
        self._release_task: asyncio.Task[None] | None = None

    async def release(self) -> None:
        if self._released:
            if self._release_task is not None:
                await asyncio.shield(self._release_task)
            return
        self._released = True
        self._release_task = asyncio.create_task(
            self.manager._release_maintenance(self.key),
            name=f"claude-session-maintenance-release-{self.key[:8]}",
        )
        await asyncio.shield(self._release_task)

    async def __aenter__(self) -> "SessionMaintenanceLease":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        await self.release()
        return False


class RuntimeClientContext:
    """Async context wrapper that keeps service turn code compact."""

    def __init__(self, manager: "SessionRuntimePool", **acquire_kwargs: Any) -> None:
        self.manager = manager
        self.acquire_kwargs = acquire_kwargs
        self.lease: RuntimeLease | None = None
        self.runtime: SessionRuntime | None = None
        self.keep_warm = True
        self.tainted = False
        self.preserve_options = False
        self.retained = False

    async def __aenter__(self) -> RuntimeTurnClient:
        self.lease = await self.manager.acquire(**self.acquire_kwargs)
        self.runtime = self.lease.runtime
        return self.lease.client

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if self.lease is None:
            return False
        if exc_type is not None:
            self.keep_warm = False
            self.tainted = True
            # The caller's retry loop may reconnect using the same startup
            # options. It performs final cleanup if no retry succeeds.
            self.preserve_options = True
        try:
            self.retained = await self.lease.release(
                keep_warm=self.keep_warm,
                tainted=self.tainted,
                preserve_options=self.preserve_options,
            )
        except asyncio.CancelledError:
            # The shielded release still owns runtime/options cleanup. When a
            # retry explicitly preserves the overlay, ownership instead stays
            # with the caller's retry/finally path.
            self.retained = not self.preserve_options
            raise
        return False


class SessionRuntimePool:
    """Atomic admission plus LRU eviction for resident session runtimes."""

    def __init__(
        self,
        capacity: int | None = None,
        *,
        memory_reader: Callable[[], RuntimeMemorySnapshot] = read_runtime_memory,
    ) -> None:
        self._capacity_override = capacity is not None
        self._memory_reader = memory_reader
        self._last_memory = self._read_memory()
        self._capacity = self._validate_capacity(
            capacity
            if capacity is not None
            else runtime_capacity_for_memory(self._last_memory.limit_bytes)
        )
        self._active_capacity = (
            self._capacity
            if self._capacity_override
            else runtime_active_capacity_for_memory(self._last_memory.limit_bytes)
        )
        self._lock = asyncio.Lock()
        self._runtimes: dict[str, SessionRuntime] = {}
        self._starting: set[str] = set()
        self._starting_tasks: dict[str, asyncio.Task[Any]] = {}
        self._starting_write_scopes: dict[str, tuple[Path, ...]] = {}
        self._maintenance_keys: set[str] = set()
        self._maintenance_tasks: set[asyncio.Task[Any]] = set()
        self._closing = False
        self._loop: asyncio.AbstractEventLoop | None = None

    @staticmethod
    def _validate_capacity(value: int) -> int:
        value = int(value)
        if not 1 <= value <= MAX_SESSION_RUNTIME_POOL_CAPACITY:
            raise ValueError(
                "session pool capacity must be between 1 and "
                f"{MAX_SESSION_RUNTIME_POOL_CAPACITY}"
            )
        return value

    def _read_memory(self) -> RuntimeMemorySnapshot:
        try:
            snapshot = self._memory_reader()
            if not isinstance(snapshot, RuntimeMemorySnapshot):
                raise TypeError("memory_reader must return RuntimeMemorySnapshot")
            return snapshot
        except Exception:
            logger.warning("session runtime memory probe failed", exc_info=True)
            return RuntimeMemorySnapshot(None, None, "fallback")

    @staticmethod
    def _memory_ratio(snapshot: RuntimeMemorySnapshot) -> float | None:
        if not snapshot.limit_bytes or snapshot.current_bytes is None:
            return None
        return snapshot.current_bytes / snapshot.limit_bytes

    @staticmethod
    def _at_hard_memory_limit(snapshot: RuntimeMemorySnapshot) -> bool:
        ratio = SessionRuntimePool._memory_ratio(snapshot)
        return ratio is not None and ratio >= SESSION_RUNTIME_HARD_MEMORY_RATIO

    def _can_start_physical_runtime(
        self,
        snapshot: RuntimeMemorySnapshot,
        *,
        extra_starting: int = 1,
    ) -> bool:
        if not snapshot.limit_bytes or snapshot.current_bytes is None:
            return True
        projected = (
            snapshot.current_bytes
            + (len(self._starting) + extra_starting)
            * SESSION_RUNTIME_ACTIVE_BUDGET_BYTES
        )
        return projected <= int(
            snapshot.limit_bytes * SESSION_RUNTIME_HARD_MEMORY_RATIO
        )

    def _active_count_locked(self, *, exclude: SessionRuntime | None = None) -> int:
        unique = {id(item): item for item in self._runtimes.values()}.values()
        return len(self._starting) + sum(
            runtime is not exclude
            and runtime.state in {"starting", "running", "draining"}
            for runtime in unique
        )

    def _admit_native_peer(self, runtime: SessionRuntime) -> bool:
        """Synchronous guard for a peer turn already accepted by Claude's UDS."""

        snapshot = self._read_memory()
        self._last_memory = snapshot
        ratio = self._memory_ratio(snapshot)
        return (
            not self._closing
            and (ratio is None or ratio < SESSION_RUNTIME_SOFT_MEMORY_RATIO)
            and self._active_count_locked(exclude=runtime) < self._active_capacity
        )

    def _ensure_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            # Primarily protects isolated-loop unit tests. Production has one
            # uvicorn loop for the process lifetime.
            self._runtimes.clear()
            self._starting.clear()
            self._starting_tasks.clear()
            self._starting_write_scopes.clear()
            self._maintenance_keys.clear()
            self._maintenance_tasks.clear()
            self._closing = False
        self._loop = loop

    @property
    def capacity(self) -> int:
        return self._capacity

    def client_context(self, **acquire_kwargs: Any) -> RuntimeClientContext:
        return RuntimeClientContext(self, **acquire_kwargs)

    async def startup(self, capacity: int | None = None) -> None:
        """Initialize a new application lifespan after a prior clean shutdown."""
        self._ensure_loop()
        async with self._lock:
            if self._runtimes or self._starting:
                raise RuntimePoolError("cannot start a non-empty session runtime pool")
            self._maintenance_keys.clear()
            self._closing = False
        if capacity is not None:
            await self.configure(capacity)
        else:
            await self.refresh_capacity()

    async def configure(self, capacity: int) -> None:
        """Set an explicit internal/test override; never exposed in user settings."""
        self._ensure_loop()
        new_capacity = self._validate_capacity(capacity)
        self._capacity_override = True
        self._active_capacity = new_capacity
        await self._apply_capacity(new_capacity)

    async def _apply_capacity(self, new_capacity: int) -> None:
        victims: list[SessionRuntime] = []
        async with self._lock:
            self._capacity = new_capacity
            idle = sorted(
                (
                    runtime
                    for runtime in self._runtimes.values()
                    if runtime.evictable or not runtime.healthy
                ),
                key=lambda runtime: runtime.last_used,
            )
            while len(self._runtimes) - len(victims) > new_capacity and idle:
                victim = idle.pop(0)
                victims.append(victim)
                self._remove_locked(victim)
            if len(self._runtimes) > new_capacity:
                for runtime in self._runtimes.values():
                    if not runtime.evictable:
                        runtime._retire_on_idle = True
        await asyncio.gather(*(victim.close() for victim in victims), return_exceptions=True)

    async def refresh_capacity(
        self,
        *,
        preserve_key: str | None = None,
    ) -> RuntimeMemorySnapshot:
        """Re-evaluate cgroup sizing and release warm entries under pressure."""

        self._ensure_loop()
        snapshot = self._read_memory()
        self._last_memory = snapshot
        target = (
            self._capacity
            if self._capacity_override
            else runtime_capacity_for_memory(snapshot.limit_bytes)
        )
        active_target = (
            self._active_capacity
            if self._capacity_override
            else runtime_active_capacity_for_memory(snapshot.limit_bytes)
        )
        victims: list[SessionRuntime] = []
        ratio = self._memory_ratio(snapshot)
        async with self._lock:
            self._capacity = self._validate_capacity(target)
            self._active_capacity = max(1, min(self._capacity, active_target))
            unique = list(
                {id(runtime): runtime for runtime in self._runtimes.values()}.values()
            )
            idle = sorted(
                (
                    runtime
                    for runtime in unique
                    if (runtime.evictable or not runtime.healthy)
                    and runtime.key != preserve_key
                ),
                key=lambda runtime: runtime.last_used,
            )

            # Capacity shrink keeps active work intact and retires it at the
            # next clean boundary. Idle overflow can be closed immediately.
            resident_after = len(unique)
            while resident_after > self._capacity and idle:
                victim = idle.pop(0)
                self._remove_locked(victim)
                victims.append(victim)
                resident_after -= 1
            if resident_after > self._capacity:
                for runtime in unique:
                    if not runtime.evictable:
                        runtime._retire_on_idle = True

            # At the soft watermark warm cache is expendable. Close every idle
            # entry except the exact session currently being acquired; the hard
            # watermark below still decides whether that session may run.
            if ratio is not None and ratio >= SESSION_RUNTIME_SOFT_MEMORY_RATIO:
                for victim in idle:
                    self._remove_locked(victim)
                    victims.append(victim)

        await asyncio.gather(
            *(victim.close() for victim in victims),
            return_exceptions=True,
        )
        return snapshot

    async def acquire(
        self,
        *,
        key: str,
        options: Any,
        bridge: PermissionBridge,
        permission_callback: Callable[[str, dict[str, Any], Any], Awaitable[Any]],
        coordinator: Any,
        cancelled: asyncio.Event | None,
        client_factory: Callable[[Any], Any],
        cleanup_options: Callable[[Any], None],
    ) -> RuntimeLease:
        self._ensure_loop()
        snapshot = await self.refresh_capacity(preserve_key=key)
        fingerprint = options_fingerprint(options)
        turn_permission_mode = str(
            getattr(options, "permission_mode", "default") or "default"
        )
        turn_write_capable = turn_permission_mode != IDLE_PERMISSION_MODE
        turn_write_scope = _write_scope(options)
        victim: SessionRuntime | None = None
        reusable: SessionRuntime | None = None

        async with self._lock:
            if self._closing:
                cleanup_options(options)
                raise RuntimePoolShuttingDownError("session runtime pool is shutting down")
            if key in self._maintenance_keys:
                cleanup_options(options)
                raise SessionRuntimeBusyError(
                    f"session runtime {key} is reserved for maintenance"
                )
            if key in self._starting:
                cleanup_options(options)
                raise SessionRuntimeBusyError(f"session runtime {key} is starting")
            existing = self._runtimes.get(key)
            if self._at_hard_memory_limit(snapshot):
                cleanup_options(options)
                raise RuntimePoolMemoryPressureError(
                    "session runtime admission paused at the container memory "
                    "hard watermark"
                )
            if existing is not None and existing.healthy and not existing.evictable:
                cleanup_options(options)
                raise SessionRuntimeBusyError(
                    f"session runtime {key} is {existing.state}"
                )
            if self._active_count_locked() >= self._active_capacity:
                cleanup_options(options)
                raise RuntimePoolCapacityError(
                    f"all {self._active_capacity} active turn slots are in use"
                )
            if turn_write_capable:
                for runtime in {
                    id(item): item for item in self._runtimes.values()
                }.values():
                    if runtime._turn_write_capable and _write_scopes_overlap(
                        turn_write_scope, runtime.write_scope
                    ):
                        cleanup_options(options)
                        raise RuntimeWriteScopeBusyError(
                            "another writable turn owns an overlapping workspace"
                        )
                for starting_scope in self._starting_write_scopes.values():
                    if _write_scopes_overlap(turn_write_scope, starting_scope):
                        cleanup_options(options)
                        raise RuntimeWriteScopeBusyError(
                            "another writable turn is starting in an overlapping workspace"
                        )
            if existing is not None:
                if not existing.healthy:
                    victim = existing
                    self._remove_locked(existing)
                    self._starting.add(key)
                    current_task = asyncio.current_task()
                    if current_task is not None:
                        self._starting_tasks[key] = current_task
                elif not existing.evictable:
                    cleanup_options(options)
                    raise SessionRuntimeBusyError(f"session runtime {key} is {existing.state}")
                elif existing.fingerprint == fingerprint:
                    # Reserve under the manager lock.  Without this transition,
                    # two acquires can both observe IDLE and the losing request
                    # would discard the winner's now-running runtime.
                    existing.state = "starting"
                    existing._turn_write_capable = turn_write_capable
                    reusable = existing
                else:
                    victim = existing
                    self._remove_locked(existing)
                    self._starting.add(key)
                    current_task = asyncio.current_task()
                    if current_task is not None:
                        self._starting_tasks[key] = current_task
            else:
                resident = len(self._runtimes) + len(self._starting)
                if resident >= self._capacity:
                    idle = sorted(
                        (
                            runtime
                            for runtime in self._runtimes.values()
                            if runtime.evictable or not runtime.healthy
                        ),
                        key=lambda runtime: runtime.last_used,
                    )
                    if not idle:
                        cleanup_options(options)
                        raise RuntimePoolCapacityError(
                            f"all {self._capacity} session runtime slots are active"
                        )
                    victim = idle[0]
                    self._remove_locked(victim)
                if victim is None and not self._can_start_physical_runtime(snapshot):
                    cleanup_options(options)
                    raise RuntimePoolMemoryPressureError(
                        "starting another session runtime would cross the "
                        "container memory hard watermark"
                    )
                self._starting.add(key)
                current_task = asyncio.current_task()
                if current_task is not None:
                    self._starting_tasks[key] = current_task
            if key in self._starting and turn_write_capable:
                self._starting_write_scopes[key] = turn_write_scope

        if reusable is not None:
            cleanup_options(options)
            try:
                client = await reusable.start_turn(
                    permission_callback,
                    coordinator,
                    cancelled,
                    turn_permission_mode,
                )
                return RuntimeLease(self, reusable, client)
            except BaseException:
                await self._discard(reusable)
                raise

        if victim is not None:
            try:
                await victim.close()
            except BaseException:
                # The victim is already absent from the resident map and the
                # replacement slot is reserved in ``_starting``. Cancellation
                # during eviction must neither leave a permanent ghost slot nor
                # make the slot reusable while the old CLI is still closing.
                cleanup_options(options)

                async def finish_cancelled_eviction() -> None:
                    try:
                        await victim.close()
                    finally:
                        async with self._lock:
                            self._starting.discard(key)
                            self._starting_tasks.pop(key, None)
                            self._starting_write_scopes.pop(key, None)

                maintenance = asyncio.create_task(
                    finish_cancelled_eviction(),
                    name=f"claude-runtime-evict-{victim.runtime_id[:8]}",
                )
                self._maintenance_tasks.add(maintenance)
                maintenance.add_done_callback(self._maintenance_tasks.discard)
                raise

        runtime = SessionRuntime(
            key=key,
            options=options,
            bridge=bridge,
            fingerprint=fingerprint,
            client_factory=client_factory,
            cleanup_options=cleanup_options,
            retire_callback=self._retire_dead_runtime,
            peer_admission=self._admit_native_peer,
        )
        try:
            await runtime.connect()
            client = await runtime.start_turn(
                permission_callback,
                coordinator,
                cancelled,
                turn_permission_mode,
            )
        except BaseException:
            # The service retry loop still owns these startup options until an
            # acquire succeeds; keep its overlay available for reconnect. A
            # shutdown-induced cancellation is terminal, so the pool performs
            # final cleanup itself before reporting shutdown complete.
            await runtime.close(cleanup_options=self._closing)
            async with self._lock:
                self._starting.discard(key)
                self._starting_tasks.pop(key, None)
                self._starting_write_scopes.pop(key, None)
            raise

        async with self._lock:
            self._starting.discard(key)
            self._starting_tasks.pop(key, None)
            self._starting_write_scopes.pop(key, None)
            if self._closing:
                close_after = True
                close_for_maintenance = False
            elif key in self._maintenance_keys:
                close_after = True
                close_for_maintenance = True
            elif key in self._runtimes:
                close_after = True
                close_for_maintenance = False
            else:
                # Capacity can be reduced while this CLI is still connecting.
                # Let the already-admitted user turn start, but retire the
                # excess runtime as soon as that turn becomes quiescent.
                if len(self._runtimes) >= self._capacity:
                    runtime._retire_on_idle = True
                self._runtimes[key] = runtime
                runtime._resident = True
                close_after = False
                close_for_maintenance = False
        if close_after:
            await runtime.close()
            if close_for_maintenance:
                raise SessionRuntimeBusyError(
                    f"session runtime {key} was reserved during startup"
                )
            raise RuntimePoolShuttingDownError("session runtime pool stopped during startup")
        return RuntimeLease(self, runtime, client)

    async def remap(self, runtime: SessionRuntime, session_id: str) -> None:
        if not session_id:
            return
        async with self._lock:
            current = self._runtimes.get(runtime.key)
            if current is not runtime:
                raise RuntimeDisconnectedError("runtime is no longer resident")
            occupied = self._runtimes.get(session_id)
            if occupied is not None and occupied is not runtime:
                runtime._healthy = False
                raise RuntimeSessionCollisionError(
                    f"session id {session_id} is already owned by another runtime"
                )
            self._runtimes.pop(runtime.key, None)
            runtime.key = session_id
            runtime.session_id = session_id
            self._runtimes[session_id] = runtime

    async def release(
        self,
        runtime: SessionRuntime,
        *,
        keep_warm: bool,
        tainted: bool,
        preserve_options: bool = False,
    ) -> bool:
        try:
            await runtime.finish_turn(tainted=tainted, keep_warm=keep_warm)
        except BaseException:
            # A failed security transition must never leave a resident runtime
            # with a stale permission binding. Retire it through the same
            # exactly-once physical close path before surfacing the failure.
            async with self._lock:
                self._remove_locked(runtime)
            await runtime.close(cleanup_options=not preserve_options)
            raise
        close_runtime = (
            not keep_warm
            or not runtime.healthy
            or runtime._retire_on_idle
            or self._closing
        )
        async with self._lock:
            if close_runtime:
                self._remove_locked(runtime)
            elif len(self._runtimes) > self._capacity:
                self._remove_locked(runtime)
                close_runtime = True
        if close_runtime:
            await runtime.close(cleanup_options=not preserve_options)
        return not close_runtime

    async def recycle_all(self) -> None:
        """Apply startup-only setting changes without disturbing live turns."""
        self._ensure_loop()
        victims: list[SessionRuntime] = []
        async with self._lock:
            for runtime in list(self._runtimes.values()):
                if runtime.evictable or not runtime.healthy:
                    self._remove_locked(runtime)
                    victims.append(runtime)
                else:
                    runtime._retire_on_idle = True
        await asyncio.gather(*(runtime.close() for runtime in victims), return_exceptions=True)

    def owns_native_record(self, record: RunRecord) -> bool:
        """Whether a warm runtime has adopted this record for a peer turn."""
        return any(
            runtime._unsolicited_record is record
            for runtime in {id(item): item for item in self._runtimes.values()}.values()
        )

    async def reserve_session(
        self,
        session_id: str,
        *,
        cancel_active: bool,
        grace_seconds: float = 8.0,
    ) -> SessionMaintenanceLease | None:
        """Reserve a session until a caller finishes transcript mutation.

        The reservation is acquired before inspecting runtime state, closing
        the check-to-mutate race with WS/HTTP admission. ``None`` means an
        active owner prevented a non-destructive operation such as rewind.
        """
        self._ensure_loop()
        close_now: SessionRuntime | None = None
        tasks: dict[int, asyncio.Task[Any]] = {}
        async with self._lock:
            if self._closing:
                raise RuntimePoolShuttingDownError(
                    "session runtime pool is shutting down"
                )
            if session_id in self._maintenance_keys:
                return None
            self._maintenance_keys.add(session_id)
            runtime = self._runtimes.get(session_id)
            record = run_registry.live_for_session(session_id)
            busy = (
                session_id in self._starting
                or record is not None
                or (runtime is not None and not runtime.evictable)
            )
            if busy and not cancel_active:
                self._maintenance_keys.discard(session_id)
                return None
            if record is not None:
                record.cancelled.set()
                if record.task is not None and not record.task.done():
                    tasks[id(record.task)] = record.task
            if runtime is not None:
                runtime._retire_on_idle = True
                if runtime.evictable:
                    self._remove_locked(runtime)
                    close_now = runtime
                else:
                    runtime.request_cancel()

        try:
            if close_now is not None:
                await close_now.close()

            if cancel_active:
                deadline = time.monotonic() + max(0.0, grace_seconds)
                victim: SessionRuntime | None = None
                while True:
                    async with self._lock:
                        starting = session_id in self._starting
                        current = self._runtimes.get(session_id)
                        record = run_registry.live_for_session(session_id)
                        if record is not None:
                            record.cancelled.set()
                            if record.task is not None and not record.task.done():
                                tasks[id(record.task)] = record.task
                        if current is not None:
                            current._retire_on_idle = True
                            current.request_cancel()
                        if not starting and (
                            current is None or current.evictable or not current.healthy
                        ):
                            if current is not None:
                                self._remove_locked(current)
                                victim = current
                            break
                        if time.monotonic() >= deadline:
                            if current is not None:
                                self._remove_locked(current)
                                victim = current
                            break
                    await asyncio.sleep(0.05)
                if victim is not None:
                    await victim.close()

            return SessionMaintenanceLease(
                self,
                session_id,
                tuple(tasks.values()),
            )
        except BaseException:
            await self._release_maintenance(session_id)
            raise

    async def _release_maintenance(self, session_id: str) -> None:
        async with self._lock:
            self._maintenance_keys.discard(session_id)

    async def _discard(self, runtime: SessionRuntime) -> None:
        async with self._lock:
            self._remove_locked(runtime)
        await runtime.close()

    async def _retire_dead_runtime(self, runtime: SessionRuntime) -> None:
        """Reap an EOF/poisoned runtime without waiting for another acquire."""
        await runtime.close()
        async with self._lock:
            self._remove_locked(runtime)

    def _remove_locked(self, runtime: SessionRuntime) -> None:
        runtime._resident = False
        for key, item in list(self._runtimes.items()):
            if item is runtime:
                self._runtimes.pop(key, None)

    async def shutdown(self, *, grace_seconds: float = 8.0) -> None:
        self._ensure_loop()
        async with self._lock:
            self._closing = True
            runtimes = list({id(runtime): runtime for runtime in self._runtimes.values()}.values())
            for runtime in runtimes:
                runtime.request_cancel()
        deadline = time.monotonic() + grace_seconds
        while (
            any(runtime.state in {"running", "draining"} for runtime in runtimes)
            or self._starting
        ):
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.05)
        async with self._lock:
            starting_tasks = list(
                {
                    id(task): task
                    for task in self._starting_tasks.values()
                    if task is not asyncio.current_task()
                }.values()
            )
        for task in starting_tasks:
            if not task.done():
                task.cancel()
        if starting_tasks:
            await asyncio.gather(*starting_tasks, return_exceptions=True)
        async with self._lock:
            self._runtimes.clear()
            self._starting.clear()
            self._starting_tasks.clear()
            self._starting_write_scopes.clear()
            self._maintenance_keys.clear()
            maintenance = list(self._maintenance_tasks)
        await asyncio.gather(
            *(runtime.close() for runtime in runtimes),
            *maintenance,
            return_exceptions=True,
        )

    def stats(self) -> dict[str, Any]:
        snapshot = self._read_memory()
        self._last_memory = snapshot
        memory_ratio = self._memory_ratio(snapshot)
        runtimes = list({id(runtime): runtime for runtime in self._runtimes.values()}.values())
        observed_capabilities = [
            runtime.list_agents_capable
            for runtime in runtimes
            if runtime.list_agents_capable is not None
        ]
        return {
            "capacity": self._capacity,
            "active_capacity": self._active_capacity,
            "capacity_mode": "fixed-test" if self._capacity_override else "cgroup",
            "computed_capacity": runtime_capacity_for_memory(snapshot.limit_bytes),
            "computed_active_capacity": runtime_active_capacity_for_memory(
                snapshot.limit_bytes
            ),
            "resident": len(runtimes),
            "idle": sum(runtime.state == "idle" for runtime in runtimes),
            "active": sum(runtime.state in {"running", "draining"} for runtime in runtimes),
            "closing": self._closing,
            "list_agents_capable": (
                any(observed_capabilities) if observed_capabilities else None
            ),
            "memory": {
                "source": snapshot.source,
                "limit_bytes": snapshot.limit_bytes,
                "current_bytes": snapshot.current_bytes,
                "ratio": round(memory_ratio, 4) if memory_ratio is not None else None,
                "soft_ratio": SESSION_RUNTIME_SOFT_MEMORY_RATIO,
                "hard_ratio": SESSION_RUNTIME_HARD_MEMORY_RATIO,
            },
        }


session_runtime_pool = SessionRuntimePool()
