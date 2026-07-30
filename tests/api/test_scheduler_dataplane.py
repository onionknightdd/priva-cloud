"""Scheduler domain over the gRPC data-plane transport (Phase 4a, step 1).

Round-trips jobs / runs / the job_fire exactly-once claim through
build_server ↔ build_grpc_client, mirroring test_dataplane_grpc.py's harness.
Runs against SQLite always; also against Postgres when TEST_POSTGRES_DSN is set.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import grpc
import pytest

from priva_common.config import Settings
from priva_common.dataplane.grpc_client import _cache, build_grpc_client
from priva_common.models.scheduler import (
    AgentRunConfig,
    CronTriggerConfig,
    IntervalTriggerConfig,
    JobRunRecord,
    ScheduledJobDefinition,
)
from priva_data_spine.server import build_server
from priva_data_spine.service import build_repo

PG_DSN = os.environ.get("TEST_POSTGRES_DSN")
_pg_param = pytest.param(
    "postgres", marks=pytest.mark.skipif(not PG_DSN, reason="TEST_POSTGRES_DSN not set"))


def wipe_pg(dsn: str) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")


@pytest.fixture(params=["sqlite", _pg_param])
def backend(request):
    return request.param


@pytest.fixture
def client(backend, tmp_path):
    s = Settings()
    s.dataspine.backend = backend
    if backend == "postgres":
        wipe_pg(PG_DSN)
        s.dataspine.postgres_dsn = PG_DSN
    else:
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
def account(client):
    return client.accounts.create("carol", "pw").account_id


def _defn(job_id: str, *, cron: str = "0 9 * * 1-5", prompt: str = "daily briefing",
          status: str = "active") -> ScheduledJobDefinition:
    return ScheduledJobDefinition(
        id=job_id,
        name=f"job {job_id}",
        prompt=prompt,
        trigger=CronTriggerConfig(expr=cron),
        timezone="Asia/Shanghai",
        status=status,
        job_config=AgentRunConfig(prompt=prompt),
    )


def test_job_crud_roundtrip(client, account, as_service_identity):
    as_service_identity("agent-runner", account_id=account)
    created = client.scheduler.create_job(account, _defn("j1"))
    assert created.id == "j1" and created.status == "active"
    assert created.trigger.type == "cron" and created.trigger.expr == "0 9 * * 1-5"
    assert created.job_config.job_type == "agent_run"
    # D14 guards ride the config blob with defaults
    assert created.job_config.timeout_seconds == 1800 and created.job_config.max_turns == 50

    got = client.scheduler.get_job("j1")
    assert got.name == "job j1" and got.timezone == "Asia/Shanghai"
    as_service_identity("scheduler")
    assert client.scheduler.get_job("missing") is None
    as_service_identity("agent-runner", account_id=account)

    updated = client.scheduler.update_job("j1", _defn("j1", cron="0 18 * * 5", prompt="weekly"))
    assert updated.trigger.expr == "0 18 * * 5" and updated.prompt == "weekly"
    with pytest.raises(grpc.RpcError) as missing_update:
        client.scheduler.update_job("missing", _defn("missing"))
    assert missing_update.value.code() == grpc.StatusCode.PERMISSION_DENIED

    # interval trigger shape survives the wire too
    ivl = ScheduledJobDefinition(
        id="j2", name="every 6h", trigger=IntervalTriggerConfig(hours=6),
        timezone="UTC", prompt="tick")
    assert client.scheduler.create_job(account, ivl).trigger.hours == 6

    assert {j.id for j in client.scheduler.list_jobs(account)} == {"j1", "j2"}

    paused = client.scheduler.set_job_status("j2", "paused")
    assert paused.status == "paused"
    as_service_identity("scheduler")
    active = client.scheduler.list_active_jobs()
    assert [(a, j.id) for a, j in active] == [(account, "j1")]

    as_service_identity("agent-runner", account_id=account)
    assert client.scheduler.delete_job("j2") is True
    with pytest.raises(grpc.RpcError) as missing_delete:
        client.scheduler.delete_job("j2")
    assert missing_delete.value.code() == grpc.StatusCode.PERMISSION_DENIED


def test_run_lifecycle_and_skip_record(client, account, as_service_identity):
    as_service_identity("agent-runner", account_id=account)
    client.scheduler.create_job(account, _defn("j1"))

    as_service_identity("scheduler")
    born = client.scheduler.start_run(account, JobRunRecord(
        run_id="r1", job_id="j1", job_name="job j1", username="", status="running",
        session_id="sess-9"))
    assert born.status == "running" and born.session_id == "sess-9"
    assert client.scheduler.get_latest_run(account, "j1").run_id == "r1"

    done = client.scheduler.finish_run(JobRunRecord(
        run_id="r1", job_id="j1", job_name="job j1", username="",
        finished_at="2026-07-12T09:02:41.000Z", status="success",
        duration_ms=161000, num_turns=12, result_summary="wrote notes/daily.md"))
    assert done.status == "success" and done.duration_ms == 161000 and done.num_turns == 12
    assert done.session_id == "sess-9"  # a session-less outcome write never clobbers

    # The real dispatch shape: StartRun has no session (the pod hasn't started
    # the CLI yet); the pod's FinishRun carries it and it must persist.
    client.scheduler.start_run(account, JobRunRecord(
        run_id="r0", job_id="j1", job_name="job j1", username="", status="running",
        started_at="2026-07-11T09:00:00.000Z"))
    finished = client.scheduler.finish_run(JobRunRecord(
        run_id="r0", job_id="j1", job_name="job j1", username="",
        finished_at="2026-07-11T09:03:00.000Z", status="success", session_id="sess-42"))
    assert finished.session_id == "sess-42"

    # one-shot skipped record (D11: reason rides error_message) via RecordRun
    skipped = client.scheduler.record_run(account, JobRunRecord(
        run_id="r2", job_id="j1", job_name="job j1", username="", status="skipped",
        finished_at="2026-07-13T09:00:00.000Z", error_message="already_running"))
    assert skipped.status == "skipped" and skipped.error_message == "already_running"

    # Run-history reads are performed by the owning runner/control-panel path,
    # not by the scheduler workload itself.
    as_service_identity("agent-runner", account_id=account)
    assert client.scheduler.get_run(account, "r1").result_summary == "wrote notes/daily.md"
    assert client.scheduler.get_run(account, "missing") is None
    with pytest.raises(grpc.RpcError) as cross_tenant:
        client.scheduler.get_run("other-account", "r1")
    assert cross_tenant.value.code() == grpc.StatusCode.PERMISSION_DENIED

    page = client.scheduler.list_runs(account, limit=10)
    assert [r.run_id for r in page.runs] == ["r2", "r1", "r0"]  # newest-first
    assert page.total == 3

    only_skipped = client.scheduler.list_runs(account, status="skipped")
    assert [r.run_id for r in only_skipped.runs] == ["r2"]
    assert only_skipped.total is None  # filtered => total unknown (-1 on the wire)

    # No shipped workload owns the destructive bulk-delete RPC. It remains
    # default-denied until a concrete maintenance caller is introduced.
    as_service_identity("scheduler")
    with pytest.raises(grpc.RpcError) as delete_denied:
        client.scheduler.delete_runs_before(account, "2099-01-01")
    assert delete_denied.value.code() == grpc.StatusCode.PERMISSION_DENIED


def test_run_keyset_pagination(client, account, as_service_identity):
    as_service_identity("agent-runner", account_id=account)
    client.scheduler.create_job(account, _defn("j1"))
    as_service_identity("scheduler")
    for i in range(5):
        client.scheduler.record_run(account, JobRunRecord(
            run_id=f"r{i}", job_id="j1", job_name="job j1", username="",
            started_at=f"2026-07-1{i}T09:00:00.000Z", status="success"))
    first = client.scheduler.list_runs(account, limit=2)
    assert [r.run_id for r in first.runs] == ["r4", "r3"] and first.next_cursor
    second = client.scheduler.list_runs(account, limit=2, before=first.next_cursor)
    assert [r.run_id for r in second.runs] == ["r2", "r1"]


def test_claim_fire_exactly_once(client, account, as_service_identity):
    as_service_identity("agent-runner", account_id=account)
    client.scheduler.create_job(account, _defn("j1"))

    as_service_identity("scheduler")
    assert client.scheduler.claim_fire("j1", 1780000000, "replica-a") is True
    assert client.scheduler.claim_fire("j1", 1780000000, "replica-b") is False  # lost
    assert client.scheduler.claim_fire("j1", 1780000060, "replica-b") is True   # next fire
    # a fire for a deleted/unknown job is "no claim", not an error (FK-safe)
    assert client.scheduler.claim_fire("ghost", 1780000000, "replica-a") is False

    assert client.scheduler.prune_fires_before("2099-01-01") == 2
    # pruned => the same key is claimable again (prune only runs far past misfire window)
    assert client.scheduler.claim_fire("j1", 1780000000, "replica-c") is True


def test_claim_fire_concurrent_single_winner(client, account, as_service_identity):
    as_service_identity("agent-runner", account_id=account)
    client.scheduler.create_job(account, _defn("j1"))
    as_service_identity("scheduler")
    epoch = 1780009999

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda i: client.scheduler.claim_fire("j1", epoch, f"replica-{i}"), range(8)))
    assert results.count(True) == 1 and results.count(False) == 7
