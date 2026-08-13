from __future__ import annotations

import asyncio
import uuid
from typing import Any

from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

from priva_common.logging import get_app_logger

from .bounded_queue import BoundedAsyncQueue

logger = get_app_logger(__name__)

_MAX_SETTLED_REQUESTS = 256


class PermissionCoordinator:
    """Bridges SDK can_use_tool callbacks to the SSE stream for frontend approval."""

    def __init__(
        self,
        session_id: str,
        event_queue: (
            asyncio.Queue[dict[str, Any] | None]
            | BoundedAsyncQueue[dict[str, Any] | None]
        ),
        *,
        owner_username: str | None = None,
    ):
        self.session_id = session_id
        self.event_queue = event_queue
        self.owner_username = owner_username
        self.pending: dict[str, asyncio.Future[PermissionResultAllow | PermissionResultDeny]] = {}
        # The replay buffer is intentionally bounded, so it cannot be the
        # authority for approval prompts. Keep the request payload beside its
        # Future until the request is resolved or times out; an attaching WS
        # can then restore every outstanding card even after a replay gap.
        self.pending_requests: dict[str, dict[str, Any]] = {}
        self._control_event_tasks: set[asyncio.Task[None]] = set()
        # A second browser tab may answer just after the first one. Remember a
        # small bounded tail so that duplicate responses are idempotent while
        # truly unknown request ids still fail closed.
        self._settled_requests: dict[str, str] = {}
        try:
            from priva_common.config import get_settings
            self.timeout = get_settings().agent.permission_timeout_seconds
        except Exception:
            self.timeout = 600

    async def request_permission(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        context: Any,
        *,
        risky: bool = False,
        matched_rule: str | None = None,
        reason: str | None = None,
        kind: str = "permission",
    ) -> PermissionResultAllow | PermissionResultDeny:
        request_id = str(uuid.uuid4())
        tool_use_id = getattr(context, "tool_use_id", None)
        logger.info("[PERM] request_permission request_id={} session_id={} tool={} risky={} queue_id={}",
                    request_id, self.session_id, tool_name, risky, id(self.event_queue))
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.pending[request_id] = future
        request_data = {
            "request_id": request_id,
            "tool_use_id": tool_use_id,
            "tool_name": tool_name,
            "input": tool_input,
            "session_id": self.session_id,
            "risky": risky,
            "matched_rule": matched_rule,
            "reason": reason,
            "kind": kind,
        }
        self.pending_requests[request_id] = request_data

        await self.event_queue.put({
            "event": "permission_request",
            "data": request_data,
        })

        try:
            return await asyncio.wait_for(future, timeout=self.timeout)
        except asyncio.TimeoutError:
            self._mark_settled(request_id, "timeout")
            await self.event_queue.put({
                "event": "permission_timeout",
                "data": {
                    "request_id": request_id,
                    "tool_use_id": tool_use_id,
                    "tool_name": tool_name,
                    "session_id": self.session_id,
                },
            })
            return PermissionResultDeny(message="user did not answer")
        finally:
            self.pending.pop(request_id, None)
            self.pending_requests.pop(request_id, None)

    def pending_request_snapshots(self) -> list[dict[str, Any]]:
        """Return the authoritative set of requests that can still be answered."""
        snapshots: list[dict[str, Any]] = []
        for request_id, data in self.pending_requests.items():
            future = self.pending.get(request_id)
            if future is None or future.done():
                continue
            snapshot = dict(data)
            # A legacy stream id can be remapped after system.init. Always
            # expose the coordinator's current address to a reattached client.
            snapshot["session_id"] = self.session_id
            snapshots.append(snapshot)
        return snapshots

    async def _put_control_event(self, event: dict[str, Any]) -> None:
        """Deliver a critical state transition without an unbounded wait."""
        try:
            await asyncio.wait_for(self.event_queue.put(event), timeout=5.0)
        except asyncio.TimeoutError:
            logger.error(
                "[PERM] failed to enqueue control event={} session_id={} within timeout",
                event.get("event"),
                self.session_id,
            )

    def _emit_control_event(self, event: dict[str, Any]) -> None:
        try:
            self.event_queue.put_nowait(event)
        except asyncio.QueueFull:
            # Output queues are bounded for memory safety. A resolution is
            # nevertheless state-critical for other attached tabs, so wait
            # briefly in a tracked task rather than silently dropping it.
            task = asyncio.create_task(self._put_control_event(event))
            self._control_event_tasks.add(task)
            task.add_done_callback(self._control_event_tasks.discard)

    def _mark_settled(self, request_id: str, state: str) -> None:
        self._settled_requests[request_id] = state
        while len(self._settled_requests) > _MAX_SETTLED_REQUESTS:
            self._settled_requests.pop(next(iter(self._settled_requests)))

    async def can_use_tool(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        context: Any,
    ) -> PermissionResultAllow | PermissionResultDeny:
        return await self.request_permission(tool_name, tool_input, context)

    def resolve(
        self,
        request_id: str,
        decision: str,
        message: str = "",
        updated_input: dict[str, Any] | None = None,
    ) -> bool:
        logger.info("[PERM] resolve request_id={} decision={} session_id={}",
                    request_id, decision, self.session_id)
        future = self.pending.get(request_id)
        if not future or future.done():
            if request_id in self._settled_requests:
                return False
            raise ValueError(f"No pending permission request: {request_id}")
        request_data = self.pending_requests.get(request_id, {})
        self._mark_settled(request_id, "resolved")
        self._emit_control_event({
            "event": "permission_resolved",
            "data": {
                "request_id": request_id,
                "tool_use_id": request_data.get("tool_use_id"),
                "tool_name": request_data.get("tool_name"),
                "session_id": self.session_id,
                "decision": decision,
                "message": message or None,
                "updated_input": updated_input,
            },
        })
        if decision == "allow":
            future.set_result(PermissionResultAllow(updated_input=updated_input or None))
        else:
            # A deny message becomes the errored tool_result's content, which must
            # not be empty: the Anthropic API 400s on an empty error tool_result
            # ("content cannot be empty if is_error is true") and lenient gateways
            # feed the model a malformed empty block instead. Default it.
            future.set_result(PermissionResultDeny(message=message or "User denied permission"))
        return True

    def cancel_all(self):
        for request_id, future in list(self.pending.items()):
            if not future.done():
                self._mark_settled(request_id, "cancelled")
                future.set_result(PermissionResultDeny(message="Stream cancelled"))
        self.pending.clear()
        self.pending_requests.clear()


class PermissionCoordinatorRegistry:
    def __init__(self) -> None:
        self._coordinators: dict[str, PermissionCoordinator] = {}

    def register(self, session_id: str, coordinator: PermissionCoordinator) -> None:
        self._coordinators[session_id] = coordinator

    def unregister(self, session_id: str) -> None:
        self._coordinators.pop(session_id, None)

    def get(self, session_id: str) -> PermissionCoordinator | None:
        return self._coordinators.get(session_id)

    def remap_session(self, old_session_id: str, new_session_id: str, coordinator: PermissionCoordinator) -> None:
        if old_session_id != new_session_id:
            self.unregister(old_session_id)
        coordinator.session_id = new_session_id
        self.register(new_session_id, coordinator)


registry = PermissionCoordinatorRegistry()
