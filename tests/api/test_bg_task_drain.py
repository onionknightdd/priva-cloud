"""Tests for background-workflow drain tracking + idle give-up.

Guards the fix for "multi-workflow cards auto-stop": the streaming run must stay
alive past the launching turn's ResultMessage while background workflows are
still live, so it keeps forwarding their progress + terminal events instead of
tearing the CLI down (which killed the workflows and made the frontend finalize
the cards as STOPPED).

The tracking is anchored on the **Workflow tool_use**, which is always seen
before the turn's ResultMessage — unlike the async ``task_started``, which can
lose that race and arrive after end-of-turn (the intermittent-failure cause).
"""

from priva_agent_runner.services.claude_sdk.service import (
    _BG_IDLE_TIMEOUT,
    _BG_SETTLE_SECONDS,
    WorkflowDrainTracker,
    classify_bg_task_event,
    should_abort_silent_stream,
    should_stop_bg_drain,
)


def _sys(subtype: str, payload: dict) -> dict:
    """A task event as it actually hits the loop: a `system` message whose SDK
    Task*Message subclass was serialized to {type, subtype, data}."""
    return {"event": "system", "data": {"type": "system", "subtype": subtype, "data": payload}}


def _tool_use(name: str, tool_id: str) -> dict:
    return {"event": "tool_use", "data": {"content": [{"type": "tool_use", "id": tool_id, "name": name}]}}


def _tool_result(tool_use_id: str, is_error: bool = False) -> dict:
    return {
        "event": "tool_result",
        "data": {"content": [{"type": "tool_result", "tool_use_id": tool_use_id, "is_error": is_error}]},
    }


class TestClassifyBgTaskEvent:
    def test_task_started_decodes_ids(self) -> None:
        evt = _sys("task_started", {"task_id": "t1", "tool_use_id": "u1", "task_type": "local_workflow"})
        assert classify_bg_task_event(evt["event"], evt["data"]) == ("task_started", "t1", "u1", False)

    def test_task_notification_completed_is_terminal(self) -> None:
        evt = _sys("task_notification", {"task_id": "t1", "tool_use_id": "u1", "status": "completed"})
        assert classify_bg_task_event(evt["event"], evt["data"]) == ("task_notification", "t1", "u1", True)

    def test_task_notification_running_is_not_terminal(self) -> None:
        evt = _sys("task_notification", {"task_id": "t1", "status": "running"})
        assert classify_bg_task_event(evt["event"], evt["data"]) == ("task_notification", "t1", None, False)

    def test_task_updated_terminal_patch(self) -> None:
        # A killed/stopped background task may report ONLY via task_updated.
        evt = _sys("task_updated", {"task_id": "t1", "patch": {"status": "killed"}})
        assert classify_bg_task_event(evt["event"], evt["data"]) == ("task_updated", "t1", None, True)

    def test_task_updated_running_patch_not_terminal(self) -> None:
        evt = _sys("task_updated", {"task_id": "t1", "patch": {"status": "running"}})
        assert classify_bg_task_event(evt["event"], evt["data"]) == ("task_updated", "t1", None, False)

    def test_non_task_events_ignored(self) -> None:
        assert classify_bg_task_event("assistant", {"content": []}) == (None, None, None, False)
        assert classify_bg_task_event("result", {"session_id": "s"}) == (None, None, None, False)
        assert classify_bg_task_event("system", {"subtype": "init", "data": {}}) == (None, None, None, False)
        assert classify_bg_task_event(None, None) == (None, None, None, False)


