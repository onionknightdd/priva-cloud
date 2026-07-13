"""Trigger math for the user API: validation + server-computed next_run_time
(the same APScheduler classes the firing engine arms, so the preview the UI
shows is exactly what will fire)."""

from __future__ import annotations

from datetime import datetime, timezone

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from priva_common.models.scheduler import CronTriggerConfig, IntervalTriggerConfig


def build_trigger(config, tz: str, *, anchor: datetime | None = None):
    if isinstance(config, CronTriggerConfig):
        return CronTrigger.from_crontab(config.expr, timezone=tz)
    if isinstance(config, IntervalTriggerConfig):
        if interval_total_seconds(config) <= 0:
            raise ValueError("interval must be positive")
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


def interval_total_seconds(config: IntervalTriggerConfig) -> int:
    return (
        config.weeks * 604800 + config.days * 86400 + config.hours * 3600
        + config.minutes * 60 + config.seconds
    )


def next_run_time(config, tz: str, *, anchor: datetime | None = None) -> str | None:
    """ISO next fire instant, or None when the trigger has no future fire.

    ``anchor`` (the job's created_at) pins the interval phase to the same
    start_date the engine arms — the engine fires at ``anchor + k*interval``,
    so this returns that actual instant, stable across polls. Unanchored,
    an IntervalTrigger defaults start_date to now + interval and the value
    would slide with the request clock.
    """
    trigger = build_trigger(config, tz, anchor=anchor)
    nxt = trigger.get_next_fire_time(None, datetime.now(timezone.utc))
    return nxt.isoformat() if nxt else None
