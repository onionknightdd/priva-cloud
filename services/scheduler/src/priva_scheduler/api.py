"""Internal API (:8082, cluster-internal — design §6/§8).

``POST /internal/trigger/{job_id}`` is the run-now path: the runner's user API
proxies here; the synthetic fire goes through the same claim dance, so a
double-click (or two replicas' proxies) can't double-run. ``/healthz`` backs
the k8s probes; ``/metrics`` the ADR-0002 scrape.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response

from priva_common.logging import get_app_logger
from priva_common.metrics import render

from .engine import SchedulerEngine

logger = get_app_logger(__name__)


def create_app(engine: SchedulerEngine) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await engine.start()
        try:
            yield
        finally:
            await engine.stop()

    app = FastAPI(
        title="Priva scheduler", docs_url=None, redoc_url=None, openapi_url=None,
        lifespan=lifespan,
    )

    @app.get("/healthz")
    async def healthz():
        return {
            "status": "ok",
            "service": "scheduler",
            "replica": engine.replica_id,
            "armed_jobs": engine.armed_count,
        }

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        body, content_type = render()
        return Response(content=body, media_type=content_type)

    @app.post("/internal/trigger/{job_id}", status_code=202)
    async def trigger(job_id: str):
        # The pipeline includes the wake (potentially ~a minute) — detach it
        # so the run-now caller gets a fast ack; StartRun makes the run show
        # RUNNING in history as soon as the claim wins.
        import asyncio

        exists = await asyncio.to_thread(engine._client.scheduler.get_job, job_id)
        if exists is None:
            raise HTTPException(404, "job not found")
        task = asyncio.create_task(engine.trigger_now(job_id), name=f"trigger-{job_id}")
        task.add_done_callback(_log_trigger_outcome)
        return {"status": "accepted", "job_id": job_id}

    return app


def _log_trigger_outcome(task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error("manual trigger failed: {}", exc)
    else:
        logger.info("manual trigger outcome: {}", task.result())
