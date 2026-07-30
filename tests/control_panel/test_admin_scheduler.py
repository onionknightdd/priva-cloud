"""Admin scheduler oversight endpoints (D12) against the real dataplane."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from priva_common.config import Settings
from priva_common.dataplane.grpc_client import _cache, build_grpc_client
from priva_common.models.auth import UserRecord
from priva_common.models.scheduler import CronTriggerConfig, ScheduledJobDefinition
from priva_data_spine.server import build_server
from priva_data_spine.service import build_repo
from priva_common.service_token import HEADER

ADMIN = UserRecord(username="root", password_hash="x", role="admin")


@pytest.fixture
def harness(tmp_path, monkeypatch, as_service_identity):
    s = Settings()
    s.dataspine.backend = "sqlite"
    s.dataspine.sqlite_path = str(tmp_path / "ds.db")
    repo = build_repo(s)
    server = build_server(s, repo=repo)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    s.dataspine.grpc_dsn = f"127.0.0.1:{port}"
    dataplane = build_grpc_client(s)

    monkeypatch.setenv("PRIVA_HOME", str(tmp_path / "home"))
    from priva_control_panel.routers import admin_scheduler as mod
    monkeypatch.setattr(mod, "get_client", lambda: dataplane)

    app = FastAPI()
    app.include_router(mod.router)
    app.dependency_overrides[mod.require_admin] = lambda: ADMIN

    account_id = dataplane.accounts.create("carol", "pw").account_id
    # Jobs are tenant-owned writes in production. Seed them through the same
    # account-scoped agent-runner identity, then restore the control-panel
    # identity used by the admin oversight endpoints below.
    as_service_identity("agent-runner", account_id=account_id)
    for i, status in enumerate(["active", "active", "paused"]):
        dataplane.scheduler.create_job(account_id, ScheduledJobDefinition(
            id=f"j{i}", name=f"job {i}", prompt="x", status=status,
            trigger=CronTriggerConfig(expr="0 9 * * *"), timezone="UTC"))
    as_service_identity("control-panel")

    with TestClient(app) as http:
        yield SimpleNamespace(http=http, dataplane=dataplane, account_id=account_id, mod=mod, monkeypatch=monkeypatch)

    server.stop(None)
    repo.close()
    _cache.clear()


def test_jobs_runs_and_pause_all(harness):
    jobs = harness.http.get(f"/api/admin/scheduler/accounts/{harness.account_id}/jobs").json()
    assert jobs["total"] == 3
    assert {j["status"] for j in jobs["jobs"]} == {"active", "paused"}

    runs = harness.http.get(f"/api/admin/scheduler/accounts/{harness.account_id}/runs").json()
    assert runs["runs"] == [] and runs["total"] == 0

    out = harness.http.post(f"/api/admin/scheduler/accounts/{harness.account_id}/pause-all").json()
    assert out == {"status": "ok", "paused": 2, "total": 3}
    assert all(j.status == "paused"
               for j in harness.dataplane.scheduler.list_jobs(harness.account_id))


def test_trigger_proxies_and_maps_errors(harness):
    class FakeAsyncClient:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **kw):
            # The scheduler's internal API rejects anonymous callers, so the
            # admin proxy must present this pod's control-plane identity.
            assert HEADER in (kw.get("headers") or {}), "trigger proxy sent no service token"
            code = 202 if "/j0" in url else 404
            return httpx.Response(code, json={})

    harness.monkeypatch.setattr(harness.mod.httpx, "AsyncClient", FakeAsyncClient)
    assert harness.http.post("/api/admin/scheduler/jobs/j0/trigger").status_code == 202
    assert harness.http.post("/api/admin/scheduler/jobs/ghost/trigger").status_code == 404
