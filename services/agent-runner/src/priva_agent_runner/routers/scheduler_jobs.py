"""User scheduler API (design §8): ``/api/sandbox/scheduler/*``, session-authed
via the same signed runner token every route trusts.

CRUD + pause/resume write straight to the dataplane (the scheduler replicas
re-list ≤30s, D6 — no notify channel); run-now proxies to the scheduler's
internal API so the synthetic fire goes through the exactly-once claim; runs
come from ``ListRuns`` keyset pagination. Every job_id is ownership-checked
against this pod's single account before any unscoped-by-id dataplane call.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException

from priva_common.audit_log import AuditEntry, get_audit_logger
from priva_common.config import get_settings
from priva_common.dataplane import get_client
from priva_common.logging import get_app_logger
from priva_common.models.auth import UserRecord
from priva_common.models.scheduler import (
    AgentRunConfig,
    CreateJobRequest,
    JobRunHistoryResponse,
    ScheduledJobDefinition,
    ScheduledJobListResponse,
    ScheduledJobResponse,
    TriggerValidationRequest,
    TriggerValidationResponse,
    UpdateJobRequest,
)

from ..deps import pinned_account_id, require_account
from ..services.scheduled_runs.triggers import build_trigger, next_run_time

logger = get_app_logger(__name__)

router = APIRouter(prefix="/api/sandbox/scheduler", tags=["scheduler"])


def _account_id(user: UserRecord) -> str:
    account_id = user.account_id or pinned_account_id()
    if not account_id:
        raise HTTPException(400, "account not resolved")
    return account_id


def _validate_trigger_or_400(trigger, tz: str) -> None:
    try:
        build_trigger(trigger, tz)
    except Exception as exc:
        raise HTTPException(400, f"invalid trigger: {exc}") from exc


def _next_run_time(defn: ScheduledJobDefinition) -> str | None:
    if defn.status != "active":
        return None  # round-3: blank for paused jobs
    try:
        return next_run_time(defn.trigger, defn.timezone)
    except Exception:
        return None


def _to_response(defn: ScheduledJobDefinition, username: str) -> ScheduledJobResponse:
    cfg = defn.job_config
    if cfg is not None and cfg.job_type == "tool_retry":
        cfg = None  # dormant enum (D4): imported rows render config-less
    return ScheduledJobResponse(
        id=defn.id, name=defn.name, prompt=defn.prompt, trigger=defn.trigger,
        timezone=defn.timezone, status=defn.status, model=defn.model,
        job_config=cfg, created_at=defn.created_at, updated_at=defn.updated_at,
        next_run_time=_next_run_time(defn), username=username,
    )


async def _owned_job(account_id: str, job_id: str) -> ScheduledJobDefinition:
    """Resolve a job WITHIN this account (GetJob is unscoped by design — the
    ownership fence lives here)."""
    jobs = await asyncio.to_thread(get_client().scheduler.list_jobs, account_id)
    for job in jobs:
        if job.id == job_id:
            return job
    raise HTTPException(404, "job not found")


def _audit(user: UserRecord, action: str, job_id: str, **details) -> None:
    get_audit_logger().append(AuditEntry(
        actor=user.username, action=action, target=job_id, details=details,
    ))


# --- jobs -----------------------------------------------------------------


@router.get("/jobs", response_model=ScheduledJobListResponse)
async def list_jobs(user: UserRecord = Depends(require_account)):
    account_id = _account_id(user)
    jobs = await asyncio.to_thread(get_client().scheduler.list_jobs, account_id)
    jobs.sort(key=lambda j: j.created_at, reverse=True)
    return ScheduledJobListResponse(
        jobs=[_to_response(j, user.username) for j in jobs], total=len(jobs))


@router.post("/jobs", response_model=ScheduledJobResponse)
async def create_job(req: CreateJobRequest, user: UserRecord = Depends(require_account)):
    account_id = _account_id(user)
    _validate_trigger_or_400(req.trigger, req.timezone)
    now = datetime.now(timezone.utc)
    defn = ScheduledJobDefinition(
        id=str(uuid4())[:8], name=req.name, prompt=req.prompt,
        trigger=req.trigger, timezone=req.timezone, status=req.status,
        model=req.model, job_config=req.job_config, created_at=now, updated_at=now,
    )
    created = await asyncio.to_thread(get_client().scheduler.create_job, account_id, defn)
    _audit(user, "scheduler.job_created", created.id,
           name=created.name, job_type=(created.job_config.job_type if created.job_config else "agent_run"))
    return _to_response(created, user.username)


@router.put("/jobs/{job_id}", response_model=ScheduledJobResponse)
async def update_job(job_id: str, req: UpdateJobRequest,
                     user: UserRecord = Depends(require_account)):
    account_id = _account_id(user)
    updated = (await _owned_job(account_id, job_id)).model_copy(deep=True)

    if req.name is not None:
        updated.name = req.name
    if req.trigger is not None:
        updated.trigger = req.trigger
    if req.timezone is not None:
        updated.timezone = req.timezone
    if req.status is not None:
        updated.status = req.status
    if req.model is not None:
        updated.model = req.model
    if req.job_config is not None:
        updated.job_config = req.job_config
        if isinstance(req.job_config, AgentRunConfig):
            updated.prompt = req.job_config.prompt
    elif req.prompt is not None:
        updated.prompt = req.prompt
        if isinstance(updated.job_config, AgentRunConfig):
            updated.job_config.prompt = req.prompt
    _validate_trigger_or_400(updated.trigger, updated.timezone)
    updated.updated_at = datetime.now(timezone.utc)

    saved = await asyncio.to_thread(get_client().scheduler.update_job, job_id, updated)
    if saved is None:
        raise HTTPException(404, "job not found")
    _audit(user, "scheduler.job_updated", job_id)
    return _to_response(saved, user.username)


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str, user: UserRecord = Depends(require_account)):
    account_id = _account_id(user)
    job = await _owned_job(account_id, job_id)
    await asyncio.to_thread(get_client().scheduler.delete_job, job.id)
    _audit(user, "scheduler.job_deleted", job_id, name=job.name)
    return {"status": "ok"}


@router.post("/jobs/{job_id}/pause", response_model=ScheduledJobResponse)
async def pause_job(job_id: str, user: UserRecord = Depends(require_account)):
    return await _set_status(job_id, "paused", user)


@router.post("/jobs/{job_id}/resume", response_model=ScheduledJobResponse)
async def resume_job(job_id: str, user: UserRecord = Depends(require_account)):
    return await _set_status(job_id, "active", user)


async def _set_status(job_id: str, status: str, user: UserRecord) -> ScheduledJobResponse:
    account_id = _account_id(user)
    await _owned_job(account_id, job_id)
    saved = await asyncio.to_thread(get_client().scheduler.set_job_status, job_id, status)
    if saved is None:
        raise HTTPException(404, "job not found")
    _audit(user, f"scheduler.job_{'paused' if status == 'paused' else 'resumed'}", job_id)
    return _to_response(saved, user.username)


# --- run-now (proxies the scheduler's internal API — same claim dance) -----


async def _post_trigger(job_id: str) -> httpx.Response:
    url = f"{get_settings().scheduler.internal_url}/internal/trigger/{job_id}"
    async with httpx.AsyncClient(trust_env=False, timeout=10.0) as cx:
        return await cx.post(url)


@router.post("/jobs/{job_id}/trigger", status_code=202)
async def trigger_job(job_id: str, user: UserRecord = Depends(require_account)):
    account_id = _account_id(user)
    job = await _owned_job(account_id, job_id)
    try:
        resp = await _post_trigger(job.id)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"scheduler unreachable: {exc}") from exc
    if resp.status_code == 404:
        raise HTTPException(404, "job not found")
    if resp.status_code != 202:
        raise HTTPException(502, f"scheduler error ({resp.status_code})")
    _audit(user, "scheduler.job_triggered", job_id, name=job.name)
    return {"status": "accepted", "job_id": job.id}


# --- trigger validation (drawer live preview, §9.2) -------------------------


@router.post("/validate-trigger", response_model=TriggerValidationResponse)
async def validate_trigger(req: TriggerValidationRequest,
                           user: UserRecord = Depends(require_account)):
    try:
        nxt = next_run_time(req.trigger, req.timezone)
    except Exception as exc:
        return TriggerValidationResponse(valid=False, error=str(exc))
    return TriggerValidationResponse(valid=True, next_run_time=nxt)


# --- run history -------------------------------------------------------------


@router.get("/runs", response_model=JobRunHistoryResponse)
async def list_runs(
    job_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    before: str | None = None,
    after: str | None = None,
    user: UserRecord = Depends(require_account),
):
    account_id = _account_id(user)
    limit = max(1, min(limit, 200))
    page = await asyncio.to_thread(
        lambda: get_client().scheduler.list_runs(
            account_id, limit=limit, before=before, after=after,
            job_id=job_id, status=status))
    return JobRunHistoryResponse(
        runs=page.runs, next_cursor=page.next_cursor,
        prev_cursor=page.prev_cursor, total=page.total, limit=limit)
