"""Shared fakes for the services/scheduler unit tests.

``FakeClient`` implements exactly the dataplane surface the engine/reconcile
touch (sync methods — the real client is sync gRPC called via to_thread).
``fast_settings`` zeroes the cached Settings' sleep knobs so pipelines run in
milliseconds, restoring them afterwards (get_settings() is process-cached).
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from priva_common.config import get_settings
from priva_common.models.scheduler import (
    AgentRunConfig,
    CronTriggerConfig,
    JobRunRecord,
    ScheduledJobDefinition,
)


class FakeScheduler:
    def __init__(self):
        self.jobs: dict[str, tuple[str, ScheduledJobDefinition]] = {}  # job_id → (acct, defn)
        self.claims: set[tuple[str, int]] = set()
        self.claim_calls: list[tuple[str, int, str]] = []
        self.runs: dict[str, JobRunRecord] = {}
        self.records: list[JobRunRecord] = []   # record_run one-shots
        self.finishes: list[JobRunRecord] = []
        self.pruned_before: list[str] = []
        self.deny_claims = False

    # --- job set ---
    def list_active_jobs(self):
        return [(a, j) for a, j in self.jobs.values() if j.status == "active"]

    def get_job(self, job_id):
        entry = self.jobs.get(job_id)
        return entry[1] if entry else None

    def list_jobs(self, account_id):
        return [j for a, j in self.jobs.values() if a == account_id]

    # --- claim ---
    def claim_fire(self, job_id, fire_epoch, claimed_by):
        self.claim_calls.append((job_id, fire_epoch, claimed_by))
        if self.deny_claims or (job_id, fire_epoch) in self.claims:
            return False
        self.claims.add((job_id, fire_epoch))
        return True

    def prune_fires_before(self, cutoff):
        self.pruned_before.append(cutoff)
        return 0

    # --- runs ---
    def start_run(self, account_id, record):
        self.runs[record.run_id] = record
        return record

    def record_run(self, account_id, record):
        self.records.append(record)
        return record

    def finish_run(self, record):
        self.finishes.append(record)
        return record

    def get_latest_run(self, account_id, job_id):
        candidates = [r for r in self.runs.values() if r.job_id == job_id]
        return candidates[-1] if candidates else None

    def list_runs(self, account_id, *, status=None, limit=50, **kw):
        runs = [
            r for r in self.runs.values()
            if (status is None or r.status == status)
        ]
        return SimpleNamespace(runs=runs[:limit], total=len(runs))


class FakeAccounts:
    def __init__(self):
        self.by_id: dict[str, SimpleNamespace] = {}

    def add(self, account_id, username="carol", status="active"):
        self.by_id[account_id] = SimpleNamespace(
            account_id=account_id, username=username, status=status)
        return self.by_id[account_id]

    def get(self, account_id):
        return self.by_id.get(account_id)

    def list(self):
        return list(self.by_id.values())


class FakeClient:
    def __init__(self):
        self.scheduler = FakeScheduler()
        self.accounts = FakeAccounts()


def make_job(job_id, *, cron="0 9 * * *", status="active", prompt="brief me",
             account_id="acct-1", updated_at=None) -> ScheduledJobDefinition:
    return ScheduledJobDefinition(
        id=job_id, name=f"job {job_id}", prompt=prompt,
        trigger=CronTriggerConfig(expr=cron), timezone="UTC", status=status,
        job_config=AgentRunConfig(prompt=prompt),
        updated_at=updated_at or datetime(2026, 7, 1, tzinfo=timezone.utc),
    )


@pytest.fixture
def fake_client():
    client = FakeClient()
    client.accounts.add("acct-1")
    return client


@pytest.fixture
def fast_settings():
    """Zero the sleep knobs on the cached Settings; restore on teardown."""
    s = get_settings().scheduler
    saved = (
        s.jitter_window_seconds, s.wake_retry_attempts,
        s.wake_retry_base_seconds, s.wake_retry_max_seconds,
        s.admission_retry_window_seconds,
    )
    s.jitter_window_seconds = 0
    s.wake_retry_attempts = 3
    s.wake_retry_base_seconds = 0.01
    s.wake_retry_max_seconds = 0.02
    s.admission_retry_window_seconds = 10
    try:
        yield s
    finally:
        (s.jitter_window_seconds, s.wake_retry_attempts,
         s.wake_retry_base_seconds, s.wake_retry_max_seconds,
         s.admission_retry_window_seconds) = saved
