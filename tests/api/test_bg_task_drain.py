"""Tests for background-task (workflow) drain classification.

Guards the fix for "multi-workflow cards auto-stop": the streaming run must
stay alive past the launching turn's ResultMessage while background workflow
tasks are still live, so it keeps forwarding their progress + terminal events
instead of tearing the CLI down (which killed the workflows and made the
frontend finalize the cards as STOPPED).

``classify_bg_task_event`` is the pure core of that tracking: given an emitted
(event, data), it says whether a background task started, finished, or neither.
"""

from priva_agent_runner.services.claude_sdk.service import (
    _BG_IDLE_TIMEOUT,
    _BG_SETTLE_SECONDS,
    classify_bg_task_event,
    should_stop_bg_drain,
)


def _sys(subtype: str, payload: dict) -> dict:
    """A task event as it actually hits the loop: a `system` message whose
    SDK Task*Message subclass was serialized to {type, subtype, data}."""
    return {"event": "system", "data": {"type": "system", "subtype": subtype, "data": payload}}


class TestClassifyBgTaskEvent:
    def test_workflow_task_started_is_a_start(self) -> None:
        evt = _sys("task_started", {"task_id": "t1", "task_type": "local_workflow"})
        assert classify_bg_task_event(evt["event"], evt["data"]) == ("start", "t1")

    def test_non_workflow_task_started_is_ignored(self) -> None:
        # Background bash / subagents must NOT keep the run alive here.
        evt = _sys("task_started", {"task_id": "t1", "task_type": "local_bash"})
        assert classify_bg_task_event(evt["event"], evt["data"]) == (None, None)

    def test_task_notification_completed_is_terminal(self) -> None:
        evt = _sys("task_notification", {"task_id": "t1", "status": "completed"})
        assert classify_bg_task_event(evt["event"], evt["data"]) == ("terminal", "t1")

    def test_task_notification_nonterminal_status_is_not_terminal(self) -> None:
        evt = _sys("task_notification", {"task_id": "t1", "status": "running"})
        assert classify_bg_task_event(evt["event"], evt["data"]) == (None, None)

    def test_task_updated_terminal_patch_clears_the_task(self) -> None:
        # A killed/stopped background task may report ONLY via task_updated.
        evt = _sys("task_updated", {"task_id": "t1", "patch": {"status": "killed"}})
        assert classify_bg_task_event(evt["event"], evt["data"]) == ("terminal", "t1")

    def test_task_updated_running_patch_is_not_terminal(self) -> None:
        evt = _sys("task_updated", {"task_id": "t1", "patch": {"status": "running"}})
        assert classify_bg_task_event(evt["event"], evt["data"]) == (None, None)

    def test_task_progress_is_neither(self) -> None:
        evt = _sys("task_progress", {"task_id": "t1"})
        assert classify_bg_task_event(evt["event"], evt["data"]) == (None, None)

    def test_flat_task_started_label_is_handled_defensively(self) -> None:
        # Defensive: if the SDK ever labels these as flat task_* events.
        assert classify_bg_task_event(
            "task_started", {"task_id": "t9", "task_type": "local_workflow"}
        ) == ("start", "t9")

    def test_unrelated_events_are_ignored(self) -> None:
        assert classify_bg_task_event("assistant", {"content": []}) == (None, None)
        assert classify_bg_task_event("result", {"session_id": "s"}) == (None, None)
        assert classify_bg_task_event("system", {"subtype": "init", "data": {}}) == (None, None)

    def test_missing_task_id_is_ignored(self) -> None:
        evt = _sys("task_started", {"task_type": "local_workflow"})
        assert classify_bg_task_event(evt["event"], evt["data"]) == (None, None)

    def test_none_and_empty_are_safe(self) -> None:
        assert classify_bg_task_event(None, None) == (None, None)
        assert classify_bg_task_event("system", None) == (None, None)


class TestDrainSetSemantics:
    """The loop maintains a set of live task ids from start/terminal classifications;
    a multi-workflow turn only finishes once the set drains to empty."""

    def _apply(self, events: list[tuple[str, dict]]) -> set[str]:
        live: set[str] = set()
        for event, data in events:
            kind, task_id = classify_bg_task_event(event, data)
            if kind == "start":
                live.add(task_id)
            elif kind == "terminal":
                live.discard(task_id)
        return live

    def test_two_workflows_drain_independently(self) -> None:
        events = [
            _sys("task_started", {"task_id": "A", "task_type": "local_workflow"}),
            _sys("task_started", {"task_id": "B", "task_type": "local_workflow"}),
            _sys("task_progress", {"task_id": "A"}),
            _sys("task_notification", {"task_id": "A", "status": "completed"}),
        ]
        # A done, B still live → run must NOT end.
        live = self._apply([(e["event"], e["data"]) for e in events])
        assert live == {"B"}

        events.append(_sys("task_updated", {"task_id": "B", "patch": {"status": "completed"}}))
        live = self._apply([(e["event"], e["data"]) for e in events])
        assert live == set()  # both terminal → safe to finish


class TestShouldStopBgDrain:
    """Idle-based give-up: bound the drain by inactivity, not total wall-clock."""

    def test_live_task_active_keeps_waiting(self) -> None:
        # A workflow that emitted an event a moment ago must keep running,
        # however long it has been going overall.
        assert should_stop_bg_drain(2, idle_seconds=1.0) is False
        assert should_stop_bg_drain(1, idle_seconds=_BG_IDLE_TIMEOUT - 1) is False

    def test_live_task_silent_past_idle_window_gives_up(self) -> None:
        assert should_stop_bg_drain(1, idle_seconds=_BG_IDLE_TIMEOUT) is True
        assert should_stop_bg_drain(3, idle_seconds=_BG_IDLE_TIMEOUT + 30) is True

    def test_drained_waits_short_settle_for_summary_turn(self) -> None:
        # All tasks terminal: brief grace for a re-invocation summary to begin,
        # but far shorter than the live-task idle window.
        assert should_stop_bg_drain(0, idle_seconds=_BG_SETTLE_SECONDS - 1) is False
        assert should_stop_bg_drain(0, idle_seconds=_BG_SETTLE_SECONDS) is True

    def test_settle_is_much_shorter_than_idle_window(self) -> None:
        assert _BG_SETTLE_SECONDS < _BG_IDLE_TIMEOUT
