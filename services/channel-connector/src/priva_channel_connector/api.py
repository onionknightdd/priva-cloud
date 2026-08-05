"""Internal API (:8083, cluster-internal).

``POST /internal/reconcile/{account_id}`` is the low-latency config push.
``POST /internal/scheduler-callback/{account_id}`` accepts a terminal scheduled-run
outcome from that account's agent-runner and proactively sends a card to the account's
bound Feishu owner. ``/healthz`` backs the k8s probes; ``/metrics`` the scrape.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Response

from priva_common.body_limit import MaxBodySizeMiddleware
from priva_common.logging import get_app_logger
from priva_common.metrics import render
from priva_common.runner_token import verify
from priva_common.scheduler_callback_token import verify as verify_callback_token
from priva_common.service_token import verify_service

from .engine import (
    ReconcileEngine,
    SchedulerCallbackDeliveryFailed,
    SchedulerCallbackOwnerUnbound,
    SchedulerCallbackRateLimited,
    SchedulerCallbackRejected,
    SchedulerCallbackUnavailable,
    SchedulerCallbackWorkerUnavailable,
)
from .scheduler_callback import SchedulerCallbackPayload, render_scheduler_callback_card

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
    # Typed callback payloads are only tens of KiB. Keep a hard streaming
    # ceiling ahead of JSON/Pydantic parsing so a compromised tenant-scoped
    # runner token cannot turn this shared service into an upload sink.
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=1024 * 1024)

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

    @app.post("/internal/scheduler-callback/{account_id}")
    async def scheduler_callback(
        account_id: str,
        payload: SchedulerCallbackPayload,
        x_priva_service_token: str | None = Header(default=None),
        x_priva_scheduler_callback_token: str | None = Header(default=None),
    ):
        """Deliver one terminal scheduler outcome through the account's own bot."""
        if not x_priva_service_token:
            raise HTTPException(401, "missing service token")
        try:
            principal = verify_service(x_priva_service_token)
        except ValueError as exc:
            raise HTTPException(401, f"invalid service token: {exc}") from exc
        if principal.svc != "agent-runner":
            raise HTTPException(403, "agent-runner service identity required")
        if principal.account_id != account_id:
            raise HTTPException(403, "service token account mismatch")
        if not x_priva_scheduler_callback_token:
            raise HTTPException(401, "missing scheduler callback token")
        try:
            callback_claims = verify_callback_token(x_priva_scheduler_callback_token)
        except ValueError as exc:
            raise HTTPException(401, f"invalid scheduler callback token: {exc}") from exc
        expected_claims = {
            "account_id": account_id,
            "run_id": payload.run_id,
            "job_id": payload.job_id,
        }
        if any(callback_claims.get(key) != value for key, value in expected_claims.items()):
            raise HTTPException(403, "scheduler callback token scope mismatch")

        card = render_scheduler_callback_card(payload)
        try:
            message_id = await engine.push_scheduler_callback(account_id, payload, card)
        except SchedulerCallbackRateLimited as exc:
            raise HTTPException(429, str(exc)) from exc
        except SchedulerCallbackRejected as exc:
            raise HTTPException(409, str(exc)) from exc
        except SchedulerCallbackUnavailable as exc:
            raise HTTPException(409, str(exc)) from exc
        except SchedulerCallbackOwnerUnbound as exc:
            raise HTTPException(409, str(exc)) from exc
        except SchedulerCallbackWorkerUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except SchedulerCallbackDeliveryFailed as exc:
            raise HTTPException(502, str(exc)) from exc
        return {
            "status": "delivered",
            "account_id": account_id,
            "run_id": payload.run_id,
            "message_id": message_id,
        }

    return app


def _log_reconcile_outcome(task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error("push reconcile failed: {}", exc)
