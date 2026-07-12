"""D15 transcript retention: on pod boot, delete scheduler-origin session
JSONLs older than ``settings.scheduler.history_retention_days`` (default 7).

Only sessions in the session-meta scheduler index are candidates — interactive
transcripts are never touched. Run records in data-spine persist forever; a
pruned run loses only its "open session" link. Scheduled runs never write
add_dirs sidecars (the executor passes none), so the JSONL + the session's
subagent dir are the whole footprint.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from claude_agent_sdk._internal.sessions import _get_claude_config_home_dir

from priva_common.config import get_settings
from priva_common.logging import get_app_logger

from ..claude_sdk import session_meta

logger = get_app_logger(__name__)


def _transcript_paths(session_id: str) -> list[Path]:
    projects = _get_claude_config_home_dir() / "projects"
    if not projects.is_dir():
        return []
    return list(projects.rglob(f"{session_id}.jsonl"))


async def prune_scheduler_transcripts(retention_days: int | None = None) -> int:
    """Delete scheduler-origin transcripts past retention; returns the count."""
    days = (
        get_settings().scheduler.history_retention_days
        if retention_days is None else retention_days
    )
    if days <= 0:
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for session_id in list(session_meta.list_scheduler_sessions()):
        paths = _transcript_paths(session_id)
        if not paths:
            # Transcript already gone (user delete) — drop the index row.
            await session_meta.prune_session(session_id)
            continue
        try:
            newest = max(p.stat().st_mtime for p in paths)
        except OSError:
            continue
        if newest >= cutoff:
            continue
        for p in paths:
            try:
                p.unlink()
            except OSError:
                logger.warning("[SCHED] prune could not delete {}", p)
            sidecar_dir = p.with_suffix("")  # <sid>/ (subagent transcripts)
            if sidecar_dir.is_dir():
                shutil.rmtree(sidecar_dir, ignore_errors=True)
        await session_meta.prune_session(session_id)
        removed += 1
    if removed:
        logger.info(
            "[SCHED] pruned {} scheduler-origin transcript(s) older than {}d",
            removed, days,
        )
    return removed
