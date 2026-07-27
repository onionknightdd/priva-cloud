"""control-panel FastAPI app.

Single origin: serves its own control-plane routes (auth/admin/admin_files/
resource/metrics), owns the data-plane (``compose()``), serves the user SPA at ``/`` and
``/sandbox`` and the admin SPA at ``/admin``, and runs the ext_proc EndpointPicker (``extproc.py``) that
agentgateway consults to steer runtime requests to the per-account agent-runner pod.
Runtime traffic does not pass through this app. No CORS (same-origin).
"""

from __future__ import annotations

import asyncio
import mimetypes
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# Minimal containers may not register web font types; browsers refuse fonts
# served as application/octet-stream.
for _ext, _type in ((".woff2", "font/woff2"), (".woff", "font/woff"), (".ttf", "font/ttf"), (".otf", "font/otf")):
    mimetypes.add_type(_type, _ext)

from priva_common.config import get_settings
from priva_common.logging import AccessLogMiddleware, configure_logging, get_app_logger, shutdown_logging

logger = get_app_logger(__name__)


SPA_SHELL_CACHE_CONTROL = "no-cache"
SPA_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"
TENANT_SYNC_INTERVAL_SECONDS = 60.0


async def _tenant_sync_loop() -> None:
    """Idempotent account -> AgentTenant repair backstop.

    A user can delete/re-apply a CR outside Control Panel. Re-reading the authoritative
    account/default records keeps identity and desired runtime snapshots complete without
    generating writes when nothing changed.
    """
    from .provisioner import sync_all_tenants

    while True:
        try:
            await asyncio.to_thread(sync_all_tenants)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("AgentTenant account sync failed: {}", exc)
        await asyncio.sleep(TENANT_SYNC_INTERVAL_SECONDS)


