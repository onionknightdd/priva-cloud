"""The firing engine — a leaderless per-replica clock over the dataplane job
set (design §4).

Every replica re-lists ``ListActiveJobs`` every ~30s (D6) and arms the SAME
triggers on its local APScheduler; the DB row is the truth, the trigger only
its in-memory shadow. On fire, the pipeline is:

    claim (exactly-once, D5) → active-check (D8) → overlap-check (D9)
    → StartRun (history shows RUNNING instantly) → Dispatcher.dispatch (D1)

The engine never executes a job and holds no state outside the DB — kill or
roll replicas freely (US-8 misfire semantics cover the gap).

``fire_epoch`` must be the same number on every replica for the same logical
fire (it IS the dedupe key). APScheduler doesn't hand the callback its
scheduled time, so we derive a canonical epoch from the trigger shape:
cron → the fire's minute (cron granularity); interval → the period bucket
``now // period * period`` (interval ticks are anchored to the job's
created_at, so replicas fire the same instants; buckets keep the key immune
to clock skew between them; at most one run per period).
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from uuid import uuid4

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from priva_common.config import get_settings
from priva_common.logging import get_app_logger
from priva_common.metrics import (
    SCHEDULER_ARMED_JOBS,
    SCHEDULER_CLAIM_LOST,
    SCHEDULER_DISPATCH_SECONDS,
    SCHEDULER_FIRES,
)
from priva_common.models.scheduler import (
    AgentRunConfig,
    CronTriggerConfig,
    IntervalTriggerConfig,
    JobRunRecord,
    ScheduledJobDefinition,
    ScheduledRunRequest,
)

from .dispatch import DispatchError, Dispatcher
from .reconcile import sweep_once

logger = get_app_logger(__name__)


def build_trigger(config, tz: str, *, anchor: datetime | None = None):
    """APScheduler trigger from a stored TriggerConfig (fork of the monolith's
    ``shared.build_trigger`` — the only piece of it that carries over).

    ``anchor`` (the job's immutable created_at) pins the interval phase:
    every replica ticks at ``anchor + k*interval`` regardless of when it
    armed, so the schedule is deterministic across restarts and re-arms —
    and the user API can compute the same instants for display. Without it
    APScheduler defaults start_date to arm-time + interval (phase drift).
    """
    if isinstance(config, CronTriggerConfig):
        return CronTrigger.from_crontab(config.expr, timezone=tz)
    if isinstance(config, IntervalTriggerConfig):
        return IntervalTrigger(
            weeks=config.weeks, days=config.days, hours=config.hours,
            minutes=config.minutes, seconds=config.seconds,
            start_date=_aware_utc(anchor),
        )
    raise ValueError(f"Unknown trigger type: {config}")


def _aware_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def interval_seconds(config: IntervalTriggerConfig) -> int:
    return (
        config.weeks * 604800 + config.days * 86400 + config.hours * 3600
        + config.minutes * 60 + config.seconds
    )


def fire_epoch_for(job: ScheduledJobDefinition, now: float) -> int:
    """The replica-independent dedupe key for this fire (see module docstring)."""
    if isinstance(job.trigger, IntervalTriggerConfig):
        period = max(interval_seconds(job.trigger), 1)
        return int(now // period * period)
    return int(now // 60 * 60)  # cron: the scheduled minute


def _job_jitter_seconds(job_id: str, window: float) -> float:
    """Stable per-job wake offset — spreads the 09:00 storm (design §4)."""
    if window <= 0:
        return 0.0
    return (hash(job_id) % int(window * 10)) / 10.0


class SchedulerEngine:
    def __init__(self, client, dispatcher: Dispatcher, *, replica_id: str):
        self._client = client
        self._dispatcher = dispatcher
        self._replica_id = replica_id
        self._scheduler = AsyncIOScheduler(timezone="UTC")
        # job_id → (account_id, updated_at-iso): the diff key for re-arming.
        self._armed: dict[str, tuple[str, str]] = {}
        self._tasks: list[asyncio.Task] = []

    # --- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        self._scheduler.start()
        await self.sync_jobs()  # arm before the first relist tick
        self._tasks = [
            asyncio.create_task(self._relist_loop(), name="scheduler-relist"),
            asyncio.create_task(self._reconcile_loop(), name="scheduler-reconcile"),
        ]
        logger.info("engine started replica={} armed={}", self._replica_id, len(self._armed))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        self._scheduler.shutdown(wait=False)

    @property
    def armed_count(self) -> int:
        return len(self._armed)

    @property
    def replica_id(self) -> str:
        return self._replica_id

    # --- job-set propagation (D6: re-list, no pub/sub) --------------------

    async def _relist_loop(self) -> None:
        s = get_settings().scheduler
        while True:
            await asyncio.sleep(s.relist_seconds)
            try:
                await self.sync_jobs()
            except Exception:
                logger.exception("re-list failed; keeping the current arm set")

    async def sync_jobs(self) -> dict:
        """One re-list pass: diff by (job_id, updated_at) → arm/re-arm/disarm."""
        active = await asyncio.to_thread(self._client.scheduler.list_active_jobs)
        seen: dict[str, tuple[str, str]] = {}
        armed = removed = 0
        for account_id, job in active:
            key = (account_id, job.updated_at.isoformat() if job.updated_at else "")
            seen[job.id] = key
            if self._armed.get(job.id) == key:
                continue
            if self._arm(account_id, job):
                self._armed[job.id] = key
                armed += 1
        for job_id in list(self._armed):
            if job_id not in seen:
                self._disarm(job_id)
                removed += 1
        SCHEDULER_ARMED_JOBS.set(len(self._armed))
        if armed or removed:
            logger.info("job set synced: +{} -{} (armed={})", armed, removed, len(self._armed))
        return {"armed": armed, "removed": removed, "total": len(self._armed)}

    def _arm(self, account_id: str, job: ScheduledJobDefinition) -> bool:
        try:
            trigger = build_trigger(job.trigger, job.timezone, anchor=job.created_at)
        except Exception as exc:
            logger.error("cannot arm job {} ({}): {}", job.id, job.name, exc)
            return False
        s = get_settings().scheduler
        self._scheduler.add_job(
            self._on_aps_fire,
            trigger=trigger,
            id=job.id,
            args=[account_id, job.id],
            replace_existing=True,
            misfire_grace_time=s.misfire_grace_seconds,  # US-8: late-once within grace
            coalesce=True,                               # …and only once
            max_instances=1,  # per replica per job; cross-replica dedupe is the claim
        )
        return True

    def _disarm(self, job_id: str) -> None:
        self._armed.pop(job_id, None)
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass  # never armed / already gone

    # --- the fire pipeline -------------------------------------------------

    async def _on_aps_fire(self, account_id: str, job_id: str) -> None:
        try:
            await self.fire(account_id, job_id)
        except Exception:
            logger.exception("fire pipeline crashed job={}", job_id)

    async def fire(self, account_id: str, job_id: str, *, manual: bool = False) -> str:
        """Run the claim→checks→StartRun→dispatch pipeline once.

        Returns the outcome label (metrics vocabulary). ``manual`` = the
        internal trigger API: fire_epoch=now, allowed on paused jobs
        (round-3: run-now on paused is a sanctioned one-shot), no jitter.
        """
        c = self._client
        job = await asyncio.to_thread(c.scheduler.get_job, job_id)
        if job is None:
            return "gone"  # deleted mid-flight; the next re-list disarms
        if not manual and job.status != "active":
            return "paused"  # paused between arm and fire (≤30s window)

        now = time.time()
        epoch = int(now) if manual else fire_epoch_for(job, now)
        claimed = await asyncio.to_thread(
            c.scheduler.claim_fire, job_id, epoch, self._replica_id)
        if not claimed:
            SCHEDULER_CLAIM_LOST.inc()
            return "claim_lost"

        account = await asyncio.to_thread(c.accounts.get, account_id)
        if account is None:
            logger.warning("fire {}: account {} gone", job_id, account_id)
            return "gone"
        if account.status != "active":
            await self._record_skip(account_id, job, account.username, "account_disabled")
            SCHEDULER_FIRES.labels(outcome="skipped_inactive").inc()
            return "skipped_inactive"

        latest = await asyncio.to_thread(c.scheduler.get_latest_run, account_id, job_id)
        if latest is not None and latest.status == "running":
            await self._record_skip(account_id, job, account.username, "already_running")
            SCHEDULER_FIRES.labels(outcome="skipped_busy").inc()
            return "skipped_busy"

        frame = self._build_frame(job)
        run_id = str(uuid4())
        if frame is None:
            await self._record_skip(
                account_id, job, account.username, "invalid_job_config", status="error")
            SCHEDULER_FIRES.labels(outcome="error").inc()
            return "error"
        frame.run_id = run_id

        await asyncio.to_thread(c.scheduler.start_run, account_id, JobRunRecord(
            run_id=run_id, job_id=job_id, job_name=job.name,
            username=account.username, status="running",
        ))

        if not manual:
            jitter = _job_jitter_seconds(job_id, get_settings().scheduler.jitter_window_seconds)
            if jitter:
                await asyncio.sleep(jitter)

        # The first lifecycle check preceded StartRun and optional jitter. Fence
        # the actual delivery as well so an admin disable in that window cannot
        # wake or dial the tenant pod. WakeDialDispatcher repeats this before
        # every network retry.
        latest_account = await asyncio.to_thread(c.accounts.get, account_id)
        if latest_account is None or latest_account.status != "active":
            await self._finish(job, run_id, "skipped", "account_disabled")
            SCHEDULER_FIRES.labels(outcome="skipped_inactive").inc()
            return "skipped_inactive"

        t0 = time.monotonic()
        try:
            verdict = await self._dispatcher.dispatch(account_id, account.username, frame)
        except DispatchError as exc:
            await self._finish(job, run_id, "error", exc.reason)
            SCHEDULER_FIRES.labels(outcome="error").inc()
            return "error"
        finally:
            SCHEDULER_DISPATCH_SECONDS.observe(time.monotonic() - t0)

        if verdict == "accepted":
            SCHEDULER_FIRES.labels(outcome="dispatched").inc()
            logger.info("dispatched job={} run={} account={}", job_id, run_id, account_id)
            return "dispatched"
        if verdict == "job_overlap":
            await self._finish(job, run_id, "skipped", "already_running")
            SCHEDULER_FIRES.labels(outcome="skipped_busy").inc()
            return "skipped_busy"
        if verdict == "account_inactive":
            await self._finish(job, run_id, "skipped", "account_disabled")
            SCHEDULER_FIRES.labels(outcome="skipped_inactive").inc()
            return "skipped_inactive"
        # concurrency_cap (D16: the ≤2 min re-admit already happened downstream)
        await self._finish(job, run_id, "skipped", "concurrency_cap")
        SCHEDULER_FIRES.labels(outcome="skipped_cap").inc()
        return "skipped_cap"

    def _build_frame(self, job: ScheduledJobDefinition) -> ScheduledRunRequest | None:
        cfg = job.job_config
        if cfg is None and job.prompt:
            cfg = AgentRunConfig(prompt=job.prompt, model=job.model)
        if cfg is None:
            return None
        try:
            return ScheduledRunRequest(
                run_id="",  # stamped by the caller after StartRun id minting
                job_id=job.id, job_name=job.name,
                job_config=cfg, model=job.model,
            )
        except Exception:  # e.g. a dormant tool_retry row forced active
            logger.warning("job {} config not dispatchable: {}", job.id, type(cfg).__name__)
            return None

    # --- record writes (birth/skip are the scheduler's; outcomes are the pod's) --

    async def _record_skip(
        self, account_id: str, job: ScheduledJobDefinition, username: str,
        reason: str, *, status: str = "skipped",
    ) -> None:
        try:
            await asyncio.to_thread(
                self._client.scheduler.record_run, account_id, JobRunRecord(
                    run_id=str(uuid4()), job_id=job.id, job_name=job.name,
                    username=username, status=status,
                    finished_at=datetime.now(timezone.utc),
                    is_error=status == "error", error_message=reason,
                ))
        except Exception:
            logger.exception("record_run({}) failed job={}", reason, job.id)

    async def _finish(
        self, job: ScheduledJobDefinition, run_id: str, status: str, reason: str
    ) -> None:
        try:
            await asyncio.to_thread(self._client.scheduler.finish_run, JobRunRecord(
                run_id=run_id, job_id=job.id, job_name=job.name, username="",
                status=status, finished_at=datetime.now(timezone.utc),
                is_error=status == "error", error_message=reason,
            ))
        except Exception:
            logger.exception("finish_run({}) failed run={}", reason, run_id)

    # --- run-now (US-6 via the internal API) --------------------------------

    async def trigger_now(self, job_id: str) -> str | None:
        """Synthetic fire through the same claim dance (double-click safe).
        Returns the outcome, or None when the job doesn't exist anywhere."""
        armed = self._armed.get(job_id)
        if armed:
            account_id = armed[0]
        else:
            account_id = await self._find_account_for_job(job_id)
            if account_id is None:
                return None
        return await self.fire(account_id, job_id, manual=True)

    async def _find_account_for_job(self, job_id: str) -> str | None:
        """Paused/unarmed jobs aren't in the armed map — scan per account
        (v1 scale; the M5 'admin-list accepted-slower' precedent)."""
        job = await asyncio.to_thread(self._client.scheduler.get_job, job_id)
        if job is None:
            return None
        accounts = await asyncio.to_thread(self._client.accounts.list)
        for account in accounts:
            jobs = await asyncio.to_thread(self._client.scheduler.list_jobs, account.account_id)
            if any(j.id == job_id for j in jobs):
                return account.account_id
        return None

    # --- reconcile (design §4 sweep) ----------------------------------------

    async def _reconcile_loop(self) -> None:
        s = get_settings().scheduler
        while True:
            await asyncio.sleep(s.sweep_seconds)
            try:
                await sweep_once(self._client)
            except Exception:
                logger.exception("reconcile sweep failed")
