"""Per-user agent-runtime state: usage overview, stats, analytics, and the
agent-runtime audit feed.

This is served BY the agent-runner because all of it derives from the
per-account /workspace PVC: sessions (claude_agent_sdk.list_sessions over
$work_dir/<user>) and the agent-runtime audit log (get_audit_logger() ->
$PRIVA_HOME/priva, i.e. /workspace/.priva/priva on the runner). The control-panel
mounts no such volume, so it cannot compute any of this — it only holds
control-plane audit (login/auth/user-mgmt), which it serves separately at
/api/auth/audit and the SPA merges in client-side.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime as _dt
from pathlib import Path

from claude_agent_sdk import list_sessions
from fastapi import APIRouter, Depends, Query

from priva_common import skill_exclude as _user_yaml
from priva_common.audit_log import get_audit_logger
from priva_common.logging import get_app_logger
from priva_common.models.admin import AuditEntryResponse, AuditLogResponse
from priva_common.models.auth import (
    UserOverviewBootstrap,
    UserOverviewResponse,
    UserRecord,
)
from priva_common.models.llm_profiles import LlmProfileSummary
from priva_common.models.mcp import McpServerSummary
from priva_common.models.resource import QuickAction
from priva_common.workspace import get_user_workspace
from ..deps import require_user
from ..services.claude_sdk import session_meta
from ..services.compute_user_stats import compute_user_stats
from ..services.mcp.config_manager import McpConfigManager
from .credentials import _migrate_legacy_vision
from ..services.llm_profiles import profile_summary, store

router = APIRouter(prefix="/api/sandbox/user", tags=["user-data"])
logger = get_app_logger(__name__)


def _load_quickactions(username: str) -> list[QuickAction]:
    raw = _user_yaml.get_user_yaml_key(username, "quickactions", [])
    if not isinstance(raw, list):
        return []
    return [
        QuickAction(name=item["name"], prompt=item["prompt"], icon=item.get("icon"))
        for item in raw
        if isinstance(item, dict) and "name" in item and "prompt" in item
    ]


def _load_mcp_servers(username: str) -> list[McpServerSummary]:
    mgr = McpConfigManager(username)
    servers: list[McpServerSummary] = []

    def _append(name: str, config: dict, level: str, cwd: str | None) -> None:
        try:
            servers.append(McpServerSummary(
                name=name,
                type=config.get("type", "http"),
                url=config.get("url", ""),
                level=level,
                cwd=cwd,
                header_count=len(config.get("headers", {})),
                timeout=config.get("timeout", 60),
            ))
        except Exception:
            logger.warning("Skipping invalid MCP server config in overview bootstrap: %s", name)

    for cwd, project_servers in mgr.read_project_groups():
        for name, config in project_servers.items():
            _append(name, config, "project", cwd)
    for name, config in mgr.read_global_servers().items():
        _append(name, config, "global", None)
    return servers


async def _load_overview_bootstrap(user: UserRecord) -> UserOverviewBootstrap:
    profiles, default_profile_id = store.read(_migrate_legacy_vision(user.username))

    recent_activities = session_meta.get_recent_activities()
    active_cwd = (
        recent_activities[0].get("cwd")
        if recent_activities and isinstance(recent_activities[0], dict)
        else None
    )
    return UserOverviewBootstrap(
        quickactions=_load_quickactions(user.username),
        llm_profiles=[LlmProfileSummary.model_validate(profile_summary(profile)) for profile in profiles],
        default_profile_id=default_profile_id,
        mcp_servers=_load_mcp_servers(user.username),
        active_cwd=active_cwd or get_user_workspace(user),
        recent_activities=recent_activities,
    )


@router.get("/overview", response_model=UserOverviewResponse)
async def get_user_overview(user: UserRecord = Depends(require_user)):
    """The usage overview the dashboard renders."""
    stats_task = asyncio.to_thread(compute_user_stats, user.username)
    bootstrap_task = asyncio.create_task(_load_overview_bootstrap(user))
    block, bootstrap = await asyncio.gather(stats_task, bootstrap_task)
    return UserOverviewResponse(
        stats=block.stats,
        heatmap=block.heatmap,
        model_usage=block.model_usage,
        daily_model_tokens=block.daily_model_tokens,
        favorite_model=block.favorite_model,
        current_streak=block.current_streak,
        longest_streak=block.longest_streak,
        peak_hour=block.peak_hour,
        tagline=block.tagline,
        skill_usage=block.skill_usage,
        explored_skills=block.explored_skills,
        skill_invocations=block.skill_invocations,
        bootstrap=bootstrap,
    )


@router.get("/stats")
async def get_user_stats(user: UserRecord = Depends(require_user)):
    user_workspace = Path(get_user_workspace(user))

    session_count = 0
    storage_bytes = 0
    last_active = None

    try:
        sessions = list_sessions(directory=str(user_workspace))
        session_count = len(sessions)
        for s in sessions:
            storage_bytes += s.file_size or 0
            if s.last_modified:
                if last_active is None or s.last_modified > last_active:
                    last_active = s.last_modified
    except Exception:
        pass

    # Temp-file stats live elsewhere; informational only and dropped for the alpha.
    return {
        "username": user.username,
        # The real agent runtime workspace (the per-account /workspace PVC). The
        # SPA shows this instead of any control-panel-sourced path.
        "workspace": str(user_workspace),
        "session_count": session_count,
        "storage_bytes": storage_bytes,
        "file_count": 0,
        "total_file_size": 0,
        "last_active": last_active,
    }


@router.get("/audit", response_model=AuditLogResponse)
async def get_user_audit(
    user: UserRecord = Depends(require_user),
    limit: int = Query(default=50, ge=1, le=200),
    before: str | None = Query(default=None),
    after: str | None = Query(default=None),
    action: str | None = Query(default=None),
    target: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
):
    start_time = _dt.fromisoformat(start) if start else None
    end_time = _dt.fromisoformat(end) if end else None

    audit = get_audit_logger()
    entries, next_cursor, prev_cursor, total = await asyncio.to_thread(
        audit.query_cursor,
        limit=limit,
        before=before,
        after=after,
        action_filter=action,
        actor_filter=user.username,
        target_filter=target,
        start_time=start_time,
        end_time=end_time,
        session_id_filter=session_id,
    )
    return AuditLogResponse(
        entries=[
            AuditEntryResponse(
                id=e.id,
                timestamp=e.timestamp,
                actor=e.actor,
                action=e.action,
                target=e.target,
                details=e.details,
            )
            for e in entries
        ],
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
        total=total,
        limit=limit,
    )


@router.get("/analytics")
async def get_user_analytics(
    user: UserRecord = Depends(require_user),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
):
    start_time = _dt.fromisoformat(start) if start else None
    end_time = _dt.fromisoformat(end) if end else None

    audit = get_audit_logger()

    # Timeline: all user audit entries (up to 500)
    timeline_entries, _, _, _ = await asyncio.to_thread(
        audit.query_cursor,
        limit=500,
        actor_filter=user.username,
        start_time=start_time,
        end_time=end_time,
    )
    timeline = [
        AuditEntryResponse(
            id=e.id,
            timestamp=e.timestamp,
            actor=e.actor,
            action=e.action,
            target=e.target,
            details=e.details,
        ).model_dump(mode="json")
        for e in timeline_entries
    ]

    # Skill usage: aggregate skill.invoked by target (skill name)
    skill_entries, _, _, _ = await asyncio.to_thread(
        audit.query_cursor,
        limit=500,
        action_filter="skill.invoked",
        actor_filter=user.username,
        start_time=start_time,
        end_time=end_time,
    )
    skill_counter: Counter[str] = Counter()
    for e in skill_entries:
        skill_name = e.target or "unknown"
        skill_counter[skill_name] += 1
    skill_usage = [
        {"skill": name, "count": count}
        for name, count in skill_counter.most_common(10)
    ]

    # Session activity: sessions per day
    user_workspace = Path(get_user_workspace(user))
    session_day_counter: Counter[str] = Counter()
    try:
        sessions = list_sessions(directory=str(user_workspace))
        for s in sessions:
            if s.last_modified:
                day = s.last_modified.strftime("%Y-%m-%d")
                if start_time and s.last_modified < start_time:
                    continue
                if end_time and s.last_modified > end_time:
                    continue
                session_day_counter[day] += 1
    except Exception:
        pass
    session_activity = [
        {"date": day, "count": count}
        for day, count in sorted(session_day_counter.items())
    ]

    return {
        "timeline": timeline,
        "skill_usage": skill_usage,
        "session_activity": session_activity,
    }