class SpaStaticFiles(StaticFiles):
    """Serve Vite SPA files with deployment-safe cache headers."""

    def file_response(self, full_path, stat_result, scope, status_code=200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = self._cache_control_for(full_path)
        return response

    @staticmethod
    def _cache_control_for(full_path) -> str:
        path = Path(full_path)
        if "assets" in path.parts:
            return SPA_ASSET_CACHE_CONTROL
        return SPA_SHELL_CACHE_CONTROL


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
        tenant_sync_task = asyncio.create_task(
            _tenant_sync_loop(), name="agenttenant-account-sync")

        from priva_common.user_store import get_user_store
        try:
            users = get_user_store().list_users()
            logger.info("control-panel ready: users={}, extproc={}", len(users), settings.edge.extproc_port)
        except Exception as exc:
            logger.warning("user listing failed at boot: {}", exc)

        try:
            yield
        finally:
            tenant_sync_task.cancel()
            try:
                await tenant_sync_task
            except asyncio.CancelledError:
                pass
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

    # Same story for the user SPA's /sandbox alias (it is also served at "/").
    @app.get("/sandbox", include_in_schema=False)
    async def _sandbox_index_redirect():
        return RedirectResponse(url="/sandbox/")

    # The agent-runner's OpenAPI docs (/sandbox/apidocs + the schema under it) are served
    # HERE, not via the InferencePool: the GIE/EPP response path buffers bodies to ~8KB,
    # which truncates the ~91KB schema. The schema is account-independent, so we proxy it
    # from ANY ready runner (full body, like the SPA bundles this app already serves). The
    # SPA's "API Doc" link opens /sandbox/apidocs in a new tab — a tokenless top-level nav,
    # so this is unauthenticated; it exposes only the API shape, never user data. Must
    # register BEFORE the "/sandbox" static mount so the SPA mount doesn't shadow it.
    @app.get("/sandbox/apidocs", include_in_schema=False)
    @app.get("/sandbox/apidocs/{sub:path}", include_in_schema=False)
    async def _sandbox_docs_proxy(sub: str = ""):
        from . import provisioner
        endpoint = await asyncio.to_thread(provisioner.any_ready_runner_endpoint)
        if not endpoint:
            return Response("agent sandbox is waking, retry in a moment", status_code=503)
        url = f"http://{endpoint}/sandbox/apidocs" + (f"/{sub}" if sub else "")
        try:
            # trust_env=False: in-cluster pod-to-pod hop must not honor any host/system proxy.
            async with httpx.AsyncClient(trust_env=False, timeout=15.0) as cx:
                r = await cx.get(url)
        except Exception as exc:
            return Response(f"docs upstream unavailable: {exc}", status_code=502)
        # Forward caching headers so the browser caches the ~3.7MB Scalar bundle instead
        # of re-fetching it (and this app re-proxying it) on every docs open.
        passthru = {k: v for k, v in r.headers.items()
                    if k.lower() in ("cache-control", "etag", "last-modified")}
        return Response(content=r.content, status_code=r.status_code, headers=passthru,
                        media_type=r.headers.get("content-type"))

    # Large sandbox reads and multipart uploads are fetched HERE, not via the InferencePool, for the
    # same reason as /sandbox/apidocs: the GIE/EPP response path buffers bodies
    # to ~8KB (agentgateway hardcodes the EPP ext_proc to FullDuplexStreamed —
    # not a tunable buffer, no upstream fix), which truncates any response larger
    # than that (workflow session transcripts run 35-300KB, file previews up to
    # 1MB, big skill/log/listing bodies) and breaks JSON.parse on the client
    # ("Unterminated string"). This shared helper rides the "/" catch-all
    # (control-panel face, no ext_proc), so it returns the full body. It re-does
    # the EPP's per-account steering: auth the user's bearer token, wake their
    # pod, mint a per-account runner token, and proxy the allowed method — so a caller
    # reaches only their OWN pod. SSE callers (Accept: text/event-stream) branch to
    # _stream_runner_response below and are relayed chunk by chunk instead of buffered.
    async def _proxy_runner_request(request: Request, sandbox_path: str, method: str = "GET") -> Response:
        from . import provisioner
        from .services.auth import authenticate_raw_token
        from priva_common.runner_token import mint

        auth = request.headers.get("authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else None
        try:
            user = await authenticate_raw_token(token, request.headers.get("x-user-name"))
        except HTTPException as exc:
            if exc.status_code == 403:  # lifecycle gate fired: revoked, not unauthenticated
                return Response("account access revoked", status_code=403)
            user = None
        except Exception:
            user = None
        if user is None or not getattr(user, "account_id", None):
            return Response("Authentication required", status_code=401)
        if getattr(user, "status", "active") != "active":
            return Response("account access revoked", status_code=403)

        try:
            endpoint = await provisioner.wake_and_wait(user.account_id)
        except Exception as exc:
            return Response(f"agent sandbox unavailable: {exc}", status_code=503)
        if not endpoint:
            return Response("agent sandbox is waking, retry in a moment", status_code=503)

        qs = request.url.query
        url = f"http://{endpoint}/api/sandbox/{sandbox_path}" + (f"?{qs}" if qs else "")
        headers = {"X-Priva-Runner-Token": mint(user.account_id, user.username)}
        content_type = request.headers.get("content-type")
        if content_type:
            headers["Content-Type"] = content_type
        accept = request.headers.get("accept")
        if accept:
            headers["Accept"] = accept
        body = await request.body() if method != "GET" else None

        # SSE must not be buffered: the caller needs events as they happen, and an
        # agent run outlives any sane total timeout. Hand it to the streaming path.
        if "text/event-stream" in (accept or "").lower():
            return await _stream_runner_response(url, headers, body, method)

        try:
            # trust_env=False: in-cluster pod-to-pod hop must not honor any host/system proxy.
            # httpx buffers r.content fully — fine for file-manager previews/uploads.
            async with httpx.AsyncClient(trust_env=False, timeout=30.0) as cx:
                r = await cx.request(method, url, headers=headers, content=body)
        except Exception as exc:
            return Response(f"runner upstream unavailable: {exc}", status_code=502)
        return Response(content=r.content, status_code=r.status_code,
                        media_type=r.headers.get("content-type", "application/json"))

    async def _stream_runner_response(url: str, headers: dict, body, method: str) -> Response:
        """Relay an SSE response from the account's pod, chunk by chunk.

        SSE on the /api/sandbox lane is truncated like any other body: agentgateway
        hardcodes the GIE EPP ext_proc to FullDuplexStreamed and ignores our
        mode_override, so a run's event stream is cut at ~8KB and the client never
        sees `result` (ADR 0003 exempted "streams/SSE/WS", but that reasoning only
        holds for WS — after the upgrade those bytes tunnel past ext_proc, while SSE
        is an ordinary response body). This lane rides the "/" catch-all, which has
        no ext_proc at all.

        read=None because the gap between two events is the model thinking, not a
        stalled socket; the run's own limits bound it. The client going away
        cancels this generator, which closes the upstream connection and lets the
        runner tear the run down.
        """
        timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)
        cx = httpx.AsyncClient(trust_env=False, timeout=timeout)
        try:
            req = cx.build_request(method, url, headers=headers, content=body)
            r = await cx.send(req, stream=True)
        except Exception as exc:
            await cx.aclose()
            return Response(f"runner upstream unavailable: {exc}", status_code=502)

        # An error reply is a small one-shot body, not a stream — read it whole so
        # the caller gets the message instead of an empty stream.
        if r.status_code >= 400:
            try:
                detail = await r.aread()
            finally:
                await r.aclose()
                await cx.aclose()
            return Response(content=detail, status_code=r.status_code,
                            media_type=r.headers.get("content-type", "application/json"))

        async def relay():
            try:
                async for chunk in r.aiter_raw():
                    yield chunk
            finally:
                await r.aclose()
                await cx.aclose()

        return StreamingResponse(
            relay(),
            status_code=r.status_code,
            media_type=r.headers.get("content-type", "text/event-stream"),
            # No intermediary may buffer or cache a live event stream.
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Generic large-body-safe read lane: any GET /api/cp-proxy/<path> is proxied to
    # the caller's pod at /api/sandbox/<path>. Not under /api/sandbox, so Gateway-API
    # most-specific-prefix routing sends it to the "/" catch-all (control-panel)
    # automatically — no deploy/gateway change needed. The SPA points its >8KB-risk
    # reads here (web/shared/api/client.js sandboxRead).
    @app.get("/api/cp-proxy/{sandbox_path:path}", include_in_schema=False)
    async def _cp_proxy_get(sandbox_path: str, request: Request):
        if ".." in sandbox_path:  # defense-in-depth: no path escape above /api/sandbox/
            return Response("invalid path", status_code=400)
        return await _proxy_runner_request(request, sandbox_path)

    @app.post("/api/cp-proxy/{sandbox_path:path}", include_in_schema=False)
    async def _cp_proxy_post(sandbox_path: str, request: Request):
        if ".." in sandbox_path:
            return Response("invalid path", status_code=400)
        return await _proxy_runner_request(request, sandbox_path, method="POST")

    # Back-compat alias for the original session-transcript proxy, now sharing the
    # helper above (the SPA's primary path is the generic /api/cp-proxy lane).
    @app.get("/api/session-history/{session_id}/messages", include_in_schema=False)
    async def _session_messages_proxy(session_id: str, request: Request):
        return await _proxy_runner_request(request, f"agent/sessions/{session_id}/messages")

    # --- CP-served routers ---
    from .routers.auth import router as auth_router
    from .routers.admin import router as admin_router
    from .routers.admin_files import router as admin_files_router
    from .routers.admin_scheduler import router as admin_scheduler_router
    from .routers.hook_policy import router as hook_policy_router
    from .routers.metrics import router as metrics_router
    from .routers.console import router as console_router
    from .routers.terminal import router as terminal_router

    # Per-user agent-runtime state (usage overview/stats/analytics + agent audit)
    # is served by the agent-runner from its /workspace PVC, not here. The CP only
    # retains control-plane audit, exposed at GET /api/auth/audit (auth router).
    # console_router: admin web terminal INTO control-plane pods (k8s exec bridge).
    # (The old /api/resource/models proxy is gone — the model-list connection test
    # is served pod-side at /api/sandbox/credentials/models, alongside the creds.)
    for r in (auth_router, admin_router, admin_files_router, admin_scheduler_router,
              hook_policy_router, metrics_router, console_router, terminal_router):
        app.include_router(r)

    # Runtime routes (/api/sandbox/* and /api/terminal/ws) are NOT served by CP:
    # agentgateway routes them to the per-account pod via the InferencePool, steered
    # by CP's ext_proc EPP (extproc.py).

    # --- SPA static serving: admin at /admin, the user SPA at /sandbox AND the "/"
    # catch-all. Most-specific mounts must register first; "/" stays last. The same
    # build is served at both user paths — index.html uses absolute /assets/... refs
    # (vite base "/"), which the "/" mount serves, so both /sandbox/ and / work. ---
    admin_dist = _dist_dir("PRIVA_WEB_DIST_ADMIN", "dist-admin", "web/admin/dist")
    user_dist = _dist_dir("PRIVA_WEB_DIST", "dist", "web/user/dist")
    if admin_dist.exists():
        app.mount("/admin", SpaStaticFiles(directory=admin_dist, html=True), name="admin-spa")
        logger.info("admin SPA mounted at /admin from {}", admin_dist)
    else:
        logger.warning("admin SPA dist not found at {} (run `npm run build:admin`)", admin_dist)
    if user_dist.exists():
        app.mount("/sandbox", SpaStaticFiles(directory=user_dist, html=True), name="user-spa-sandbox")
        app.mount("/", SpaStaticFiles(directory=user_dist, html=True), name="user-spa")
        logger.info("user SPA mounted at /sandbox and / from {}", user_dist)
    else:
        logger.warning("user SPA dist not found at {} (run `npm run build`)", user_dist)

    return app


app = create_app()
