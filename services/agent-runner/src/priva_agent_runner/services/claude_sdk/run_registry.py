"""In-process registry of live agent runs — the WS detach/attach backbone.

A run used to live and die with its WebSocket: the socket's close cancelled
the run within ~2s. Runs are now owned by a :class:`RunRecord` here instead:

* ``ws_run`` spawns the run as a registry-owned asyncio task; every emitted
  event is appended to the record's seq-numbered buffer and fanned out to
  subscriber queues (one per attached socket).
* A socket dying merely unsubscribes it — the run keeps executing, and the
  record holds an ``activity`` slot so the operator's idle sweep can't sleep
  the pod mid-run.
* A later ``attach`` frame (page refresh, second browser) replays the buffer
  from the client's ``since_seq`` and then follows live.
* Only an explicit ``abort`` frame — or run completion — ends a run.

Single-process uvicorn + one pod per user (the operator is the sole 0↔1
scaler), so a module-level dict needs no cross-process coordination — same
pattern as ``permission_coordinator.registry``.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from priva_common.logging import get_app_logger

from ... import activity
from .bounded_queue import BoundedAsyncQueue

logger = get_app_logger(__name__)

# Events kept per run for attach replay. A run past this many events keeps
# only the tail (first_seq advances); attach then replays what remains.
MAX_BUFFERED_EVENTS = 4000
# A handful of very large tool payloads must not bypass the count limit.
MAX_BUFFERED_BYTES = 8 * 1024 * 1024
# A detached or throttled browser cannot accumulate an unbounded fan-out queue.
MAX_SUBSCRIBER_EVENTS = 256
MAX_SUBSCRIBER_BYTES = 4 * 1024 * 1024
# Terminal records stay attachable for this long, then sweep out.
TERMINAL_TTL_SECONDS = 600
# A burst of completed large runs must not retain N × 8 MiB until that TTL.
MAX_TERMINAL_RECORDS = 32
MAX_TERMINAL_BYTES = 32 * 1024 * 1024

# Internal sentinel recorded when the run task finishes — followers drain up
# to it and close their sockets. Never sent to clients.
RUN_END_EVENT = "__run_end__"
SUBSCRIBER_OVERFLOW_EVENT = "__subscriber_overflow__"


class RunAlreadyActiveError(RuntimeError):
    """A logical session/run id already has an in-flight owner."""


class RunRecord:
    def __init__(
        self,
        run_id: str,
        session_id: str | None = None,
        run_mode: str = "agent",
    ):
        self.run_id = run_id
        self.session_id = session_id
        self.run_mode = run_mode
        self.cancelled = asyncio.Event()
        # Same out-param protocol agent_run_events already speaks.
        self.coordinator_out: list = [None]
        self.queue_out: list = [None]
        self.task: asyncio.Task | None = None
        self.events: list[tuple[int, str, dict]] = []
        self._event_sizes: list[int] = []
        self._event_bytes = 0
        self.first_seq = 1
        self.next_seq = 1
        self.subscribers: dict[
            str,
            BoundedAsyncQueue[tuple[int, str, dict]],
        ] = {}
        self.status = "running"  # running | completed | error | aborted
        self.started_at = time.time()
        self.ended_at: float | None = None
        # uuid of the run's first user_message event — lets the client cut its
        # JSONL snapshot at the run boundary before replaying the run's events.
        self.first_user_uuid: str | None = None

    @property
    def live(self) -> bool:
        return self.status == "running"

    @property
    def pending_permission(self) -> bool:
        coord = self.coordinator_out[0]
        return bool(coord and getattr(coord, "pending", None))

    def outstanding_permission_ids(self) -> set[str]:
        coord = self.coordinator_out[0]
        if not coord or not getattr(coord, "pending", None):
            return set()
        return {
            request_id
            for request_id, future in coord.pending.items()
            if not callable(getattr(future, "done", None)) or not future.done()
        }

    def outstanding_permission_requests(self) -> list[dict[str, Any]]:
        """Return authoritative permission payloads for a newly attached UI.

        Real coordinators retain these independently of the bounded event
        buffer. The event scan is only a compatibility path for lightweight
        test/dummy coordinators that expose ``pending`` but no snapshot API.
        """
        coord = self.coordinator_out[0]
        snapshotter = getattr(coord, "pending_request_snapshots", None)
        if callable(snapshotter):
            return snapshotter()
        outstanding = self.outstanding_permission_ids()
        if not outstanding:
            return []
        requests: dict[str, dict[str, Any]] = {}
        for _seq, event_type, data in self.events:
            request_id = data.get("request_id") if isinstance(data, dict) else None
            if event_type == "permission_request" and request_id in outstanding:
                requests[request_id] = dict(data)
        return list(requests.values())

    def queued_entries(self) -> list[dict[str, str]]:
        q = self.queue_out[0]
        if q is None:
            return []
        # asyncio.Queue has no peek; its internal deque is stable CPython.
        entries = list(getattr(q, "_queue", []))
        return [{"id": e[0], "text": e[1]} for e in entries if isinstance(e, tuple) and len(e) >= 2]

    def record_event(self, event_type: str, data: dict) -> None:
        """Buffer an event and fan it out to every attached socket.

        Synchronous on purpose: it must be callable from a task's ``finally``
        during cancellation (an ``await`` there would re-raise CancelledError
        and drop the run-end sentinel).
        """
        if event_type == "keepalive":
            return  # followers generate their own keepalives
        if (
            event_type == "user_message"
            and self.first_user_uuid is None
            and isinstance(data, dict)
            and data.get("uuid")
        ):
            self.first_user_uuid = data["uuid"]
        seq = self.next_seq
        self.next_seq += 1
        try:
            event_size = len(
                json.dumps(
                    {"event": event_type, "data": data},
                    ensure_ascii=False,
                    default=str,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
        except Exception:
            event_size = len(repr(data).encode("utf-8", errors="replace"))
        self.events.append((seq, event_type, data))
        self._event_sizes.append(event_size)
        self._event_bytes += event_size
        trim = 0
        while (
            len(self.events) - trim > MAX_BUFFERED_EVENTS
            or self._event_bytes > MAX_BUFFERED_BYTES
        ):
            self._event_bytes -= self._event_sizes[trim]
            trim += 1
        if trim:
            del self.events[:trim]
            del self._event_sizes[:trim]
            self.first_seq = self.events[0][0] if self.events else self.next_seq
        for sub_id, q in list(self.subscribers.items()):
            try:
                q.put_nowait((seq, event_type, data))
            except asyncio.QueueFull:
                # Terminate only this slow follower. The run remains live and
                # the browser can reattach from its last acknowledged seq.
                self.subscribers.pop(sub_id, None)
                while not q.empty():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                # seq=0 is an internal marker, never an acknowledgement. The
                # follower sends an unsequenced error so reconnect starts from
                # the last event the browser actually received.
                q.put_nowait((0, SUBSCRIBER_OVERFLOW_EVENT, {}))

    def subscribe(
        self,
    ) -> tuple[str, BoundedAsyncQueue[tuple[int, str, dict]]]:
        sub_id = uuid.uuid4().hex[:8]
        q = BoundedAsyncQueue[tuple[int, str, dict]](
            maxsize=MAX_SUBSCRIBER_EVENTS,
            max_bytes=MAX_SUBSCRIBER_BYTES,
        )
        self.subscribers[sub_id] = q
        return sub_id, q

    def unsubscribe(self, sub_id: str) -> None:
        self.subscribers.pop(sub_id, None)

    def replay_since(self, since_seq: int) -> list[tuple[int, str, dict]]:
        return [e for e in self.events if e[0] > since_seq]

    @property
    def buffered_bytes(self) -> int:
        return self._event_bytes

    def has_replay_gap(self, since_seq: int) -> bool:
        """Whether the requested next event predates the retained tail."""
        return since_seq < self.first_seq - 1


class RunRegistry:
    def __init__(self) -> None:
        self._by_run_id: dict[str, RunRecord] = {}
        self._by_session_id: dict[str, RunRecord] = {}

    def create(
        self,
        session_id: str | None = None,
        run_id: str | None = None,
        run_mode: str = "agent",
    ) -> RunRecord:
        """New registry-owned record. ``run_id`` lets a caller with an external
        identity (the scheduler's minted run_id, D13) key the record by it."""
        self.sweep()
        if run_id:
            existing_run = self._by_run_id.get(run_id)
            if existing_run is not None and existing_run.live:
                raise RunAlreadyActiveError(f"run {run_id} is already active")
        if session_id:
            existing_session = self._by_session_id.get(session_id)
            if existing_session is not None and existing_session.live:
                raise RunAlreadyActiveError(
                    f"session {session_id} already has run {existing_session.run_id}"
                )
        record = RunRecord(
            run_id=run_id or str(uuid.uuid4()),
            session_id=session_id,
            run_mode=run_mode,
        )
        self._by_run_id[record.run_id] = record
        if session_id:
            self._by_session_id[session_id] = record
        # The record — not any socket — keeps the pod awake for the run's life.
        activity.enter()
        logger.info("[RUNREG] created run_id={} session_id={}", record.run_id, session_id)
        return record

    def index_run_id(self, record: RunRecord, run_id: str) -> None:
        """Adopt the stream_id agent_run_events minted as an extra lookup key."""
        if run_id and run_id not in self._by_run_id:
            self._by_run_id[run_id] = record

    def index_session(self, record: RunRecord, session_id: str) -> None:
        if not session_id:
            return
        occupied = self._by_session_id.get(session_id)
        if occupied is not None and occupied is not record and occupied.live:
            raise RunAlreadyActiveError(
                f"session {session_id} already has run {occupied.run_id}"
            )
        if record.session_id and record.session_id != session_id:
            self._by_session_id.pop(record.session_id, None)
        record.session_id = session_id
        self._by_session_id[session_id] = record

    def get(self, session_id: str | None = None, run_id: str | None = None) -> RunRecord | None:
        self.sweep()
        if run_id and run_id in self._by_run_id:
            return self._by_run_id[run_id]
        if session_id and session_id in self._by_session_id:
            return self._by_session_id[session_id]
        return None

    def live_for_session(self, session_id: str) -> RunRecord | None:
        record = self.get(session_id=session_id)
        return record if record and record.live else None

    def finish(self, record: RunRecord, status: str) -> None:
        if record.status != "running":
            return
        record.status = status
        record.ended_at = time.time()
        # Terminal replay needs only the bounded event tail. Drop references to
        # completed coroutine frames, permission Futures and queued attachments.
        record.task = None
        record.coordinator_out[0] = None
        record.queue_out[0] = None
        activity.leave()
        logger.info(
            "[RUNREG] finished run_id={} session_id={} status={} events={}",
            record.run_id, record.session_id, status, record.next_seq - 1,
        )
        self.sweep()

    def sweep(self) -> None:
        now = time.time()
        records = set(self._by_run_id.values()) | set(self._by_session_id.values())
        terminal = sorted(
            (record for record in records if record.ended_at is not None),
            key=lambda record: record.ended_at or 0,
        )
        stale: set[RunRecord] = {
            record
            for record in terminal
            if now - (record.ended_at or now) > TERMINAL_TTL_SECONDS
        }
        retained = [record for record in terminal if record not in stale]
        retained_bytes = sum(record.buffered_bytes for record in retained)
        while (
            len(retained) > MAX_TERMINAL_RECORDS
            or retained_bytes > MAX_TERMINAL_BYTES
        ):
            record = retained.pop(0)
            retained_bytes -= record.buffered_bytes
            stale.add(record)
        for record in stale:
            self._drop(record)

    def _drop(self, record: RunRecord) -> None:
        for key, rec in list(self._by_run_id.items()):
            if rec is record:
                self._by_run_id.pop(key, None)
        for key, rec in list(self._by_session_id.items()):
            if rec is record:
                self._by_session_id.pop(key, None)

    def list_active(self) -> list[dict[str, Any]]:
        self.sweep()
        out = []
        for record in {id(r): r for r in self._by_run_id.values()}.values():
            if not record.live:
                continue
            out.append({
                "session_id": record.session_id,
                "run_id": record.run_id,
                "status": record.status,
                "started_at": record.started_at,
                "last_seq": record.next_seq - 1,
                "first_seq": record.first_seq,
                "first_user_uuid": record.first_user_uuid,
                "pending_permission": record.pending_permission,
                "run_mode": record.run_mode,
            })
        return out

    def cancel_active(self) -> list[asyncio.Task]:
        """Signal every detached run and return the live owner tasks.

        Lifespan shutdown calls this before closing the runtime pool. A record
        can exist before its SDK acquire finishes, so cancelling only resident
        runtimes would miss that startup window and leave the task to abrupt
        event-loop cancellation.
        """
        tasks: dict[int, asyncio.Task] = {}
        records = {
            id(record): record
            for record in (*self._by_run_id.values(), *self._by_session_id.values())
        }.values()
        for record in records:
            if not record.live:
                continue
            record.cancelled.set()
            if record.task is not None and not record.task.done():
                tasks[id(record.task)] = record.task
        return list(tasks.values())

    def finish_cancelled(self) -> None:
        """Finalize any orphan record left after shutdown task draining."""
        records = {
            id(record): record
            for record in (*self._by_run_id.values(), *self._by_session_id.values())
        }.values()
        for record in records:
            if record.live and record.cancelled.is_set():
                self.finish(record, "aborted")
                record.record_event(RUN_END_EVENT, {"status": record.status})


run_registry = RunRegistry()
