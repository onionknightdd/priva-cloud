"""Reconcile sweep: stale 'running' → error(dispatch_lost); job_fire prune."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from priva_common.config import get_settings
from priva_common.models.scheduler import JobRunRecord

from priva_scheduler.reconcile import sweep_once


def _running(run_id: str, *, age_seconds: int) -> JobRunRecord:
    return JobRunRecord(
        run_id=run_id, job_id="j1", job_name="job j1", username="carol",
        status="running",
        started_at=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
    )


def test_sweep_ages_out_only_past_ceiling(fake_client):
    ceiling = get_settings().scheduler.running_ceiling_seconds
    sched = fake_client.scheduler
    sched.runs["r-stale"] = _running("r-stale", age_seconds=ceiling + 600)
    sched.runs["r-fresh"] = _running("r-fresh", age_seconds=60)
    # naive timestamp (a legacy writer) must be treated as UTC, not crash
    naive = _running("r-naive", age_seconds=ceiling + 600)
    naive.started_at = naive.started_at.replace(tzinfo=None)
    sched.runs["r-naive"] = naive

    out = asyncio.run(sweep_once(fake_client))

    assert out["reaped"] == 2
    finished = {f.run_id: f for f in sched.finishes}
    assert set(finished) == {"r-stale", "r-naive"}
    assert all(f.status == "error" and f.error_message == "dispatch_lost"
               for f in finished.values())
    # the fire table got pruned with an ISO cutoff
    assert len(sched.pruned_before) == 1 and sched.pruned_before[0].startswith("20")


def test_sweep_noop_when_all_healthy(fake_client):
    fake_client.scheduler.runs["r1"] = _running("r1", age_seconds=30)
    out = asyncio.run(sweep_once(fake_client))
    assert out["reaped"] == 0 and fake_client.scheduler.finishes == []
