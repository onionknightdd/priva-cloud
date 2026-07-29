"""Runner user scheduler API (/api/sandbox/scheduler/*) + the 7 ported MCP
tools, both against the real dataplane over gRPC (Phase 4a, step 4).

The MCP tools are exercised by calling each SdkMcpTool's handler directly —
the CLI transport adds nothing to what needs locking here (routing text is
carried verbatim from the monolith; behaviour is the dataplane re-point)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from priva_common.config import Settings
from priva_common.dataplane.grpc_client import _cache, build_grpc_client
from priva_common.models.auth import UserRecord
from priva_common.models.scheduler import JobRunRecord
from priva_data_spine.server import build_server
from priva_data_spine.service import build_repo
from priva_common.service_token import HEADER


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


@pytest.fixture
def harness(dataplane, tmp_path, monkeypatch):
    from priva_agent_runner.routers import scheduler_jobs as router_mod

    monkeypatch.setenv("PRIVA_HOME", str(tmp_path / "home"))
    account_id = dataplane.accounts.create("carol", "pw").account_id
    user = UserRecord(username="carol", password_hash="x", account_id=account_id)

    monkeypatch.setattr(router_mod, "get_client", lambda: dataplane)

    app = FastAPI()
    app.include_router(router_mod.router)
    app.dependency_overrides[router_mod.require_account] = lambda: user

    with TestClient(app) as http:
        yield SimpleNamespace(
            http=http, dataplane=dataplane, account_id=account_id,
            user=user, router_mod=router_mod, monkeypatch=monkeypatch,
        )


def _cron_job(name="Daily briefing", expr="0 9 * * 1-5"):
    return {
        "name": name, "timezone": "Asia/Shanghai",
        "trigger": {"type": "cron", "expr": expr},
        "job_config": {"job_type": "agent_run", "prompt": "brief me"},
    }


# --- CRUD + lifecycle ---------------------------------------------------------


def test_job_crud_lifecycle(harness):
    http = harness.http

    created = http.post("/api/sandbox/scheduler/jobs", json=_cron_job())
    assert created.status_code == 200, created.text
    job = created.json()
    assert job["username"] == "carol" and job["status"] == "active"
    assert job["next_run_time"]  # server-computed from the same trigger math
    assert job["job_config"]["timeout_seconds"] == 1800  # D14 default rides along
    job_id = job["id"]

    listed = http.get("/api/sandbox/scheduler/jobs").json()
    assert listed["total"] == 1 and listed["jobs"][0]["id"] == job_id

    updated = http.put(f"/api/sandbox/scheduler/jobs/{job_id}", json={
        "name": "Morning brief", "trigger": {"type": "interval", "hours": 6}})
    assert updated.status_code == 200
    assert updated.json()["name"] == "Morning brief"
    assert updated.json()["trigger"]["hours"] == 6

    paused = http.post(f"/api/sandbox/scheduler/jobs/{job_id}/pause").json()
    assert paused["status"] == "paused" and paused["next_run_time"] is None  # round-3
    resumed = http.post(f"/api/sandbox/scheduler/jobs/{job_id}/resume").json()
    assert resumed["status"] == "active" and resumed["next_run_time"]

    assert http.delete(f"/api/sandbox/scheduler/jobs/{job_id}").json() == {"status": "ok"}
    assert http.get("/api/sandbox/scheduler/jobs").json()["total"] == 0
    assert http.delete(f"/api/sandbox/scheduler/jobs/{job_id}").status_code == 404


def test_create_rejects_invalid_cron(harness):
    bad = _cron_job(expr="not a cron")
    resp = harness.http.post("/api/sandbox/scheduler/jobs", json=bad)
    assert resp.status_code == 400 and "invalid trigger" in resp.json()["detail"]


def test_ownership_fence_hides_foreign_jobs(harness):
    other = harness.dataplane.accounts.create("mallory", "pw").account_id
    from priva_common.models.scheduler import CronTriggerConfig, ScheduledJobDefinition
    foreign = harness.dataplane.scheduler.create_job(other, ScheduledJobDefinition(
        id="foreign1", name="not yours", prompt="x",
        trigger=CronTriggerConfig(expr="0 9 * * *"), timezone="UTC"))

    assert harness.http.get("/api/sandbox/scheduler/jobs").json()["total"] == 0
    for method, path in (
        ("delete", f"/api/sandbox/scheduler/jobs/{foreign.id}"),
        ("post", f"/api/sandbox/scheduler/jobs/{foreign.id}/pause"),
        ("post", f"/api/sandbox/scheduler/jobs/{foreign.id}/trigger"),
    ):
        assert getattr(harness.http, method)(path).status_code == 404
    # …and it's still alive under its own account
    assert harness.dataplane.scheduler.get_job(foreign.id) is not None


def test_interval_next_run_anchored_to_created_at(harness):
    """next_run_time for interval jobs must not slide with the poll clock —
    it is created_at + k*interval, the instant the engine actually fires."""
    from datetime import datetime, timedelta

    http = harness.http
    job = http.post("/api/sandbox/scheduler/jobs", json={
        "name": "poller", "timezone": "UTC",
        "trigger": {"type": "interval", "hours": 4},
        "job_config": {"job_type": "agent_run", "prompt": "poll"},
    }).json()
    assert job["next_run_time"]

    listed = http.get("/api/sandbox/scheduler/jobs").json()["jobs"][0]
    assert listed["next_run_time"] == job["next_run_time"]  # stable across polls

    parse = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))  # noqa: E731
    assert parse(job["next_run_time"]) - parse(job["created_at"]) == timedelta(hours=4)


def test_validate_trigger_preview(harness):
    ok = harness.http.post("/api/sandbox/scheduler/validate-trigger", json={
        "trigger": {"type": "cron", "expr": "0 9 * * 1-5"}, "timezone": "Asia/Shanghai"})
    assert ok.json()["valid"] is True and ok.json()["next_run_time"]

    bad = harness.http.post("/api/sandbox/scheduler/validate-trigger", json={
        "trigger": {"type": "cron", "expr": "99 99 * * *"}, "timezone": "UTC"})
    assert bad.json()["valid"] is False and bad.json()["error"]

    zero = harness.http.post("/api/sandbox/scheduler/validate-trigger", json={
        "trigger": {"type": "interval"}, "timezone": "UTC"})
    assert zero.json()["valid"] is False  # zero-length interval never fires

    # editing an existing job sends its created_at → preview keeps the armed
    # phase (ticks at 10:07 + k*4h → minute stays :07 whatever the clock)
    anchored = harness.http.post("/api/sandbox/scheduler/validate-trigger", json={
        "trigger": {"type": "interval", "hours": 4}, "timezone": "UTC",
        "created_at": "2026-07-01T10:07:00Z"})
    assert anchored.json()["valid"] is True
    assert anchored.json()["next_run_time"][14:16] == "07"


def test_trigger_proxies_scheduler_internal_api(harness):
    job_id = harness.http.post(
        "/api/sandbox/scheduler/jobs", json=_cron_job()).json()["id"]

    posted: list[str] = []

    async def fake_post(jid: str) -> httpx.Response:
        posted.append(jid)
        return httpx.Response(202, json={"status": "accepted"})

    harness.monkeypatch.setattr(harness.router_mod, "_post_trigger", fake_post)
    resp = harness.http.post(f"/api/sandbox/scheduler/jobs/{job_id}/trigger")
    assert resp.status_code == 202 and posted == [job_id]

    async def down(jid: str) -> httpx.Response:
        raise httpx.ConnectError("no scheduler")

    harness.monkeypatch.setattr(harness.router_mod, "_post_trigger", down)
    assert harness.http.post(
        f"/api/sandbox/scheduler/jobs/{job_id}/trigger").status_code == 502


def test_runs_listing_with_filters(harness):
    job_id = harness.http.post(
        "/api/sandbox/scheduler/jobs", json=_cron_job()).json()["id"]
    for i, status in enumerate(["success", "error", "skipped"]):
        harness.dataplane.scheduler.record_run(harness.account_id, JobRunRecord(
            run_id=f"r{i}", job_id=job_id, job_name="Daily briefing", username="carol",
            started_at=f"2026-07-1{i}T09:00:00.000Z", status=status,
            error_message="boom" if status == "error" else None))

    page = harness.http.get("/api/sandbox/scheduler/runs").json()
    assert [r["run_id"] for r in page["runs"]] == ["r2", "r1", "r0"]
    assert page["total"] == 3

    errors = harness.http.get("/api/sandbox/scheduler/runs?status=error").json()
    assert [r["run_id"] for r in errors["runs"]] == ["r1"]
    assert errors["runs"][0]["error_message"] == "boom"

    first = harness.http.get("/api/sandbox/scheduler/runs?limit=2").json()
    assert len(first["runs"]) == 2 and first["next_cursor"]
    rest = harness.http.get(
        f"/api/sandbox/scheduler/runs?limit=2&before={first['next_cursor']}").json()
    assert [r["run_id"] for r in rest["runs"]] == ["r0"]


def test_run_timestamps_carry_utc_offset(harness):
    """started_at/finished_at must be tz-aware in the JSON — offset-less
    strings get parsed as *local* time by `new Date()` in the browser, which
    displayed run history in UTC wall-clock. Legacy naive rows are stamped UTC."""
    job_id = harness.http.post(
        "/api/sandbox/scheduler/jobs", json=_cron_job()).json()["id"]
    harness.dataplane.scheduler.record_run(harness.account_id, JobRunRecord(
        run_id="naive", job_id=job_id, job_name="Daily briefing", username="carol",
        started_at="2026-07-12T09:00:00.000000",  # legacy naive-UTC row
        finished_at="2026-07-12T09:01:00.000000", status="success"))

    (run,) = harness.http.get("/api/sandbox/scheduler/runs").json()["runs"]
    for field in ("started_at", "finished_at"):
        assert run[field].endswith("Z") or "+00:00" in run[field], run[field]


# --- the 7 MCP tools -----------------------------------------------------------


@pytest.fixture
def tools(dataplane, monkeypatch):
    from priva_agent_runner.services.scheduled_runs import mcp_tools

    account_id = dataplane.accounts.create("carol", "pw").account_id
    monkeypatch.setenv("ACCOUNT_ID", account_id)
    monkeypatch.setattr(mcp_tools, "get_client", lambda: dataplane)

    by_name = {t.name: t for t in mcp_tools.build_scheduler_tools("carol")}
    assert set(by_name) == {
        "scheduler_list_jobs", "scheduler_view_job", "scheduler_create_job",
        "scheduler_delete_job", "scheduler_trigger_job", "scheduler_pause_job",
        "scheduler_resume_job",
    }
    return SimpleNamespace(
        by_name=by_name, dataplane=dataplane, account_id=account_id,
        mcp_tools=mcp_tools, monkeypatch=monkeypatch,
    )


def _run(tool, args) -> str:
    out = asyncio.run(tool.handler(args))
    text = out["content"][0]["text"]
    if out.get("is_error"):
        return f"ERROR: {text}"
    return text


def test_mcp_create_list_view_pause_resume_delete(tools):
    created = _run(tools.by_name["scheduler_create_job"], {
        "name": "Nightly backup", "job_type": "user_script",
        "trigger_type": "cron", "cron_expr": "0 3 * * *",
        "language": "shell", "script": "tar -czf /tmp/b.tgz notes/",
    })
    assert "Created job **Nightly backup**" in created

    (job,) = tools.dataplane.scheduler.list_jobs(tools.account_id)
    assert job.job_config.job_type == "user_script" and job.timezone == "Asia/Shanghai"

    assert "Nightly backup" in _run(tools.by_name["scheduler_list_jobs"], {})
    view = _run(tools.by_name["scheduler_view_job"], {"job_id": "nightly"})  # partial name
    assert "cron 0 3 * * *" in view and "shell" in view

    assert "Paused" in _run(tools.by_name["scheduler_pause_job"], {"job_id": job.id})
    assert tools.dataplane.scheduler.get_job(job.id).status == "paused"
    assert "Resumed" in _run(tools.by_name["scheduler_resume_job"], {"job_id": "Nightly backup"})
    assert tools.dataplane.scheduler.get_job(job.id).status == "active"

    assert "Deleted job" in _run(tools.by_name["scheduler_delete_job"], {"job_id": job.id})
    assert tools.dataplane.scheduler.list_jobs(tools.account_id) == []


def test_mcp_interval_create_and_trigger(tools):
    _run(tools.by_name["scheduler_create_job"], {
        "name": "poll api", "job_type": "http_call", "trigger_type": "interval",
        "interval_minutes": 90, "url": "https://example.com/health",
    })
    (job,) = tools.dataplane.scheduler.list_jobs(tools.account_id)
    assert job.trigger.hours == 1 and job.trigger.minutes == 30

    posted: list[str] = []

    class FakeAsyncClient:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **kw):
            posted.append(url)
            assert HEADER in (kw.get("headers") or {}), "trigger proxy sent no service token"
            return httpx.Response(202, json={"status": "accepted"})

    tools.monkeypatch.setattr(tools.mcp_tools.httpx, "AsyncClient", FakeAsyncClient)
    out = _run(tools.by_name["scheduler_trigger_job"], {"job_id": "poll api"})
    assert "Triggered immediate run" in out
    assert posted and posted[0].endswith(f"/internal/trigger/{job.id}")


def test_mcp_tools_scoped_to_own_account(tools):
    other = tools.dataplane.accounts.create("mallory", "pw").account_id
    from priva_common.models.scheduler import CronTriggerConfig, ScheduledJobDefinition
    foreign = tools.dataplane.scheduler.create_job(other, ScheduledJobDefinition(
        id="foreign2", name="secret job", prompt="x",
        trigger=CronTriggerConfig(expr="0 9 * * *"), timezone="UTC"))

    assert "No scheduled jobs" in _run(tools.by_name["scheduler_list_jobs"], {})
    # a guessed foreign job_id resolves to nothing — delete can't cross tenants
    assert "ERROR" in _run(tools.by_name["scheduler_delete_job"], {"job_id": foreign.id})
    assert tools.dataplane.scheduler.get_job(foreign.id) is not None
