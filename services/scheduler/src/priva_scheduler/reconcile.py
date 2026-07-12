"""Reconcile sweep — the two housekeeping duties (design §4/§10).

1. Stale ``running`` age-out: a record still running past
   ``running_ceiling_seconds`` means the pod vanished without writing its
   outcome (admitted-then-crashed) — close it as ``error(dispatch_lost)``.
   The ceiling sits above the D14 caps, so a live runner always kills first.
2. ``job_fire`` prune: claim rows only dedupe concurrent fires; older than
   ``fire_prune_hours`` they're dead weight.

Per-account listing (accounts × ListRuns(status='running')) — no cross-account
RPC, the M5 'admin-list accepted-slower' precedent. Fine at v1 scale.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from priva_common.config import get_settings
from priva_common.logging import get_app_logger
from priva_common.metrics import SCHEDULER_SWEEP_REAPED
from priva_common.models.scheduler import JobRunRecord

logger = get_app_logger(__name__)


def _aware(dt: datetime) -> datetime:
    """Records written by different writers mix naive and aware stamps; the
    wire format is UTC ISO, so naive = UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def sweep_once(client) -> dict:
    s = get_settings().scheduler
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=s.running_ceiling_seconds)

    reaped = 0
    accounts = await asyncio.to_thread(client.accounts.list)
    for account in accounts:
        try:
            page = await asyncio.to_thread(
                lambda aid=account.account_id: client.scheduler.list_runs(
                    aid, status="running", limit=200))
        except Exception:
            logger.exception("sweep: list_runs failed account={}", account.account_id)
            continue
        for run in page.runs:
            if run.started_at is None or _aware(run.started_at) >= cutoff:
                continue
            try:
                await asyncio.to_thread(client.scheduler.finish_run, JobRunRecord(
                    run_id=run.run_id, job_id=run.job_id or "", job_name=run.job_name,
                    username=run.username, status="error", finished_at=now,
                    is_error=True, error_message="dispatch_lost",
                ))
                reaped += 1
                logger.warning(
                    "sweep: aged out run={} job={} (running since {})",
                    run.run_id, run.job_id, run.started_at,
                )
            except Exception:
                logger.exception("sweep: finish_run failed run={}", run.run_id)

    if reaped:
        SCHEDULER_SWEEP_REAPED.inc(reaped)

    fire_cutoff = (now - timedelta(hours=s.fire_prune_hours)).isoformat()
    try:
        pruned = await asyncio.to_thread(client.scheduler.prune_fires_before, fire_cutoff)
    except Exception:
        logger.exception("sweep: fire prune failed")
        pruned = 0

    if reaped or pruned:
        logger.info("sweep done: reaped={} fires_pruned={}", reaped, pruned)
    return {"reaped": reaped, "fires_pruned": pruned}
