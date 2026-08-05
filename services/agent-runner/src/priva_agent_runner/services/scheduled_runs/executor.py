"""Scheduled-run admission + execution (design §7, D13/D14/D16).

The endpoint admits, this module executes: every admitted run becomes a
registry-owned ``RunRecord`` — the same backbone ``ws_run`` uses — so it holds
an activity slot (the operator's idle sweep can't sleep the pod mid-run),
buffers events for WS ``attach`` (US-4 live watch), and honours the abort
frame. The run task is DETACHED from the dispatch socket (D13: the scheduler
is stateless after its 202) and the pod writes its own outcome to data-spine
(``FinishRun``) when the run ends; only runs the pod never acknowledged are
left to the scheduler's stale-running sweep.

Single-process uvicorn + one pod per account, so module-level dicts need no
cross-process coordination (same pattern as ``run_registry``).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from priva_common.dataplane import get_client
from priva_common.logging import get_app_logger
from priva_common.metrics import SCHEDULED_RUNS
from priva_common.models.auth import UserRecord
from priva_common.models.scheduler import (
    AgentRunConfig,
    HttpCallConfig,
    JobRunRecord,
    ScheduledRunRequest,
)

from ... import activity
from ..claude_sdk import session_meta
from ..claude_sdk.run_registry import RUN_END_EVENT, RunRecord, run_registry
from ..claude_sdk.service import agent_run_events
from .builtin_tasks import execute_http_call, execute_user_script
from .callbacks import deliver_feishu, is_feishu_enabled

logger = get_app_logger(__name__)

# Post-cancel drain allowance on top of the D14 wall clock before the agent
# body is hard-cancelled (SDK teardown + JSONL flush normally take seconds).
_KILL_GRACE_SECONDS = 120
# Terminal admission states stay idempotency-visible this long — safely past
# the scheduler's ≤2 min connection-retry window (D13 re-POST → 202 again).
_ACCEPTED_TTL_SECONDS = 3600
# D11 error_message truncation: summaries stay ~200 chars (ledger), error
# detail keeps enough to be useful in the UI's inline expand.
_SUMMARY_CHARS = 200
_ERROR_CHARS = 2000
_CALLBACK_CAPTURE_CHARS = 4001

_STATUS_TO_RECORD = {"success": "completed", "error": "error", "cancelled": "aborted"}


@dataclass
class ScheduledRunState:
    run_id: str
    job_id: str
    job_type: str
    record: RunRecord
    ended_at: float | None = None


_accepted: dict[str, ScheduledRunState] = {}  # run_id → state (idempotency, D13)
_live_by_job: dict[str, str] = {}             # job_id → live run_id (D9 backstop)


def _sweep_accepted() -> None:
    now = time.time()
    for run_id, state in list(_accepted.items()):
        if state.ended_at is not None and now - state.ended_at > _ACCEPTED_TTL_SECONDS:
            _accepted.pop(run_id, None)


def is_accepted(run_id: str) -> bool:
    _sweep_accepted()
    return run_id in _accepted


def live_run_for_job(job_id: str) -> str | None:
    run_id = _live_by_job.get(job_id)
    if run_id is None:
        return None
    state = _accepted.get(run_id)
    if state is None or not state.record.live:
        _live_by_job.pop(job_id, None)  # self-heal a stale mapping
        return None
    return run_id


async def concurrency_cap_reached(account_id: str | None) -> bool:
    """D16: scheduled runs count against ``quota.max_concurrent_sessions``.

    Live count = every registry-owned run (interactive WS runs + scheduled).
    Fail-open on a quota-read error: the fire is already claimed and
    StartRun'd — skipping it on a data-spine hiccup loses real work, while one
    over-cap run is harmless.
    """
    if not account_id:
        return False
    live = len(run_registry.list_active())
    try:
        quota = await asyncio.to_thread(lambda: get_client().quota.ensure(account_id))
        cap = int(quota.max_concurrent_sessions or 0)
    except Exception:
        logger.warning("[SCHED] quota read failed — admitting without cap check", exc_info=True)
        return False
    return cap > 0 and live >= cap


def start(req: ScheduledRunRequest, user: UserRecord, cwd: str) -> RunRecord:
    """Admit: register a registry-owned RunRecord and spawn the run DETACHED."""
    record = run_registry.create(run_id=req.run_id)
    state = ScheduledRunState(req.run_id, req.job_id, req.job_config.job_type, record)
    _accepted[req.run_id] = state
    _live_by_job[req.job_id] = req.run_id
    record.task = asyncio.create_task(
        _execute(state, req, user, cwd), name=f"scheduled-run-{req.run_id[:8]}",
    )
    return record


async def _execute(
    state: ScheduledRunState, req: ScheduledRunRequest, user: UserRecord, cwd: str
) -> None:
    """The detached run task: execute by job_type, then own the outcome write."""
    started = time.monotonic()
    record = state.record
    # error is the default so an unforeseen exit never reports success.
    outcome: dict = {
        "status": "error", "error_message": None, "result_summary": None,
        "num_turns": None, "session_id": None,
    }
    logger.info(
        "[SCHED] run start run_id={} job_id={} type={}",
        req.run_id, req.job_id, state.job_type,
    )
    try:
        if isinstance(req.job_config, AgentRunConfig):
            await _execute_agent(record, req, req.job_config, user, cwd, outcome)
        else:
            await _execute_builtin(record, req, user, cwd, outcome)
    except asyncio.CancelledError:
        # Process shutdown — finalize synchronously (an await here would
        # re-raise and drop the writes); FinishRun is single-attempt
        # best-effort, the scheduler's sweep covers a lost outcome.
        outcome["status"] = "cancelled"
        _close_record(state, req, outcome)
        _write_finish(req, user, outcome, _elapsed_ms(started), attempts=1)
        raise
    except Exception as exc:
        logger.exception("[SCHED] run {} failed", req.run_id)
        if not outcome.get("error_message"):
            outcome["error_message"] = (str(exc) or type(exc).__name__)[:_ERROR_CHARS]
        outcome["status"] = "error"

    duration_ms = _elapsed_ms(started)
    callback_activity = False
    callback_enabled = False
    record_closed = False
    try:
        finish_written = await asyncio.to_thread(
            _write_finish, req, user, outcome, duration_ms,
        )
        callback_requested = is_feishu_enabled(req.job_config)
        callback_enabled = finish_written and callback_requested
        if callback_requested and not finish_written:
            record.record_event(
                "callback_failed",
                {
                    "channel": "feishu",
                    "message": "terminal run could not be persisted",
                },
            )
        if callback_enabled:
            # Keep the pod awake for the outbound notification without leaving
            # this completed run in the admission/concurrency live set.
            activity.enter()
            callback_activity = True

        _close_record(
            state,
            req,
            outcome,
            signal_followers=not callback_enabled,
        )
        record_closed = True

        if callback_enabled:
            try:
                await _deliver_callback(req, user, record, outcome, duration_ms)
            except Exception as exc:  # callback bugs must not strand the run
                message = (str(exc) or type(exc).__name__)[:_ERROR_CHARS]
                logger.exception("[SCHED] callback handling crashed run_id={}", req.run_id)
                record.record_event(
                    "callback_failed", {"channel": "feishu", "message": message},
                )
    finally:
        # Cancellation during FinishRun still has to free overlap/activity.
        if not record_closed:
            _close_record(state, req, outcome)
        elif callback_enabled:
            # The registry/admission state was already closed before delivery,
            # but hold the follower sentinel until callback_failed (if any) is
            # observable on the live stream.
            _signal_run_end(record)
        if callback_activity:
            activity.leave()
    logger.info(
        "[SCHED] run end run_id={} status={} reason={}",
        req.run_id, outcome["status"], outcome.get("error_message"),
    )


async def _execute_agent(
    record: RunRecord,
    req: ScheduledRunRequest,
    cfg: AgentRunConfig,
    user: UserRecord,
    cwd: str,
    outcome: dict,
) -> None:
    """agent_run job: a NEW session through the same internals as ws_run (D10),
    bypassPermissions + enforced admin hooks (D2), AskUserQuestion stripped and
    both D14 caps armed. Outcome classification per D11/D14 reason tokens."""
    result_out: dict = {}
    last_error: dict = {}
    cap_reason: list[str | None] = [None]

    async def _tag_session(sid: str) -> None:
        if not sid or sid == outcome.get("session_id"):
            return
        outcome["session_id"] = sid
        run_registry.index_session(record, sid)  # attach-by-session-id works mid-run
        await session_meta.set_scheduler_session(  # sidebar ⏰ (D3) + D15 prune index
            sid, job_id=req.job_id, job_name=req.job_name, run_id=req.run_id,
        )

    async def emit(event_type: str, data: dict) -> None:
        if event_type == "stream_init" and (data or {}).get("stream_id"):
            run_registry.index_run_id(record, data["stream_id"])
        if event_type == "system" and (data or {}).get("subtype") == "init":
            inner = (data or {}).get("data") or {}
            if isinstance(inner, dict) and inner.get("session_id"):
                await _tag_session(inner["session_id"])
        if event_type == "result":
            result_out.clear()
            result_out.update(data or {})
            if (data or {}).get("session_id"):
                await _tag_session(data["session_id"])
        if event_type in ("stream_error", "retry_exhausted"):
            last_error.clear()
            last_error.update(data or {})
        record.record_event(event_type, data)

    async def watchdog() -> None:
        await asyncio.sleep(cfg.timeout_seconds)
        cap_reason[0] = "timeout"
        logger.warning(
            "[SCHED] run {} hit the {}s wall-clock cap — stopping",
            req.run_id, cfg.timeout_seconds,
        )
        record.cancelled.set()  # graceful stop: the run loop drains and the SDK closes

    wall_clock = cfg.timeout_seconds if cfg.timeout_seconds and cfg.timeout_seconds > 0 else None
    wd = asyncio.create_task(watchdog()) if wall_clock else None
    try:
        body = agent_run_events(
            cfg.prompt,
            None,  # D10: every fire opens a NEW session
            "bypassPermissions",
            cwd,
            user.username,
            req.model or cfg.model,
            auth_method="jwt",
            add_dirs=[],
            emit=emit,
            cancelled=record.cancelled,
            coordinator_out=record.coordinator_out,
            queue_out=record.queue_out,
            max_turns=cfg.max_turns if cfg.max_turns and cfg.max_turns > 0 else None,
            enable_permission_feedback=False,  # D14: AskUserQuestion stripped, prompts deny
        )
        if wall_clock:
            # Hard bound in case the graceful stop wedges past the grace.
            await asyncio.wait_for(body, timeout=wall_clock + _KILL_GRACE_SECONDS)
        else:
            await body
    except asyncio.TimeoutError:
        cap_reason[0] = cap_reason[0] or "timeout"
    finally:
        if wd:
            wd.cancel()

    text = str(result_out.get("result") or "").strip()
    # Kept out of JobRunRecord (whose summary is intentionally 200 chars) and
    # used only by an enabled callback after the terminal write succeeds.
    outcome["agent_result_message"] = text[:_CALLBACK_CAPTURE_CHARS]
    outcome["num_turns"] = result_out.get("num_turns")
    if cap_reason[0]:
        outcome["status"] = "error"
        outcome["error_message"] = cap_reason[0]  # 'timeout' (D11 token)
    elif record.cancelled.is_set():
        outcome["status"] = "cancelled"  # explicit abort via WS attach (US-4 ③)
    elif result_out:
        outcome["result_summary"] = text[:_SUMMARY_CHARS] or None
        if result_out.get("subtype") == "error_max_turns":
            outcome["status"] = "error"
            outcome["error_message"] = "max_turns"  # D11/D14 token
        elif result_out.get("is_error"):
            outcome["status"] = "error"
            outcome["error_message"] = (text or "agent error")[:_ERROR_CHARS]
        else:
            outcome["status"] = "success"
    else:
        outcome["status"] = "error"
        outcome["error_message"] = str(
            last_error.get("message") or "run ended without a result"
        )[:_ERROR_CHARS]


async def _execute_builtin(
    record: RunRecord,
    req: ScheduledRunRequest,
    user: UserRecord,
    cwd: str,
    outcome: dict,
) -> None:
    """http_call / user_script: the ported monolith executors — pod identity,
    no agent session (session_id stays null on the run record)."""

    async def emit(event_type: str, data: dict) -> None:
        record.record_event(event_type, data)

    cfg = req.job_config
    if isinstance(cfg, HttpCallConfig):
        res = await execute_http_call(cfg, user.username, cwd, emit, record.cancelled)
        outcome["callback_result"] = {
            "method": res.get("method"),
            "url": res.get("url"),
            "status_code": res.get("status_code"),
            "reason": res.get("reason") or "",
            "body": res.get("body") or "",
            "error": res.get("error"),
        }
    else:
        res = await execute_user_script(cfg, user.username, cwd, emit, record.cancelled)
        outcome["callback_result"] = {
            "exit_code": res.get("exit_code"),
            "stdout": res.get("stdout", ""),
            "stderr": res.get("stderr", ""),
            "timed_out": bool(res.get("timed_out")),
        }

    text = str(res.get("result") or "").strip()
    outcome["result_summary"] = text[:_SUMMARY_CHARS] or None
    if record.cancelled.is_set():
        outcome["status"] = "cancelled"
    elif res.get("is_error"):
        outcome["status"] = "error"
        outcome["error_message"] = (text or "task failed")[:_ERROR_CHARS]
    else:
        outcome["status"] = "success"


async def _deliver_callback(
    req: ScheduledRunRequest,
    user: UserRecord,
    record: RunRecord,
    outcome: dict,
    duration_ms: int,
) -> None:
    if not is_feishu_enabled(req.job_config):
        return

    if isinstance(req.job_config, AgentRunConfig):
        if outcome["status"] == "success":
            message = outcome.get("agent_result_message") or ""
        else:
            message = outcome.get("error_message") or outcome["status"]
        result: dict = {"message": message}
    elif isinstance(req.job_config, HttpCallConfig):
        result = outcome.get("callback_result") or {
            "method": req.job_config.method,
            "url": req.job_config.url,
            "status_code": None,
            "reason": "",
            "body": "",
            "error": outcome.get("error_message"),
        }
    else:
        result = outcome.get("callback_result") or {
            "exit_code": None,
            "stdout": "",
            "stderr": outcome.get("error_message") or "",
            "timed_out": False,
        }

    await deliver_feishu(
        account_id=user.account_id,
        record=record,
        callback_token=req.callback_token,
        payload={
            "run_id": req.run_id,
            "job_id": req.job_id,
            "job_name": req.job_name,
            "job_type": req.job_config.job_type,
            "status": outcome["status"],
            "duration_ms": duration_ms,
            "session_id": outcome.get("session_id"),
            "result": result,
        },
    )


def _close_record(
    state: ScheduledRunState,
    req: ScheduledRunRequest,
    outcome: dict,
    *,
    signal_followers: bool = True,
) -> None:
    """Terminal bookkeeping — registry finish (releases the activity slot),
    RUN_END for followers, overlap-map cleanup, metrics. Sync on purpose so
    the cancellation path can run it."""
    record = state.record
    if record.status == "running":
        run_registry.finish(record, _STATUS_TO_RECORD.get(outcome["status"], "error"))
    if signal_followers:
        _signal_run_end(record)
    if _live_by_job.get(req.job_id) == req.run_id:
        _live_by_job.pop(req.job_id, None)
    state.ended_at = time.time()
    SCHEDULED_RUNS.labels(job_type=state.job_type, status=outcome["status"]).inc()


def _signal_run_end(record: RunRecord) -> None:
    if any(kind == RUN_END_EVENT for _, kind, _ in record.events):
        return
    record.record_event(RUN_END_EVENT, {"status": record.status})


def _elapsed_ms(started_monotonic: float) -> int:
    return int((time.monotonic() - started_monotonic) * 1000)


def _write_finish(
    req: ScheduledRunRequest,
    user: UserRecord,
    outcome: dict,
    duration_ms: int,
    attempts: int = 3,
) -> bool:
    """The pod-owned outcome write (D13). Sync — call via to_thread from the
    normal path; the cancellation path calls it directly with attempts=1."""
    rec = JobRunRecord(
        run_id=req.run_id,
        job_id=req.job_id,
        job_name=req.job_name,
        username=user.username,
        finished_at=datetime.now(timezone.utc),
        status=outcome["status"],
        duration_ms=duration_ms,
        is_error=outcome["status"] == "error",
        error_message=outcome.get("error_message"),
        num_turns=outcome.get("num_turns"),
        result_summary=outcome.get("result_summary"),
        session_id=outcome.get("session_id"),
    )
    for attempt in range(1, attempts + 1):
        try:
            get_client().scheduler.finish_run(rec)
            return True
        except Exception:
            logger.warning(
                "[SCHED] FinishRun {} attempt {}/{} failed",
                req.run_id, attempt, attempts, exc_info=True,
            )
            if attempt < attempts:
                time.sleep(min(2 ** attempt, 8))
    logger.error(
        "[SCHED] FinishRun {} lost — the scheduler sweep will age it out", req.run_id,
    )
    return False
