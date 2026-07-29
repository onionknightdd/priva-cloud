"""Internal API (:8082, cluster-internal — design §6/§8).

``POST /internal/trigger/{job_id}`` is the run-now path: the runner's user API
proxies here; the synthetic fire goes through the same claim dance, so a
double-click (or two replicas' proxies) can't double-run. ``/healthz`` backs
the k8s probes; ``/metrics`` the ADR-0002 scrape.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Response

from priva_common.logging import get_app_logger
from priva_common.metrics import render
from priva_common.service_token import ServicePrincipal, verify_service

from .engine import SchedulerEngine

logger = get_app_logger(__name__)


def _principal(token: str | None) -> ServicePrincipal:
    """Authenticate an internal caller. Only workloads holding the control-plane
    signing key can mint one of these, so a role cannot be self-asserted."""
    if not token:
        raise HTTPException(401, "missing service token")
    try:
        return verify_service(token)
    except ValueError as exc:
        raise HTTPException(401, f"invalid service token: {exc}") from exc


def create_app(engine: SchedulerEngine) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # Fail loudly if this pod has no signing identity: the fallback is an
        # ephemeral in-process keypair, which makes every token this service
        # mints unverifiable by its peers while readiness stays green.
        from priva_common.service_identity import assert_configured
        assert_configured(signing=True)
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
    async def trigger(job_id: str, x_priva_service_token: str | None = Header(default=None)):
        # This used to take a bare job_id with no auth at all. Since the
        # scheduler resolves the job's owner itself, then mints THAT account's
        # runner token and dials THAT account's pod, an anonymous caller could
        # make any tenant's runner execute a prompt it had just written into the
        # victim's job — with the victim's credentials. Identify the caller
        # first, and pin a tenant caller to its own jobs.
        import asyncio

        principal = _principal(x_priva_service_token)
        exists = await asyncio.to_thread(engine._client.scheduler.get_job, job_id)
        if exists is None:
            raise HTTPException(404, "job not found")
        if not principal.is_control_plane:
            owned = await asyncio.to_thread(
                engine._client.scheduler.list_jobs, principal.account_id)
            if not any(j.id == job_id for j in owned):
                logger.warning("DENY trigger {} for {} (job not owned)", job_id, principal)
                # 404, not 403: a tenant must not be able to probe which job ids exist.
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
