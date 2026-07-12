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
import time
import uuid
from typing import Any

from priva_common.logging import get_app_logger

from ... import activity

logger = get_app_logger(__name__)

# Events kept per run for attach replay. A run past this many events keeps
# only the tail (first_seq advances); attach then replays what remains.
MAX_BUFFERED_EVENTS = 4000
# Terminal records stay attachable for this long, then sweep out.
TERMINAL_TTL_SECONDS = 600

# Internal sentinel recorded when the run task finishes — followers drain up
# to it and close their sockets. Never sent to clients.
RUN_END_EVENT = "__run_end__"


class RunRecord:
    def __init__(self, run_id: str, session_id: str | None = None):
        self.run_id = run_id
        self.session_id = session_id
        self.cancelled = asyncio.Event()
        # Same out-param protocol agent_run_events already speaks.
        self.coordinator_out: list = [None]
        self.queue_out: list = [None]
        self.task: asyncio.Task | None = None
        self.events: list[tuple[int, str, dict]] = []
        self.first_seq = 1
        self.next_seq = 1
        self.subscribers: dict[str, asyncio.Queue] = {}
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
        return set(coord.pending.keys()) if coord and getattr(coord, "pending", None) else set()

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
        self.events.append((seq, event_type, data))
        if len(self.events) > MAX_BUFFERED_EVENTS:
            del self.events[: len(self.events) - MAX_BUFFERED_EVENTS]
            self.first_seq = self.events[0][0]
        for q in list(self.subscribers.values()):
            try:
                q.put_nowait((seq, event_type, data))
            except Exception:  # noqa: BLE001 — a wedged subscriber must not stall the run
                pass

    def subscribe(self) -> tuple[str, asyncio.Queue]:
        sub_id = uuid.uuid4().hex[:8]
        q: asyncio.Queue = asyncio.Queue()
        self.subscribers[sub_id] = q
        return sub_id, q

    def unsubscribe(self, sub_id: str) -> None:
        self.subscribers.pop(sub_id, None)

    def replay_since(self, since_seq: int) -> list[tuple[int, str, dict]]:
        return [e for e in self.events if e[0] > since_seq]


class RunRegistry:
    def __init__(self) -> None:
        self._by_run_id: dict[str, RunRecord] = {}
        self._by_session_id: dict[str, RunRecord] = {}

    def create(self, session_id: str | None = None, run_id: str | None = None) -> RunRecord:
        """New registry-owned record. ``run_id`` lets a caller with an external
        identity (the scheduler's minted run_id, D13) key the record by it."""
        record = RunRecord(run_id=run_id or str(uuid.uuid4()), session_id=session_id)
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
        activity.leave()
        logger.info(
            "[RUNREG] finished run_id={} session_id={} status={} events={}",
            record.run_id, record.session_id, status, record.next_seq - 1,
        )

    def sweep(self) -> None:
        now = time.time()
        stale = [
            r for r in set(self._by_run_id.values()) | set(self._by_session_id.values())
            if r.ended_at is not None and now - r.ended_at > TERMINAL_TTL_SECONDS
        ]
        for record in stale:
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
                "first_user_uuid": record.first_user_uuid,
                "pending_permission": record.pending_permission,
            })
        return out


run_registry = RunRegistry()