class TestWorkflowDrainTracker:
    def test_workflow_tool_use_marks_outstanding(self) -> None:
        t = WorkflowDrainTracker()
        t.observe(*_from(_tool_use("Workflow", "u1")))
        assert t.outstanding_count == 1

    def test_non_workflow_tool_use_ignored(self) -> None:
        t = WorkflowDrainTracker()
        t.observe(*_from(_tool_use("Bash", "u1")))
        assert t.outstanding_count == 0

    def test_terminal_task_notification_clears_by_tool_use_id(self) -> None:
        t = WorkflowDrainTracker()
        t.observe(*_from(_tool_use("Workflow", "u1")))
        t.observe(*_from(_sys("task_notification", {"task_id": "t1", "tool_use_id": "u1", "status": "completed"})))
        assert t.outstanding_count == 0

    def test_terminal_task_updated_clears_via_taskid_map(self) -> None:
        # task_updated carries only task_id — the task_id→tool_use_id map learned
        # from an earlier task_progress lets it still clear the launch.
        t = WorkflowDrainTracker()
        t.observe(*_from(_tool_use("Workflow", "u1")))
        t.observe(*_from(_sys("task_progress", {"task_id": "t1", "tool_use_id": "u1"})))
        t.observe(*_from(_sys("task_updated", {"task_id": "t1", "patch": {"status": "completed"}})))
        assert t.outstanding_count == 0

    def test_error_tool_result_cancels_a_failed_launch(self) -> None:
        # A parse-error Workflow spawns no background task → stop waiting on it.
        t = WorkflowDrainTracker()
        t.observe(*_from(_tool_use("Workflow", "u1")))
        t.observe(*_from(_tool_result("u1", is_error=True)))
        assert t.outstanding_count == 0

    def test_success_tool_result_keeps_launch(self) -> None:
        t = WorkflowDrainTracker()
        t.observe(*_from(_tool_use("Workflow", "u1")))
        t.observe(*_from(_tool_result("u1", is_error=False)))
        assert t.outstanding_count == 1

    def test_race_task_started_never_arrives_still_tracked(self) -> None:
        # THE regression this fix targets: the async task_started loses the race
        # to end-of-turn and never appears — the tool_use anchor still keeps the
        # workflow tracked, and the terminal event later clears it.
        t = WorkflowDrainTracker()
        t.observe(*_from(_tool_use("Workflow", "u1")))
        # ... turn ends here with NO task_started seen ...
        assert t.outstanding_count == 1  # would have been 0 under the old fix → killed
        t.observe(*_from(_sys("task_notification", {"task_id": "t1", "tool_use_id": "u1", "status": "completed"})))
        assert t.outstanding_count == 0

    def test_two_workflows_drain_independently(self) -> None:
        t = WorkflowDrainTracker()
        t.observe(*_from(_tool_use("Workflow", "uA")))
        t.observe(*_from(_tool_use("Workflow", "uB")))
        assert t.outstanding_count == 2
        t.observe(*_from(_sys("task_notification", {"task_id": "tA", "tool_use_id": "uA", "status": "completed"})))
        assert t.outstanding_count == 1  # B still live → run must not end
        t.observe(*_from(_sys("task_updated", {"task_id": "tB", "patch": {"status": "completed"}, "tool_use_id": "uB"})))
        assert t.outstanding_count == 0

    def test_terminal_for_unrelated_task_is_noop(self) -> None:
        # A subagent Task's terminal (never tracked as a workflow) must not error.
        t = WorkflowDrainTracker()
        t.observe(*_from(_tool_use("Workflow", "u1")))
        t.observe(*_from(_sys("task_notification", {"task_id": "tX", "tool_use_id": "uX", "status": "completed"})))
        assert t.outstanding_count == 1


class TestShouldStopBgDrain:
    """Idle-based give-up: bound the drain by inactivity, not total wall-clock."""

    def test_live_task_active_keeps_waiting(self) -> None:
        assert should_stop_bg_drain(2, idle_seconds=1.0) is False
        assert should_stop_bg_drain(1, idle_seconds=_BG_IDLE_TIMEOUT - 1) is False

    def test_live_task_silent_past_idle_window_gives_up(self) -> None:
        assert should_stop_bg_drain(1, idle_seconds=_BG_IDLE_TIMEOUT) is True
        assert should_stop_bg_drain(3, idle_seconds=_BG_IDLE_TIMEOUT + 30) is True

    def test_drained_waits_short_settle_for_summary_turn(self) -> None:
        assert should_stop_bg_drain(0, idle_seconds=_BG_SETTLE_SECONDS - 1) is False
        assert should_stop_bg_drain(0, idle_seconds=_BG_SETTLE_SECONDS) is True

    def test_settle_is_much_shorter_than_idle_window(self) -> None:
        assert _BG_SETTLE_SECONDS < _BG_IDLE_TIMEOUT


class TestNetworkSilenceGuard:
    def test_clean_model_wait_is_bounded(self) -> None:
        assert should_abort_silent_stream(
            draining_background=False,
            outstanding_tool_count=0,
            idle_seconds=120,
            timeout_seconds=120,
        )

    def test_foreground_tool_is_never_killed_by_network_guard(self) -> None:
        assert not should_abort_silent_stream(
            draining_background=False,
            outstanding_tool_count=1,
            idle_seconds=3600,
            timeout_seconds=120,
        )

    def test_background_drain_keeps_its_separate_idle_policy(self) -> None:
        assert not should_abort_silent_stream(
            draining_background=True,
            outstanding_tool_count=0,
            idle_seconds=3600,
            timeout_seconds=120,
        )

    def test_user_permission_wait_uses_its_longer_permission_timeout(self) -> None:
        assert not should_abort_silent_stream(
            draining_background=False,
            outstanding_tool_count=0,
            waiting_for_permission=True,
            idle_seconds=3600,
            timeout_seconds=120,
        )


def _from(evt: dict) -> tuple[str, dict]:
    """Unpack a test event dict into the (event, data) observe() takes."""
    return evt["event"], evt["data"]
