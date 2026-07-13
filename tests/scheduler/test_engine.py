"""SchedulerEngine unit tests: fire_epoch canon, arm/disarm diffing, and every
branch of the fire pipeline (claim → checks → StartRun → dispatch verdicts)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from priva_common.models.scheduler import (
    AgentRunConfig,
    IntervalTriggerConfig,
    ScheduledJobDefinition,
)

from priva_scheduler.dispatch import DispatchError
from priva_scheduler.engine import SchedulerEngine, fire_epoch_for

from .conftest import make_job


class ScriptedDispatcher:
    def __init__(self, verdict="accepted"):
        self.verdict = verdict
        self.calls: list = []

    async def dispatch(self, account_id, username, frame):
        self.calls.append((account_id, username, frame))
        if isinstance(self.verdict, Exception):
            raise self.verdict
        return self.verdict


def make_engine(fake_client, verdict="accepted"):
    dispatcher = ScriptedDispatcher(verdict)
    engine = SchedulerEngine(fake_client, dispatcher, replica_id="replica-test")
    return engine, dispatcher


# --- fire_epoch: the cross-replica dedupe key --------------------------------


def test_fire_epoch_cron_floors_to_minute(fake_client):
    job = make_job("j1", cron="0 9 * * *")
    minute = 1780000020  # divisible by 60 — a scheduled minute boundary
    assert fire_epoch_for(job, minute + 15.7) == minute
    # every replica firing inside the same minute computes the SAME key
    assert fire_epoch_for(job, minute + 1.0) == fire_epoch_for(job, minute + 59.9)
    assert fire_epoch_for(job, minute + 60.0) != minute


def test_fire_epoch_interval_buckets_by_period():
    job = ScheduledJobDefinition(
        id="j2", name="every 6h", trigger=IntervalTriggerConfig(hours=6),
        timezone="UTC", prompt="tick")
    period = 6 * 3600
    bucket = 82408 * period  # a period-aligned instant
    # replicas armed at different instants fire at different wall clocks but
    # land in the same period bucket → one claim per period
    assert fire_epoch_for(job, bucket) == bucket
    assert fire_epoch_for(job, bucket) == fire_epoch_for(job, bucket + period - 1)
    assert fire_epoch_for(job, bucket + period) != fire_epoch_for(job, bucket)


def test_arm_anchors_interval_phase_to_created_at(fake_client):
    engine, _ = make_engine(fake_client)
    created = datetime(2026, 7, 1, 10, 7, tzinfo=timezone.utc)
    job = ScheduledJobDefinition(
        id="j5", name="every 4h", prompt="tick",
        trigger=IntervalTriggerConfig(hours=4), timezone="UTC",
        job_config=AgentRunConfig(prompt="tick"), created_at=created)
    fake_client.scheduler.jobs["j5"] = ("acct-1", job)

    asyncio.run(engine.sync_jobs())

    (armed,) = engine._scheduler.get_jobs()
    assert armed.trigger.start_date == created
    # ticks are created_at + k*interval whatever the arm clock — the same
    # instants the user API computes for next_run_time
    now = datetime(2026, 7, 13, 9, 0, tzinfo=timezone.utc)
    nxt = armed.trigger.get_next_fire_time(None, now)
    assert nxt == created + timedelta(hours=288)  # 2026-07-13 10:07 UTC
    # …and a later poll inside the same gap agrees (no phase slide)
    assert armed.trigger.get_next_fire_time(None, now + timedelta(minutes=30)) == nxt


# --- arm/disarm diffing (D6 re-list) ------------------------------------------


def test_sync_jobs_arms_rearms_and_disarms(fake_client):
    engine, _ = make_engine(fake_client)
    sched = fake_client.scheduler
    sched.jobs["j1"] = ("acct-1", make_job("j1"))
    sched.jobs["j2"] = ("acct-1", make_job("j2", cron="30 18 * * 5"))

    out = asyncio.run(engine.sync_jobs())
    assert out == {"armed": 2, "removed": 0, "total": 2}

    # unchanged set → no churn
    assert asyncio.run(engine.sync_jobs()) == {"armed": 0, "removed": 0, "total": 2}

    # an edit bumps updated_at → re-arm exactly that job
    sched.jobs["j1"] = ("acct-1", make_job(
        "j1", cron="0 8 * * *", updated_at=datetime(2026, 7, 2, tzinfo=timezone.utc)))
    assert asyncio.run(engine.sync_jobs()) == {"armed": 1, "removed": 0, "total": 2}

    # pause → drops out of the active list → disarm
    sched.jobs["j2"] = ("acct-1", make_job("j2", status="paused"))
    assert asyncio.run(engine.sync_jobs()) == {"armed": 0, "removed": 1, "total": 1}

    # bad trigger never arms (and never crashes the loop)
    sched.jobs["j3"] = ("acct-1", make_job("j3", cron="not a cron"))
    assert asyncio.run(engine.sync_jobs()) == {"armed": 0, "removed": 0, "total": 1}


# --- the fire pipeline ---------------------------------------------------------


def test_fire_happy_path_dispatch_accepted(fake_client, fast_settings):
    engine, dispatcher = make_engine(fake_client)
    fake_client.scheduler.jobs["j1"] = ("acct-1", make_job("j1"))

    outcome = asyncio.run(engine.fire("acct-1", "j1"))

    assert outcome == "dispatched"
    # birth record written by the scheduler; outcome stays with the pod (D13)
    (run,) = fake_client.scheduler.runs.values()
    assert run.status == "running" and run.job_id == "j1" and run.username == "carol"
    assert fake_client.scheduler.finishes == []
    # the dispatched frame carries the run identity + config
    ((acct, username, frame),) = [dispatcher.calls[0]]
    assert acct == "acct-1" and username == "carol"
    assert frame.run_id == run.run_id and frame.job_config.job_type == "agent_run"


def test_fire_claim_lost_is_silent(fake_client, fast_settings):
    engine, dispatcher = make_engine(fake_client)
    fake_client.scheduler.jobs["j1"] = ("acct-1", make_job("j1"))
    fake_client.scheduler.deny_claims = True

    assert asyncio.run(engine.fire("acct-1", "j1")) == "claim_lost"
    assert not dispatcher.calls
    assert not fake_client.scheduler.runs and not fake_client.scheduler.records


def test_fire_same_epoch_claims_once(fake_client, fast_settings):
    engine, dispatcher = make_engine(fake_client)
    fake_client.scheduler.jobs["j1"] = ("acct-1", make_job("j1"))

    assert asyncio.run(engine.fire("acct-1", "j1")) == "dispatched"
    # second fire lands in the same minute bucket → claim already taken.
    # (also exercises the overlap path being short-circuited by the claim)
    assert asyncio.run(engine.fire("acct-1", "j1")) == "claim_lost"
    assert len(dispatcher.calls) == 1


def test_fire_account_disabled_skips_without_wake(fake_client, fast_settings):
    engine, dispatcher = make_engine(fake_client)
    fake_client.scheduler.jobs["j1"] = ("acct-1", make_job("j1"))
    fake_client.accounts.by_id["acct-1"].status = "disabled"

    assert asyncio.run(engine.fire("acct-1", "j1")) == "skipped_inactive"
    assert not dispatcher.calls  # D8: no wake for disabled accounts
    (rec,) = fake_client.scheduler.records
    assert rec.status == "skipped" and rec.error_message == "account_disabled"


def test_fire_overlap_skips_before_dispatch(fake_client, fast_settings):
    engine, dispatcher = make_engine(fake_client)
    fake_client.scheduler.jobs["j1"] = ("acct-1", make_job("j1"))
    # yesterday's run of THIS job still running (US-5)
    asyncio.run(engine.fire("acct-1", "j1"))
    dispatcher.calls.clear()
    fake_client.scheduler.claims.clear()  # let the next epoch claim through

    assert asyncio.run(engine.fire("acct-1", "j1")) == "skipped_busy"
    assert not dispatcher.calls  # no wake, no pod
    (rec,) = fake_client.scheduler.records
    assert rec.status == "skipped" and rec.error_message == "already_running"


def test_fire_runner_409_backstop_finishes_skipped(fake_client, fast_settings):
    engine, _ = make_engine(fake_client, verdict="job_overlap")
    fake_client.scheduler.jobs["j1"] = ("acct-1", make_job("j1"))

    assert asyncio.run(engine.fire("acct-1", "j1")) == "skipped_busy"
    (fin,) = fake_client.scheduler.finishes
    assert fin.status == "skipped" and fin.error_message == "already_running"


def test_fire_cap_429_finishes_skipped_cap(fake_client, fast_settings):
    engine, _ = make_engine(fake_client, verdict="concurrency_cap")
    fake_client.scheduler.jobs["j1"] = ("acct-1", make_job("j1"))

    assert asyncio.run(engine.fire("acct-1", "j1")) == "skipped_cap"
    (fin,) = fake_client.scheduler.finishes
    assert fin.status == "skipped" and fin.error_message == "concurrency_cap"


def test_fire_dispatch_exhausted_records_wake_failed(fake_client, fast_settings):
    engine, _ = make_engine(fake_client, verdict=DispatchError("wake_failed"))
    fake_client.scheduler.jobs["j1"] = ("acct-1", make_job("j1"))

    assert asyncio.run(engine.fire("acct-1", "j1")) == "error"
    (fin,) = fake_client.scheduler.finishes
    assert fin.status == "error" and fin.error_message == "wake_failed"
    assert fin.is_error is True


def test_scheduled_fire_on_paused_job_is_noop_but_manual_runs(fake_client, fast_settings):
    engine, dispatcher = make_engine(fake_client)
    fake_client.scheduler.jobs["j1"] = ("acct-1", make_job("j1", status="paused"))

    # the ≤30s disarm race: a scheduled fire of a paused job does nothing
    assert asyncio.run(engine.fire("acct-1", "j1")) == "paused"
    assert not dispatcher.calls
    # round-3: Run-now on a paused job is a sanctioned one-shot
    assert asyncio.run(engine.fire("acct-1", "j1", manual=True)) == "dispatched"
    assert len(dispatcher.calls) == 1


def test_trigger_now_finds_unarmed_job_and_unknown_is_none(fake_client, fast_settings):
    engine, dispatcher = make_engine(fake_client)
    fake_client.scheduler.jobs["j1"] = ("acct-1", make_job("j1", status="paused"))

    assert asyncio.run(engine.trigger_now("j1")) == "dispatched"  # account found by scan
    assert asyncio.run(engine.trigger_now("ghost")) is None


def test_fire_without_dispatchable_config_records_error(fake_client, fast_settings):
    engine, dispatcher = make_engine(fake_client)
    job = make_job("j1")
    job.job_config = None
    job.prompt = ""
    fake_client.scheduler.jobs["j1"] = ("acct-1", job)

    assert asyncio.run(engine.fire("acct-1", "j1")) == "error"
    assert not dispatcher.calls
    (rec,) = fake_client.scheduler.records
    assert rec.status == "error" and rec.error_message == "invalid_job_config"
