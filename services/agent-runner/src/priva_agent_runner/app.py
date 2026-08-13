"""agent-runner FastAPI app.

Single-account runtime. Account pinning (CLAUDE_CONFIG_DIR / HOME / PRIVA_HOME /
ACCOUNT_ID / USERNAME / WORKSPACE_DIR) happens in ``entry.py`` *before* this
module is imported, so by the time the lifespan runs the process env already
points at the one account's workspace. Serves JSON/WS only — no app HTML (the
control-panel is the single front door, agent-runner.md §0); the one exception is
the self-describing OpenAPI surface at /sandbox/apidocs. All runtime routes live under
the /api/sandbox/* namespace so the edge can steer them to the account's pod with a
single gateway rule, distinct from the control-plane /api/* served by control-panel.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, HTMLResponse

from priva_common.config import get_settings
from priva_common.body_limit import MaxBodySizeMiddleware
from priva_common.logging import AccessLogMiddleware, configure_logging, get_app_logger, shutdown_logging
from priva_common.models.auth import UserRecord
from priva_common.workspace import get_user_workspace

from . import activity

logger = get_app_logger(__name__)

# Hard ceiling on any request body reaching this pod. Sits just above the
# largest per-route limit (user_files' 100MB) so the route-level errors stay the
# ones users normally see; this exists to stop the multipart parser writing an
# unbounded body into /tmp before any route code runs.
MAX_REQUEST_BODY_BYTES = 110 * 1024 * 1024


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

        # In-process transport only: compose the data-plane repo (dev mode's
        # shared SQLite with control-panel). gRPC transport: this pod is a
        # data-plane *client* — never build a repo (backend/postgres_dsn stay
        # with the data-spine pod; a DSN here would hand DB credentials to
        # every tenant pod, readable from the web terminal).
        if settings.dataspine.transport == "in_process":
            from priva_data_spine import compose
            compose()
        logger.info(
            "data-plane transport={}, backend={}, dsn={}",
            settings.dataspine.transport,
            settings.dataspine.backend,
            settings.dataspine.grpc_dsn,
        )

        # Eager audit logger (PRIVA_HOME is already pinned by entry.py).
        from priva_common.audit_log import get_audit_logger
        get_audit_logger()

        # One-time: relocate resources stranded in the legacy ~/.claude dir —
        # the CLI only reads $CLAUDE_CONFIG_DIR (see services/legacy_claude_dir.py).
        try:
            from .services.legacy_claude_dir import migrate_legacy_home_claude
            migrate_legacy_home_claude()
        except Exception as exc:
            logger.warning("legacy ~/.claude migration skipped: {}", exc)

        # One-time (D5): relocate user hooks from the CLI-invisible
        # settings.local.json into the CLI-loaded settings.json so the CLI runs
        # them natively (setting_sources omits the "local" source).
        try:
            from .services.legacy_claude_dir import migrate_local_hooks_to_settings
            migrate_local_hooks_to_settings()
        except Exception as exc:
            logger.warning("settings.local hooks migration skipped: {}", exc)

        # Seed platform-required settings.json defaults (setdefault semantics):
        # enableAllProjectMcpServers so headless runs load UI-managed .mcp.json
        # servers (they'd otherwise sit "Pending approval" forever).
        try:
            from priva_common.user_env import ensure_claude_settings_defaults
            ensure_claude_settings_defaults()
        except Exception as exc:
            logger.warning("settings.json defaults seeding skipped: {}", exc)

        # Migrate the retired single-provider settings into the canonical
        # app-config profile store before the first run.  The old env keys are
        # removed only after the profile JSON exists; subsequent CLI launches
        # receive credentials through the per-run --settings overlay instead.
        try:
            from priva_common.skill_exclude import get_user_yaml_key, save_user_yaml_key
            from priva_common.user_env import clear_settings_env
            from .services.llm_profiles import cleanup_stale_overlays, store
            vision = get_user_yaml_key("vision_model")
            store.read(vision if isinstance(vision, str) else None)
            if vision:
                save_user_yaml_key("vision_model", None)
            clear_settings_env()
            cleanup_stale_overlays()
        except Exception as exc:
            logger.warning("LLM profile migration/overlay cleanup skipped: {}", exc)

        # Seed runtime skills from the baked-in bundle.
        try:
            from .services.skill_hub import seed_bundled_skills
            seed_bundled_skills()
        except Exception as exc:
            logger.warning("Skill seeding skipped: {}", exc)

        # Bootstrap the per-account Python venv on /workspace (once; persists across
        # wakes). Lets the agent install packages that survive restarts, isolated from
        # this service's own interpreter. Fail-soft — never blocks the pod coming up.
        try:
            from .services.sandbox_venv import ensure_user_venv
            ensure_user_venv()
        except Exception as exc:
            logger.warning("sandbox venv bootstrap skipped: {}", exc)

        # D15: prune scheduler-origin session transcripts past retention.
        # Detached task — a large backlog must never delay pod readiness
        # (this boot IS the wake path a scheduled fire is waiting on).
        try:
            from .services.scheduled_runs.retention import prune_scheduler_transcripts
            asyncio.get_running_loop().create_task(prune_scheduler_transcripts())
        except Exception as exc:
            logger.warning("scheduler transcript prune skipped: {}", exc)

        import os
        logger.info(
            "agent-runner ready: account={}, user={}, workspace={}",
            os.environ.get("ACCOUNT_ID"),
            os.environ.get("USERNAME"),
            os.environ.get("WORKSPACE_DIR"),
        )
        from .services.claude_sdk.run_registry import run_registry
        from .services.claude_sdk.session_runtime_pool import session_runtime_pool
        await session_runtime_pool.startup()
        registry_sweeper_stop = asyncio.Event()

        async def _sweep_terminal_runs() -> None:
            ticks = 0
            while not registry_sweeper_stop.is_set():
                try:
                    await asyncio.wait_for(registry_sweeper_stop.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    # cgroup limits and live pressure are authoritative. This
                    # retires idle LRU entries before the kernel OOM killer has
                    # to choose a process, while active runs remain pinned.
                    try:
                        await session_runtime_pool.refresh_capacity()
                    except Exception:
                        logger.warning(
                            "session runtime memory refresh failed",
                            exc_info=True,
                        )
                    ticks += 1
                    if ticks % 12 == 0:
                        run_registry.sweep()

        registry_sweeper = asyncio.create_task(
            _sweep_terminal_runs(),
            name="run-registry-sweeper",
        )
        try:
            yield
        finally:
            # SIGTERM/Pod scale-down reaches FastAPI lifespan. Warm clients do
            # not block scale-to-zero, but every CLI child is explicitly reaped
            # before uvicorn exits instead of relying on loop cancellation.
            logger.info("agent-runner draining session runtime pool")
            run_tasks = run_registry.cancel_active()
            await session_runtime_pool.shutdown(grace_seconds=8.0)
            if run_tasks:
                _done, pending = await asyncio.wait(run_tasks, timeout=2.0)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*run_tasks, return_exceptions=True)
            run_registry.finish_cancelled()
            registry_sweeper_stop.set()
            await registry_sweeper
            logger.info("agent-runner shutdown complete")
            shutdown_logging()

    app = FastAPI(
        title="Priva agent-runner",
        version=settings.app_version,
        # The API reference UI is Scalar (not Swagger), served fully offline from a
        # vendored bundle — see the /sandbox/apidocs routes below. docs_url=None disables
        # FastAPI's built-in Swagger; openapi_url keeps the schema under /sandbox/apidocs so
        # the Scalar page (and the control-panel docs proxy) reach everything on one prefix.
        docs_url=None,
        redoc_url=None,
        openapi_url="/sandbox/apidocs/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(ActivityMiddleware)
    # Added last => outermost => runs before routing and before the multipart
    # parser spools anything to /tmp. Per-route read caps only bound memory.
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=MAX_REQUEST_BODY_BYTES)

    # --- Offline Scalar API reference (replaces Swagger UI) ---
    # Served from a vendored, self-contained bundle (no CDN). withDefaultFonts:false
    # disables Scalar's web-font fetch so the page renders with ZERO external requests
    # (system fonts). control-panel proxies /sandbox/apidocs* from a ready runner, so these
    # routes live on the pod alongside the OpenAPI schema (openapi_url, above).
    _scalar_js = Path(__file__).resolve().parent / "_static" / "scalar.standalone.js"
    _scalar_html = """<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Priva agent-runner — API reference</title>
  </head>
  <body>
    <script id="api-reference" data-url="/sandbox/apidocs/openapi.json"
            data-configuration='{"withDefaultFonts":false}'></script>
    <script src="/sandbox/apidocs/scalar.js"></script>
  </body>
