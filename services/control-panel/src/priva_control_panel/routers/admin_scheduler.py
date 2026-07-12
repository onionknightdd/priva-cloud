"""Admin scheduler oversight (design §8/D12): per-account drill-down reusing
the account-scoped dataplane RPCs — no new cross-account queries (the M5
"admin-list accepted-slower" precedent). Read-only except pause-all and
trigger; mutations audited.
"""

from __future__ import annotations

import asyncio

import httpx
from fastapi import APIRouter, Depends, HTTPException

from priva_common.audit_log import AuditEntry, get_audit_logger
from priva_common.config import get_settings
from priva_common.dataplane import get_client
from priva_common.logging import get_app_logger
from priva_common.models.auth import UserRecord

from ..services.auth import require_admin

logger = get_app_logger(__name__)

router = APIRouter(
    prefix="/api/admin/scheduler",
    tags=["admin-scheduler"],
    dependencies=[Depends(require_admin)],
)


def _job_out(job) -> dict:
    return {
        "id": job.id,
        "name": job.name,
        "status": job.status,
        "job_type": job.job_config.job_type if job.job_config else "agent_run",
        "trigger": job.trigger.model_dump(),
        "timezone": job.timezone,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


@router.get("/accounts/{account_id}/jobs")
async def list_account_jobs(account_id: str):
    jobs = await asyncio.to_thread(get_client().scheduler.list_jobs, account_id)
    return {"jobs": [_job_out(j) for j in jobs], "total": len(jobs)}


@router.get("/accounts/{account_id}/runs")
async def list_account_runs(
    account_id: str,
    job_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    before: str | None = None,
):
    limit = max(1, min(limit, 200))
    page = await asyncio.to_thread(
        lambda: get_client().scheduler.list_runs(
            account_id, limit=limit, before=before, job_id=job_id, status=status))
    return {
        "runs": [r.model_dump(mode="json") for r in page.runs],
        "next_cursor": page.next_cursor,
        "prev_cursor": page.prev_cursor,
        "total": page.total,
        "limit": limit,
    }


@router.post("/accounts/{account_id}/pause-all")
async def pause_all(account_id: str, admin: UserRecord = Depends(require_admin)):
    """US-9 kill switch: eager SetJobStatus(paused) over the account's jobs."""
    client = get_client()
    jobs = await asyncio.to_thread(client.scheduler.list_jobs, account_id)
    paused = 0
    for job in jobs:
        if job.status == "active":
            await asyncio.to_thread(client.scheduler.set_job_status, job.id, "paused")
            paused += 1
    get_audit_logger().append(AuditEntry(
        actor=admin.username, action="admin.scheduler.pause_all",
        target=account_id, details={"paused": paused},
    ))
    return {"status": "ok", "paused": paused, "total": len(jobs)}


@router.post("/jobs/{job_id}/trigger", status_code=202)
async def trigger_job(job_id: str, admin: UserRecord = Depends(require_admin)):
    """Run-now through the scheduler's internal API (same exactly-once claim)."""
    url = f"{get_settings().scheduler.internal_url}/internal/trigger/{job_id}"
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=10.0) as cx:
            resp = await cx.post(url)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"scheduler unreachable: {exc}") from exc
    if resp.status_code == 404:
        raise HTTPException(404, "job not found")
    if resp.status_code != 202:
        raise HTTPException(502, f"scheduler error ({resp.status_code})")
    get_audit_logger().append(AuditEntry(
        actor=admin.username, action="admin.scheduler.job_triggered", target=job_id,
    ))
    return {"status": "accepted", "job_id": job_id}
