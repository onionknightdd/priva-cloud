"""Runner-side scheduled-run intake + executors (Phase 4a, step 2 — design §7).

Covers the D13 admission handshake (202 idempotent / 409 overlap / 429 cap),
the http_call + user_script executors end-to-end through the RunRegistry, the
agent_run executor against a faked agent_run_events (session tagging, D14
timeout/max_turns classification), the pod-owned FinishRun write, and the D15
retention prune. The real claude CLI is never spawned.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import os
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from priva_common.models.auth import UserRecord

USER = UserRecord(
    username="carol", password_hash="x", role="user", account_id="acct-1",
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Pin every on-disk surface (session meta, transcripts, audit) to tmp."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("PRIVA_HOME", str(tmp_path / "home"))
    # Dev machines route env-proxied httpx through a system proxy that 502s
    # loopback targets — keep the http_call test's localhost fetch direct.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    (tmp_path / "claude").mkdir(parents=True, exist_ok=True)
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    (tmp_path / "ws").mkdir(parents=True, exist_ok=True)
    return tmp_path


class FakeDataplane:
    def __init__(self, cap: int = 10):
        self.finishes: list = []
        self.cap = cap
        self.scheduler = SimpleNamespace(finish_run=lambda rec: self.finishes.append(rec))
        self.quota = SimpleNamespace(
            ensure=lambda aid: SimpleNamespace(max_concurrent_sessions=self.cap)
        )


@pytest.fixture
def harness(env, monkeypatch):
    """Router mounted on a bare app; dataplane + workspace faked; state reset."""
    from priva_agent_runner.routers import scheduled_runs as router_mod
    from priva_agent_runner.services.scheduled_runs import executor

    fake = FakeDataplane()
    monkeypatch.setattr(executor, "get_client", lambda: fake)
    monkeypatch.setattr(router_mod, "get_user_workspace", lambda u: str(env / "ws"))

    executor._accepted.clear()
    executor._live_by_job.clear()

    app = FastAPI()
    app.include_router(router_mod.router)
    app.dependency_overrides[router_mod.require_account] = lambda: USER

    with TestClient(app) as client:
        yield SimpleNamespace(client=client, fake=fake, executor=executor, tmp=env)

    # Don't leak still-live runs (their tasks die with the portal loop anyway).
    for state in list(executor._accepted.values()):
        state.record.cancelled.set()
    executor._accepted.clear()
    executor._live_by_job.clear()


def _wait(predicate, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _http_job(url: str, run_id: str = "r-http-1", job_id: str = "job-http") -> dict:
    return {
        "run_id": run_id, "job_id": job_id, "job_name": "ping",
        "job_config": {"job_type": "http_call", "method": "GET", "url": url,
                       "timeout_seconds": 10},
    }


def _script_job(script: str, run_id: str, job_id: str, timeout: int = 30) -> dict:
    return {
        "run_id": run_id, "job_id": job_id, "job_name": "script",
        "job_config": {"job_type": "user_script", "language": "python",
                       "source": "inline", "script": script,
                       "timeout_seconds": timeout},
    }


@pytest.fixture
def http_server():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"pong"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # keep pytest output clean
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/"
    finally:
        srv.shutdown()


# --- http_call / user_script executors through the full admission path -------


def test_http_call_success_and_idempotent_repost(harness, http_server):
    body = _http_job(http_server)
    resp = harness.client.post("/api/sandbox/agent/scheduled-run", json=body)
    assert resp.status_code == 202
    assert resp.json() == {"status": "accepted", "run_id": "r-http-1", "duplicate": False}

    assert _wait(lambda: harness.fake.finishes)
    rec = harness.fake.finishes[0]
    assert rec.status == "success" and rec.is_error is False
    assert rec.run_id == "r-http-1" and rec.job_id == "job-http"
    assert rec.session_id is None  # builtin jobs open no agent session
    assert "HTTP 200" in rec.result_summary and rec.duration_ms >= 0

    # The run rode the registry: events buffered for attach, terminal status set.
    from priva_agent_runner.services.claude_sdk.run_registry import run_registry
    record = run_registry.get(run_id="r-http-1")
    assert record is not None and record.status == "completed"
    kinds = [e[1] for e in record.events]
    assert "http_request" in kinds and "http_response" in kinds

    # D13 idempotency: re-POST → 202 again, never a second execution.
    resp2 = harness.client.post("/api/sandbox/agent/scheduled-run", json=body)
    assert resp2.status_code == 202 and resp2.json()["duplicate"] is True
    time.sleep(0.2)
    assert len(harness.fake.finishes) == 1


def test_user_script_error_exit_code(harness):
    body = _script_job("print('boom'); raise SystemExit(3)", "r-s1", "job-s")
    assert harness.client.post("/api/sandbox/agent/scheduled-run", json=body).status_code == 202
    assert _wait(lambda: harness.fake.finishes)
    rec = harness.fake.finishes[0]
    assert rec.status == "error" and rec.is_error is True
    assert "exited with code 3" in rec.error_message
    assert "boom" in rec.result_summary


def test_finish_is_persisted_before_registry_and_activity_slots_are_released(harness):
    """A data-spine write in flight must keep both scale-down and overlap fenced."""
    from priva_agent_runner import activity
    from priva_agent_runner.services.claude_sdk.run_registry import run_registry

    finish_entered = threading.Event()
    allow_finish = threading.Event()
    active_before = activity.state()[0]

    def blocking_finish(rec):
        finish_entered.set()
        assert allow_finish.wait(timeout=5)
        harness.fake.finishes.append(rec)

    harness.fake.scheduler.finish_run = blocking_finish
    body = _script_job("print('done')", "r-persist-order", "job-persist-order")
    assert harness.client.post("/api/sandbox/agent/scheduled-run", json=body).status_code == 202
    assert finish_entered.wait(timeout=5)

    # Execution is over, but FinishRun has not committed. Both guards remain.
    record = run_registry.get(run_id="r-persist-order")
    assert record is not None and record.status == "running"
    assert harness.executor.live_run_for_job("job-persist-order") == "r-persist-order"
    assert activity.state()[0] == active_before + 1

    allow_finish.set()
    assert _wait(lambda: harness.executor.live_run_for_job("job-persist-order") is None)
    assert record.status == "completed"
    assert activity.state()[0] == active_before


def test_cancelled_task_cleans_local_slots_when_finish_persistence_fails(
    env, monkeypatch,
):
    """Shutdown cancellation delegates DB repair to the sweep but never leaks slots."""
    from priva_agent_runner import activity
    from priva_agent_runner.services.scheduled_runs import executor
    from priva_common.models.scheduler import ScheduledRunRequest

    entered = None

    async def blocked_builtin(record, req, user, cwd, outcome):
        entered.set()
        await asyncio.Event().wait()

    def fail_finish(_record):
        raise RuntimeError("data-spine unavailable")

    async def go():
        nonlocal entered
        entered = asyncio.Event()
        executor._accepted.clear()
        executor._live_by_job.clear()
        monkeypatch.setattr(executor, "_execute_builtin", blocked_builtin)
        monkeypatch.setattr(
            executor,
            "get_client",
            lambda: SimpleNamespace(
                scheduler=SimpleNamespace(finish_run=fail_finish),
            ),
        )

        active_before = activity.state()[0]
        req = ScheduledRunRequest.model_validate(
            _script_job("print('never')", "r-cancel-persist", "job-cancel-persist"),
        )
        record = executor.start(req, USER, str(env / "ws"))
        await asyncio.wait_for(entered.wait(), timeout=2)
        assert activity.state()[0] == active_before + 1

        record.task.cancel()
        result = await asyncio.gather(record.task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert record.status == "aborted"
        assert executor.live_run_for_job(req.job_id) is None
        assert activity.state()[0] == active_before

    asyncio.run(go())


def test_job_overlap_409_and_abort_records_cancelled(harness):
    sleeper = _script_job("import time; time.sleep(60)", "r-a", "job-x", timeout=120)
    assert harness.client.post("/api/sandbox/agent/scheduled-run", json=sleeper).status_code == 202
    assert _wait(lambda: harness.executor.live_run_for_job("job-x") == "r-a")

    # Same job while live → 409 (D9 backstop); a different job is unaffected.
    other_run_same_job = _script_job("print(1)", "r-b", "job-x")
    assert harness.client.post(
        "/api/sandbox/agent/scheduled-run", json=other_run_same_job
    ).status_code == 409

    # Explicit abort (the WS stop path sets record.cancelled) → cancelled record.
    harness.executor._accepted["r-a"].record.cancelled.set()
    assert _wait(lambda: any(r.run_id == "r-a" for r in harness.fake.finishes))
    rec = next(r for r in harness.fake.finishes if r.run_id == "r-a")
    assert rec.status == "cancelled"
    # Job slot freed — the same job admits again.
    assert harness.executor.live_run_for_job("job-x") is None


def test_concurrency_cap_429(harness):
    harness.fake.cap = 1
    sleeper = _script_job("import time; time.sleep(60)", "r-live", "job-1", timeout=120)
    assert harness.client.post("/api/sandbox/agent/scheduled-run", json=sleeper).status_code == 202
    assert _wait(lambda: harness.executor.live_run_for_job("job-1") == "r-live")

    resp = harness.client.post(
        "/api/sandbox/agent/scheduled-run",
        json=_script_job("print(1)", "r-capped", "job-2"),
    )
    assert resp.status_code == 429 and resp.json()["detail"] == "concurrency_cap"

    harness.executor._accepted["r-live"].record.cancelled.set()
    assert _wait(lambda: any(r.run_id == "r-live" for r in harness.fake.finishes))


# --- agent_run executor against a faked agent_run_events ---------------------


def _agent_job(run_id: str = "r-ag1", job_id: str = "job-ag", **cfg) -> dict:
    return {
        "run_id": run_id, "job_id": job_id, "job_name": "daily briefing",
        "job_config": {"job_type": "agent_run", "prompt": "brief me", **cfg},
    }


def test_agent_run_success_tags_session(harness, monkeypatch):
    from priva_agent_runner.services.claude_sdk import session_meta
    from priva_agent_runner.services.scheduled_runs import executor

    seen: dict = {}

    async def fake_agent_run_events(prompt, session_id, permission_mode, cwd,
                                    username, model_override=None, **kwargs):
        seen.update(prompt=prompt, session_id=session_id, mode=permission_mode,
                    username=username, model=model_override,
                    feedback=kwargs.get("enable_permission_feedback"),
                    max_turns=kwargs.get("max_turns"))
        emit = kwargs["emit"]
        await emit("system", {"subtype": "init", "data": {"session_id": "sess-77"}})
        await emit("assistant", {"content": [{"type": "text", "text": "working"}]})
        await emit("result", {
            "session_id": "sess-77", "is_error": False, "num_turns": 5,
            "result": "wrote notes/daily.md — " + "x" * 400, "subtype": "success",
        })

    monkeypatch.setattr(executor, "agent_run_events", fake_agent_run_events)
    assert harness.client.post(
        "/api/sandbox/agent/scheduled-run", json=_agent_job()
    ).status_code == 202
    assert _wait(lambda: harness.fake.finishes)

    # The run went through ws_run's internals with the D2/D14 posture.
    assert seen["mode"] == "bypassPermissions" and seen["session_id"] is None
    assert seen["feedback"] is False and seen["max_turns"] == 50  # D14 defaults
    assert seen["username"] == "carol" and seen["prompt"] == "brief me"

    rec = harness.fake.finishes[0]
    assert rec.status == "success" and rec.session_id == "sess-77"
    assert rec.num_turns == 5 and len(rec.result_summary) == 200

    # D3: the session is scheduler-tagged for the sidebar ⏰ + the D15 prune.
    info = session_meta.get_scheduler_info("sess-77")
    assert info == {"job_id": "job-ag", "job_name": "daily briefing", "run_id": "r-ag1"}


def test_agent_run_wall_clock_timeout(harness, monkeypatch):
    from priva_agent_runner.services.scheduled_runs import executor

    async def hanging(prompt, session_id, permission_mode, cwd, username,
                      model_override=None, **kwargs):
        await kwargs["cancelled"].wait()  # honours the graceful stop

    monkeypatch.setattr(executor, "agent_run_events", hanging)
    body = _agent_job(run_id="r-slow", job_id="job-slow", timeout_seconds=1)
    assert harness.client.post("/api/sandbox/agent/scheduled-run", json=body).status_code == 202
    assert _wait(lambda: harness.fake.finishes)
    rec = harness.fake.finishes[0]
    assert rec.status == "error" and rec.error_message == "timeout"  # D11/D14 token


def test_agent_run_max_turns_cap(harness, monkeypatch):
    from priva_agent_runner.services.scheduled_runs import executor

    async def capped(prompt, session_id, permission_mode, cwd, username,
                     model_override=None, **kwargs):
        await kwargs["emit"]("result", {
            "session_id": "sess-88", "is_error": True, "num_turns": 3,
            "result": "hit the cap", "subtype": "error_max_turns",
        })

    monkeypatch.setattr(executor, "agent_run_events", capped)
    body = _agent_job(run_id="r-cap", job_id="job-cap", max_turns=3)
    assert harness.client.post("/api/sandbox/agent/scheduled-run", json=body).status_code == 202
    assert _wait(lambda: harness.fake.finishes)
    rec = harness.fake.finishes[0]
    assert rec.status == "error" and rec.error_message == "max_turns"
    assert rec.num_turns == 3 and rec.session_id == "sess-88"


def test_agent_run_crash_reports_error(harness, monkeypatch):
    from priva_agent_runner.services.scheduled_runs import executor

    async def exploding(*a, **kw):
        raise RuntimeError("CLI could not start")

    monkeypatch.setattr(executor, "agent_run_events", exploding)
    body = _agent_job(run_id="r-boom", job_id="job-boom")
    assert harness.client.post("/api/sandbox/agent/scheduled-run", json=body).status_code == 202
    assert _wait(lambda: harness.fake.finishes)
    rec = harness.fake.finishes[0]
    assert rec.status == "error" and "CLI could not start" in rec.error_message


# --- D15 retention prune ------------------------------------------------------


def test_retention_prunes_only_old_scheduler_transcripts(env, monkeypatch):
    from claude_agent_sdk._internal.sessions import _canonicalize_path, _get_project_dir
    from priva_agent_runner.services.claude_sdk import session_meta
    from priva_agent_runner.services.scheduled_runs.retention import (
        prune_scheduler_transcripts,
    )

    project_dir = _get_project_dir(_canonicalize_path(str(env / "ws")))
    project_dir.mkdir(parents=True, exist_ok=True)

    def make_jsonl(sid: str, age_days: float):
        p = project_dir / f"{sid}.jsonl"
        p.write_text(json.dumps({"type": "user"}) + "\n")
        old = time.time() - age_days * 86400
        os.utime(p, (old, old))
        return p

    old_sched = make_jsonl("sess-old", 10)
    new_sched = make_jsonl("sess-new", 1)
    interactive_old = make_jsonl("sess-chat", 30)  # NOT in the scheduler index

    async def seed():
        await session_meta.set_scheduler_session(
            "sess-old", job_id="j", job_name="n", run_id="r1")
        await session_meta.set_scheduler_session(
            "sess-new", job_id="j", job_name="n", run_id="r2")
        await session_meta.set_scheduler_session(
            "sess-gone", job_id="j", job_name="n", run_id="r3")  # no file on disk
        return await prune_scheduler_transcripts(retention_days=7)

    removed = asyncio.run(seed())

    assert removed == 1
    assert not old_sched.exists()
    assert new_sched.exists()
    assert interactive_old.exists()  # never a candidate
    index = session_meta.list_scheduler_sessions()
    assert set(index) == {"sess-new"}  # old pruned; orphan index row dropped

    # Idempotent + disable switch.
    assert asyncio.run(prune_scheduler_transcripts(retention_days=7)) == 0
    assert asyncio.run(prune_scheduler_transcripts(retention_days=0)) == 0
