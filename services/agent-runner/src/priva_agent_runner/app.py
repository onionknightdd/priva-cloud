"""agent-runner FastAPI app.

Single-account runtime. Account pinning (CLAUDE_CONFIG_DIR / HOME / PRIVA_HOME /
ACCOUNT_ID / USERNAME / WORKSPACE_DIR) happens in ``entry.py`` *before* this
module is imported, so by the time the lifespan runs the process env already
points at the one account's workspace. Serves JSON/WS only — no HTML (the
control-panel is the single front door, agent-runner.md §0).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI

from priva_common.config import get_settings
from priva_common.logging import AccessLogMiddleware, configure_logging, get_app_logger, shutdown_logging
from priva_common.models.auth import UserRecord
from priva_common.workspace import get_user_workspace

from . import activity

logger = get_app_logger(__name__)


class ActivityMiddleware:
    """Track in-flight requests + last-activity for the operator's idle sweep.
    Excludes /health so the operator's own probes don't keep the pod awake."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or scope.get("path", "").startswith("/health"):
            await self.app(scope, receive, send)
            return
        activity.enter()
        try:
            await self.app(scope, receive, send)
        finally:
            activity.leave()


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        configure_logging(settings)

        # Compose the in-process data-plane (shared SQLite with control-panel).
        from priva_data_spine import compose
        compose()
        logger.info(
            "data-plane composed: transport={}, backend={}, sqlite={}",
            settings.dataspine.transport,
            settings.dataspine.backend,
            settings.dataspine.sqlite_path,
        )

        # Eager audit logger (PRIVA_HOME is already pinned by entry.py).
        from priva_common.audit_log import get_audit_logger
        get_audit_logger()

        # Seed runtime skills from the baked-in bundle.
        try:
            from .services.skill_hub import seed_bundled_skills
            seed_bundled_skills()
        except Exception as exc:
            logger.warning("Skill seeding skipped: {}", exc)

        import os
        logger.info(
            "agent-runner ready: account={}, user={}, workspace={}",
            os.environ.get("ACCOUNT_ID"),
            os.environ.get("USERNAME"),
            os.environ.get("WORKSPACE_DIR"),
        )
        try:
            yield
        finally:
            logger.info("agent-runner shutdown complete")
            shutdown_logging()

    app = FastAPI(
        title="Priva agent-runner",
        version=settings.app_version,
        docs_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(ActivityMiddleware)

    @app.get("/health", include_in_schema=False)
    async def health():
        import os
        active, last = activity.snapshot()
        return {
            "status": "ok",
            "service": "agent-runner",
            "account_id": os.environ.get("ACCOUNT_ID"),
            "active_runs": active,
            "last_activity_ts": last,
            "time": datetime.now(timezone.utc).isoformat(),
        }

    from .deps import require_user

    @app.get("/api/health", include_in_schema=False)
    async def api_health(user: UserRecord = Depends(require_user)):
        """Per-account readiness + first-page bootstrap, reachable from the SPA via
        the gateway (the unauthenticated /health above is for the k8s probe only).
        A cold sandbox 503s at the edge EPP until this pod answers, so the SPA polls
        this through fetchWithWake — showing the "waking"/"ready" toasts — and renders
        the first page (the cwd chip) from the returned workspace. Counts as activity
        (path is not /health), so loading the app keeps the warm pod alive."""
        return {
            "status": "ok",
            "service": "agent-runner",
            "username": user.username,
            "workspace": get_user_workspace(user),
        }

    from .routers.agent import router as agent_router
    from .routers.pty import router as pty_router
    from .routers.files import router as files_router
    from .routers.user_files import router as user_files_router
    from .routers.hooks import router as hooks_router
    from .routers.mcp import router as mcp_router
    from .routers.skills import router as skills_router
    from .routers.skill_hub import router as skill_hub_router
    from .routers.subagents import router as subagents_router
    from .routers.user_config import router as user_config_router
    from .routers.user_data import router as user_data_router

    for r in (
        agent_router, pty_router, files_router, user_files_router,
        hooks_router, mcp_router, skills_router, skill_hub_router, subagents_router,
        user_config_router, user_data_router,
    ):
        app.include_router(r)

    return app


app = create_app()
