"""Trigger math for the user API: validation + server-computed next_run_time
(the same APScheduler classes the firing engine arms, so the preview the UI
shows is exactly what will fire)."""

from __future__ import annotations

from datetime import datetime, timezone

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from priva_common.models.scheduler import CronTriggerConfig, IntervalTriggerConfig


def build_trigger(config, tz: str):
    if isinstance(config, CronTriggerConfig):
        return CronTrigger.from_crontab(config.expr, timezone=tz)
    if isinstance(config, IntervalTriggerConfig):
        if interval_total_seconds(config) <= 0:
            raise ValueError("interval must be positive")
        return IntervalTrigger(
            weeks=config.weeks, days=config.days, hours=config.hours,
            minutes=config.minutes, seconds=config.seconds,
        )
    raise ValueError(f"Unknown trigger type: {config}")


def interval_total_seconds(config: IntervalTriggerConfig) -> int:
    return (
        config.weeks * 604800 + config.days * 86400 + config.hours * 3600
        + config.minutes * 60 + config.seconds
    )


def next_run_time(config, tz: str) -> str | None:
    """ISO next fire instant, or None when the trigger has no future fire."""
    trigger = build_trigger(config, tz)
    nxt = trigger.get_next_fire_time(None, datetime.now(timezone.utc))
    return nxt.isoformat() if nxt else None
