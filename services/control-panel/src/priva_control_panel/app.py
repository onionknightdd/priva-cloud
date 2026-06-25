"""control-panel FastAPI app.

Single origin: serves its own control-plane routes (auth/admin/admin_files/
resource/metrics), owns the data-plane (``compose()``), serves the user SPA at ``/`` and
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


def _repo_root() -> Path | None:
    """Locate the monorepo root relative to this file (dev checkout only)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "web" / "user").exists() or (parent / "priva" / "web").exists():
            return parent
    return None


def _dist_dir(env_var: str, bundled_subdir: str, *repo_candidates: str) -> Path:
    """Resolve a built SPA dist dir.

    Order: explicit env override -> bundled package data (``_web/<subdir>``, present in
    the installed wheel) -> dev repo checkout (the first existing ``repo_candidates``).
    The package-data hop is what makes ``priva-cloud control-panel`` self-contained
    outside the repo.
    """
    raw = os.environ.get(env_var)
    if raw:
        return Path(raw).expanduser()

    bundled = Path(__file__).resolve().parent / "_web" / bundled_subdir
    if bundled.exists():
        return bundled

    root = _repo_root()
    if root is not None:
        for rel in repo_candidates:
            cand = root / rel
            if cand.exists():
                return cand

    # Nothing found; return the bundled path so the caller's existence check logs a
    # clear "dist not found" warning pointing at the package-data location.
    return bundled


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
        # Self-reported downstream connectivity (control-panel's own data-spine dep)
        # for the admin System Map — keeps the per-service /health contract uniform.
        # Fail-soft + off-loaded so a slow data-spine never stalls the probe.
        import asyncio

        deps = []
        try:
            from priva_common.dataplane import get_client
            ok, detail = await asyncio.to_thread(lambda: get_client().admin.readyz())
            deps.append({"name": "data-spine", "ok": bool(ok), "detail": (detail or "")[:120]})
        except Exception as e:  # pragma: no cover - data-spine optional locally
            deps.append({"name": "data-spine", "ok": False, "detail": str(e)[:120]})

        return {"status": "ok", "service": "control-panel", "deps": deps,
                "time": datetime.now(timezone.utc).isoformat()}

    # The admin SPA is mounted at "/admin" (StaticFiles serves only "/admin/..."),
    # so a bare "/admin" with no trailing slash 404s. Redirect it to "/admin/".
    @app.get("/admin", include_in_schema=False)
    async def _admin_index_redirect():
        return RedirectResponse(url="/admin/")

    # --- CP-served routers ---
    from .routers.auth import router as auth_router
    from .routers.admin import router as admin_router
    from .routers.admin_files import router as admin_files_router
    from .routers.resource import router as resource_router
    from .routers.metrics import router as metrics_router
    from .routers.console import router as console_router

    # Per-user agent-runtime state (usage overview/stats/analytics + agent audit)
    # is served by the agent-runner from its /workspace PVC, not here. The CP only
    # retains control-plane audit, exposed at GET /api/auth/audit (auth router).
    # console_router: admin web terminal INTO control-plane pods (k8s exec bridge).
    for r in (auth_router, admin_router, admin_files_router, resource_router, metrics_router, console_router):
        app.include_router(r)

    # Runtime routes (/api/agent, /api/files, /api/pty, ...) are NOT served or
    # proxied by CP anymore: agentgateway routes them to the per-account pod via
    # the InferencePool, steered by CP's ext_proc EPP (extproc.py). proxy.py is gone.

    # --- SPA static serving: admin at /admin first, then user catch-all at / ---
    admin_dist = _dist_dir("PRIVA_WEB_DIST_ADMIN", "dist-admin", "web/admin/dist", "priva/web/dist-admin")
    user_dist = _dist_dir("PRIVA_WEB_DIST", "dist", "web/user/dist", "priva/web/dist")
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
