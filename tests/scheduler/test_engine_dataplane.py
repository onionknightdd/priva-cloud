"""Engine ↔ real dataplane integration (gRPC, SQLite): the same client the
deployable composes, a scripted dispatcher in place of the pod. Locks the
engine to the real wire types (list_active_jobs tuples, run records, claims)
and exercises the internal API's lifespan (arm on start, trigger endpoint)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from priva_common.config import Settings
from priva_common.dataplane.grpc_client import _cache, build_grpc_client
from priva_common.models.scheduler import (
    AgentRunConfig,
    CronTriggerConfig,
    ScheduledJobDefinition,
)
from priva_data_spine.server import build_server
from priva_data_spine.service import build_repo

from priva_scheduler.api import create_app
from priva_scheduler.engine import SchedulerEngine

from .test_engine import ScriptedDispatcher
from priva_common.service_token import HEADER, mint as mint_service


@pytest.fixture
def dataplane(tmp_path):
    s = Settings()
    s.dataspine.backend = "sqlite"
    s.dataspine.sqlite_path = str(tmp_path / "ds.db")
    repo = build_repo(s)
    server = build_server(s, repo=repo)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    s.dataspine.grpc_dsn = f"127.0.0.1:{port}"
    try:
        yield build_grpc_client(s)
    finally:
        server.stop(None)
        repo.close()
        _cache.clear()


def _create_job(client, account_id, job_id="j1", status="active"):
    return client.scheduler.create_job(account_id, ScheduledJobDefinition(
        id=job_id, name="daily briefing", prompt="brief me",
        trigger=CronTriggerConfig(expr="0 9 * * 1-5"), timezone="UTC",
        status=status, job_config=AgentRunConfig(prompt="brief me"),
    ))


def test_full_fire_pipeline_over_grpc(dataplane, fast_settings):
    account_id = dataplane.accounts.create("carol", "pw").account_id
    _create_job(dataplane, account_id)

    dispatcher = ScriptedDispatcher("accepted")
    engine = SchedulerEngine(dataplane, dispatcher, replica_id="itest-a")

    assert asyncio.run(engine.sync_jobs())["total"] == 1

    assert asyncio.run(engine.fire(account_id, "j1")) == "dispatched"
    # birth record landed in the real store, RUNNING, owned by the account
    page = dataplane.scheduler.list_runs(account_id)
    (run,) = page.runs
    assert run.status == "running" and run.job_name == "daily briefing"
    assert dispatcher.calls[0][2].run_id == run.run_id

    # a second replica firing the same minute loses the claim — exactly-once
    other = SchedulerEngine(dataplane, ScriptedDispatcher("accepted"), replica_id="itest-b")
    assert asyncio.run(other.fire(account_id, "j1")) == "claim_lost"

    # the pod finishes the run (simulated) → next manual fire dispatches again
    run.status = "success"
    dataplane.scheduler.finish_run(run)
    assert asyncio.run(engine.fire(account_id, "j1", manual=True)) == "dispatched"


def test_internal_api_lifespan_and_trigger(dataplane, fast_settings):
    account_id = dataplane.accounts.create("dave", "pw").account_id
    _create_job(dataplane, account_id, job_id="j-paused", status="paused")

    engine = SchedulerEngine(dataplane, ScriptedDispatcher("accepted"), replica_id="itest-api")
    app = create_app(engine)

    with TestClient(app) as http:  # lifespan: engine.start() → arm from the DB
        health = http.get("/healthz").json()
        assert health["status"] == "ok" and health["armed_jobs"] == 0  # paused ≠ armed

        # run-now reaches a paused job via the account scan (round-3 one-shot);
        # the ack is fast — the pipeline (wake included) runs detached.
        cp = {HEADER: mint_service("control-panel")}
        resp = http.post("/internal/trigger/j-paused", headers=cp)
        assert resp.status_code == 202 and resp.json()["status"] == "accepted"

        assert http.post("/internal/trigger/ghost", headers=cp).status_code == 404

        # --- negative: the endpoint used to accept any anonymous in-cluster
        # caller, which let one tenant fire another tenant's job (and so run an
        # attacker-authored prompt with the victim's credentials).
        assert http.post("/internal/trigger/j-paused").status_code == 401
        assert http.post("/internal/trigger/j-paused",
                         headers={HEADER: "not-a-token"}).status_code == 401
        # A tenant token is pinned to its own jobs; a foreign job is 404 (not
        # 403 — a tenant must not learn which job ids exist).
        stranger = {HEADER: mint_service("agent-runner", account_id="acc-stranger")}
        assert http.post("/internal/trigger/j-paused", headers=stranger).status_code == 404
        owner = {HEADER: mint_service("agent-runner", account_id=account_id)}
        assert http.post("/internal/trigger/j-paused", headers=owner).status_code == 202
        assert http.get("/metrics").status_code == 200

        import time
        deadline = time.time() + 10
        while time.time() < deadline:
            page = dataplane.scheduler.list_runs(account_id)
            if page.runs:
                break
            time.sleep(0.05)

    page = dataplane.scheduler.list_runs(account_id)
    assert [r.status for r in page.runs] == ["running"]  # birth written; outcome is the pod's