</html>"""

    @app.get("/sandbox/apidocs", include_in_schema=False)
    async def scalar_docs():
        return HTMLResponse(_scalar_html)

    @app.get("/sandbox/apidocs/scalar.js", include_in_schema=False)
    async def scalar_bundle():
        return FileResponse(
            _scalar_js,
            media_type="application/javascript",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @app.get("/health", include_in_schema=False)
    async def health():
        import asyncio
        import os
        from .services.claude_sdk.session_runtime_pool import session_runtime_pool
        active, last = activity.snapshot()

        # Self-reported downstream connectivity for the admin System Map. Fail-soft
        # and off-loaded to a thread so a slow/unreachable data-spine never stalls
        # the k8s readiness probe. redis is planned (not wired today) → not probed.
        deps = []
        try:
            from priva_common.dataplane import get_client
            ok, detail = await asyncio.to_thread(lambda: get_client().admin.readyz())
            deps.append({"name": "data-spine", "ok": bool(ok), "detail": (detail or "")[:120]})
        except Exception as e:  # pragma: no cover - data-spine optional locally
            deps.append({"name": "data-spine", "ok": False, "detail": str(e)[:120]})

        # Volume usage (awake-only fallback for the admin dashboard; the wake-free
        # source is the quota-manager). On a quota'd mount statvfs reports the project
        # quota as the total — O(1), no tree walk. Fail-soft.
        volume = None
        try:
            st = os.statvfs("/workspace")
            volume = {
                "used_bytes": (st.f_blocks - st.f_bfree) * st.f_frsize,
                "total_bytes": st.f_blocks * st.f_frsize,
            }
        except Exception:
            pass

        return {
            "status": "ok",
            "service": "agent-runner",
            "account_id": os.environ.get("ACCOUNT_ID"),
            "active_runs": active,
            "last_activity_ts": last,
            "session_pool": session_runtime_pool.stats(),
            "deps": deps,
            "volume": volume,
            "time": datetime.now(timezone.utc).isoformat(),
        }

    from .deps import require_user

    @app.get("/api/sandbox/health", include_in_schema=False)
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
    from .routers.scheduled_runs import router as scheduled_runs_router
    from .routers.scheduler_jobs import router as scheduler_jobs_router
    from .routers.files import router as files_router
    from .routers.user_files import router as user_files_router
    from .routers.hooks import router as hooks_router
    from .routers.mcp import router as mcp_router
    from .routers.skills import router as skills_router
    from .routers.skill_hub import router as skill_hub_router
    from .routers.subagents import router as subagents_router
    from .routers.commands import router as commands_router
    from .routers.memory import router as memory_router
    from .routers.user_config import router as user_config_router
    from .routers.user_data import router as user_data_router
    from .routers.credentials import router as credentials_router

    for r in (
        agent_router, scheduled_runs_router, scheduler_jobs_router,
        files_router, user_files_router,
        hooks_router, mcp_router, skills_router, skill_hub_router, subagents_router,
        commands_router, memory_router,
        user_config_router, user_data_router, credentials_router,
    ):
        app.include_router(r)

    return app


app = create_app()
