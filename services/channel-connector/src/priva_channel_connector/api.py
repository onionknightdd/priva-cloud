"""Internal API (:8083, cluster-internal).

``POST /internal/reconcile/{account_id}`` is the low-latency push: the control-panel
calls it right after a config edit so a change lands in <1s instead of waiting for the
≤poll_seconds loop. Auth reuses the runner-token seam — the control-panel mints
``mint(account_id, username)`` and we verify it AND that the claim's account_id matches
the path, so one tenant can't nudge another. ``/healthz`` backs the k8s probes;
``/metrics`` the scrape.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Response

from priva_common.logging import get_app_logger
from priva_common.metrics import render
from priva_common.runner_token import verify

from .engine import ReconcileEngine

logger = get_app_logger(__name__)


def create_app(engine: ReconcileEngine) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await engine.start()
        try:
            yield
        finally:
            await engine.stop()

    app = FastAPI(
        title="Priva channel-connector", docs_url=None, redoc_url=None, openapi_url=None,
        lifespan=lifespan,
    )

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "service": "channel-connector", "armed_apps": engine.armed_count}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        body, content_type = render()
        return Response(content=body, media_type=content_type)

    @app.post("/internal/reconcile/{account_id}", status_code=202)
    async def reconcile(account_id: str, x_priva_runner_token: str | None = Header(default=None)):
        if not x_priva_runner_token:
            raise HTTPException(401, "missing runner token")
        try:
            claims = verify(x_priva_runner_token)
        except ValueError as exc:
            raise HTTPException(401, f"invalid runner token: {exc}") from exc
        if claims.get("account_id") != account_id:
            raise HTTPException(403, "runner token account mismatch")
        # Detach so the caller (control-panel edit path) gets a fast ack; the converge
        # itself is idempotent with the poll loop.
        task = asyncio.create_task(engine.reconcile_now(account_id), name=f"reconcile-{account_id}")
        task.add_done_callback(_log_reconcile_outcome)
        return {"status": "accepted", "account_id": account_id}

    return app


def _log_reconcile_outcome(task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error("push reconcile failed: {}", exc)
