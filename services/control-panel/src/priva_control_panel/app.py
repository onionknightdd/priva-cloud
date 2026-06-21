"""control-panel FastAPI app.

Single origin: serves its own routes (auth/admin/admin_files/user_data/resource/
metrics), owns the data-plane (``compose()``), serves the user SPA at ``/`` and
the admin SPA at ``/admin``, and mounts the reverse-proxy router (``proxy.py``)
that forwards the runtime to agent-runner. No CORS (same-origin).
"""

from __future__ import annotations

import mimetypes
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

# Minimal containers may not register web font types; browsers refuse fonts
# served as application/octet-stream.
for _ext, _type in ((".woff2", "font/woff2"), (".woff", "font/woff"), (".ttf", "font/ttf"), (".otf", "font/otf")):
    mimetypes.add_type(_type, _ext)

from priva_common.config import get_settings
from priva_common.logging import AccessLogMiddleware, configure_logging, get_app_logger, shutdown_logging

logger = get_app_logger(__name__)


def _repo_web() -> Path:
    """Locate priva/web relative to this file (…/services/control-panel/src/…)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "priva" / "web"
        if cand.exists():
            return cand
    return Path.cwd() / "priva" / "web"


def _dist_dir(env_var: str, subdir: str) -> Path:
    raw = os.environ.get(env_var)
    if raw:
        return Path(raw).expanduser()
    return _repo_web() / subdir


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        configure_logging(settings)

        # gRPC transport: CP is a data-plane *client* (data-spine runs as its own
        # pod). Only compose in-process when explicitly configured for it.
        if settings.dataspine.transport == "in_process":
            from priva_data_spine import compose
            compose()
        logger.info(
            "data-plane transport={}, backend={}, dsn={}",
            settings.dataspine.transport,
            settings.dataspine.backend,
            settings.dataspine.grpc_dsn,
        )

        # Start the ext_proc EPP server (the routing brain agentgateway calls).
        from .extproc import start_extproc_server
        extproc_server = await start_extproc_server(settings)

        from priva_common.user_store import get_user_store
        try:
            users = get_user_store().list_users()
            logger.info("control-panel ready: users={}, extproc={}", len(users), settings.edge.extproc_port)
        except Exception as exc:
            logger.warning("user listing failed at boot: {}", exc)

        try:
            yield
        finally:
            try:
                extproc_server.close()
                await extproc_server.wait_closed()
            except Exception:
                pass
            logger.info("control-panel shutdown complete")
            shutdown_logging()

    app = FastAPI(
        title="Priva control-panel",
        version=settings.app_version,
        docs_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(AccessLogMiddleware)

    @app.get("/health", include_in_schema=False)
    async def health():
        return {"status": "ok", "service": "control-panel", "time": datetime.now(timezone.utc).isoformat()}

    # The admin SPA is mounted at "/admin" (StaticFiles serves only "/admin/..."),
    # so a bare "/admin" with no trailing slash 404s. Redirect it to "/admin/".
    @app.get("/admin", include_in_schema=False)
    async def _admin_index_redirect():
        return RedirectResponse(url="/admin/")

    # --- CP-served routers ---
    from .routers.auth import router as auth_router
    from .routers.admin import router as admin_router
    from .routers.admin_files import router as admin_files_router
    from .routers.user_data import router as user_data_router
    from .routers.resource import router as resource_router
    from .routers.metrics import router as metrics_router

    for r in (auth_router, admin_router, admin_files_router, user_data_router, resource_router, metrics_router):
        app.include_router(r)

    # Runtime routes (/api/agent, /api/files, /api/pty, ...) are NOT served or
    # proxied by CP anymore: agentgateway routes them to the per-account pod via
    # the InferencePool, steered by CP's ext_proc EPP (extproc.py). proxy.py is gone.

    # --- SPA static serving: admin at /admin first, then user catch-all at / ---
    admin_dist = _dist_dir("PRIVA_WEB_DIST_ADMIN", "dist-admin")
    user_dist = _dist_dir("PRIVA_WEB_DIST", "dist")
    if admin_dist.exists():
        app.mount("/admin", StaticFiles(directory=admin_dist, html=True), name="admin-spa")
        logger.info("admin SPA mounted at /admin from {}", admin_dist)
    else:
        logger.warning("admin SPA dist not found at {} (run `npm run build:admin`)", admin_dist)
    if user_dist.exists():
        app.mount("/", StaticFiles(directory=user_dist, html=True), name="user-spa")
        logger.info("user SPA mounted at / from {}", user_dist)
    else:
        logger.warning("user SPA dist not found at {} (run `npm run build`)", user_dist)

    return app


app = create_app()
