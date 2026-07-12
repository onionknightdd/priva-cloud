"""Scheduler dispatch intake (design §7, D13/D16).

``POST /api/sandbox/agent/scheduled-run`` — service-authed with the same
signed runner token every route trusts (the scheduler mints its own from the
shared secret, exactly like the control-panel edge does). This is admission
only: the 202 commits the pod to run the job; the run executes DETACHED as a
registry-owned RunRecord and writes its own FinishRun. Nobody holds a socket
across a run.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from priva_common.audit_log import AuditEntry, get_audit_logger
from priva_common.logging import get_app_logger
from priva_common.models.auth import UserRecord
from priva_common.models.scheduler import ScheduledRunAccepted, ScheduledRunRequest

from ..deps import get_user_workspace, require_account
from ..services.claude_sdk.run_registry import run_registry
from ..services.scheduled_runs import executor

logger = get_app_logger(__name__)

router = APIRouter(prefix="/api/sandbox/agent", tags=["scheduler"])


@router.post("/scheduled-run", status_code=202, response_model=ScheduledRunAccepted)
async def scheduled_run(
    req: ScheduledRunRequest,
    user: UserRecord = Depends(require_account),
) -> ScheduledRunAccepted:
    """Admit a scheduler-dispatched run.

    202 admitted (idempotent: a re-POST of an accepted run_id is 202 again,
    never a second execution) · 409 this job already has a live run in this
    pod (D9 backstop) · 429 account concurrency cap (D16 — the scheduler
    re-admits ≤2 min, then records skipped(concurrency_cap)).
    """
    # Idempotency by run_id (D13): a retry after an ambiguous timeout must
    # not double-run.
    if executor.is_accepted(req.run_id):
        logger.info("[SCHED] duplicate admission run_id={} — 202 again", req.run_id)
        return ScheduledRunAccepted(run_id=req.run_id, duplicate=True)

    if executor.live_run_for_job(req.job_id) is not None:
        raise HTTPException(409, "job_overlap")

    if await executor.concurrency_cap_reached(user.account_id):
        raise HTTPException(429, "concurrency_cap")

    # The cap check awaited — re-check both admission gates before committing
    # so interleaved duplicate POSTs can't double-start.
    if executor.is_accepted(req.run_id):
        return ScheduledRunAccepted(run_id=req.run_id, duplicate=True)
    if executor.live_run_for_job(req.job_id) is not None:
        raise HTTPException(409, "job_overlap")

    cwd = get_user_workspace(user)
    executor.start(req, user, cwd)
    get_audit_logger().append(AuditEntry(
        actor=user.username,
        action="scheduler.run_admitted",
        target=req.run_id,
        details={"job_id": req.job_id, "job_type": req.job_config.job_type},
    ))
    return ScheduledRunAccepted(run_id=req.run_id)


@router.post("/scheduled-run/{run_id}/abort", status_code=202)
async def abort_scheduled_run(
    run_id: str,
    user: UserRecord = Depends(require_account),
):
    """HTTP twin of the WS abort frame for a live run (the Scheduler page's
    Stop button — no socket needed). The executor records the outcome as
    ``cancelled`` (US-4 ③); the next fire is unaffected."""
    record = run_registry.get(run_id=run_id)
    if record is None or not record.live:
        raise HTTPException(404, "no live run")
    record.cancelled.set()
    get_audit_logger().append(AuditEntry(
        actor=user.username, action="scheduler.run_aborted", target=run_id,
    ))
    return {"status": "aborting", "run_id": run_id}
