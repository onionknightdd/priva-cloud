"""gRPC data-plane transport round-trip: the server (wrapping the in-process
services over the repo) ↔ the build_grpc_client stores. Covers accounts (incl.
the UNSET api_key semantics), quota, bindings, and admin. The scheduler domain
is exercised in test_scheduler_dataplane.py.

Runs against SQLite always; parametrized to also run against Postgres when
TEST_POSTGRES_DSN is set (e.g. postgresql://postgres:test@127.0.0.1:5433/priva).
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import grpc
import pytest

from priva_common.config import Settings
from priva_common.dataplane.grpc_client import _cache, build_grpc_client
from priva_data_spine.server import build_server
from priva_data_spine.service import NetworkIsolationService, build_repo

PG_DSN = os.environ.get("TEST_POSTGRES_DSN")
_pg_param = pytest.param(
    "postgres", marks=pytest.mark.skipif(not PG_DSN, reason="TEST_POSTGRES_DSN not set"))


def wipe_pg(dsn: str) -> None:
    """Fresh public schema so each test starts empty (PgRepo re-creates tables)."""
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
    port = server.add_insecure_port("127.0.0.1:0")  # 0 -> OS picks a free port, returned
    server.start()
    s.dataspine.grpc_dsn = f"127.0.0.1:{port}"
    try:
        yield build_grpc_client(s)
    finally:
        server.stop(None)
        repo.close()
        _cache.clear()


def test_accounts_crud_and_lookups(client, as_service_identity):
    u = client.accounts.create("alice", "pw", "admin")
    assert u.account_id and u.username == "alice"
    aid = u.account_id

    # AccountService/Get is used by the scheduler/channel connector, not the
    # control panel. Keep the transport coverage under a real shipped caller.
    as_service_identity("scheduler")
    assert client.accounts.get(aid).username == "alice"
    assert client.accounts.get("missing") is None
    as_service_identity("control-panel")
    assert client.accounts.get_by_username("alice").account_id == aid
    assert client.accounts.has_users() is True
    with pytest.raises(grpc.RpcError) as count_denied:
        client.accounts.count_admins()
    assert count_denied.value.code() == grpc.StatusCode.PERMISSION_DENIED
    assert client.accounts.verify_password("alice", "pw") is True
    assert client.accounts.verify_password("alice", "nope") is False
    assert len(client.accounts.list()) == 1

    with pytest.raises(ValueError):
        client.accounts.create("alice", "pw")

    client.accounts.delete(aid)
    as_service_identity("scheduler")
    assert client.accounts.get(aid) is None


def test_account_api_key_unset_set_clear(client):
    aid = client.accounts.create("bob", "pw").account_id
    # UNSET (not passed) leaves it absent
    assert client.accounts.get_by_username("bob").api_key is None
    # set
    client.accounts.update(aid, api_key="sk-key")
    assert client.accounts.get_by_username("bob").api_key == "sk-key"
    assert client.accounts.find_by_api_key("sk-key").account_id == aid
    # clear (None)
    client.accounts.update(aid, api_key=None)
    assert client.accounts.get_by_username("bob").api_key is None
    assert client.accounts.find_by_api_key("sk-key") is None


def test_quota_ensure_and_privileged_update_is_default_denied(
    client, as_service_identity
):
    aid = client.accounts.create("dave", "pw").account_id
    as_service_identity("agent-runner", account_id=aid)
    assert client.quota.ensure(aid).max_concurrent_sessions == 3
    with pytest.raises(grpc.RpcError) as update_denied:
        client.quota.set(aid, max_concurrent_sessions=5, tier="pro")
    assert update_denied.value.code() == grpc.StatusCode.PERMISSION_DENIED
    assert client.quota.get(aid).max_concurrent_sessions == 3


def test_bindings_and_first_run_cas(client, as_service_identity):
    aid = client.accounts.create("erin", "pw").account_id
    as_service_identity("channel-connector")
    b = client.bindings.bind(aid, "sess-1")
    assert b.binding_id
    assert len(client.bindings.list_bindings(aid)) == 1
    for call in (
        lambda: client.bindings.get_binding(b.binding_id),
        lambda: client.bindings.claim_first_run_im(b.binding_id),
    ):
        with pytest.raises(grpc.RpcError) as dead_rpc_denied:
            call()
        assert dead_rpc_denied.value.code() == grpc.StatusCode.PERMISSION_DENIED


def test_admin_health_and_stats(client, backend, as_service_identity):
    aid = client.accounts.create("frank", "pw").account_id
    # Healthz is called by tenant runners; the control panel uses Readyz/Stats.
    as_service_identity("agent-runner", account_id=aid)
    assert client.admin.healthz() == "ok"
    as_service_identity("control-panel")
    ready, _ = client.admin.readyz()
    assert ready is True
    stats = client.admin.stats()
    assert stats["accounts"] >= 1
    assert stats["backend"] == backend  # self-reported truthfully (System Map label)


def test_runner_defaults_terminal_policy_round_trip(client):
    seeded = client.runner_defaults.get()
    assert seeded.terminal_resource_percent == 0
    assert seeded.terminal_max_sessions == 2

    updated = client.runner_defaults.set(
        terminal_resource_percent=25,
        terminal_max_sessions=4,
        terminal_idle_timeout_seconds=900,
        terminal_max_lifetime_seconds=7200,
        terminal_scale_down_grace_seconds=60,
    )
    assert updated.terminal_resource_percent == 25
    assert updated.terminal_max_sessions == 4
    assert updated.terminal_idle_timeout_seconds == 900
    assert updated.terminal_max_lifetime_seconds == 7200
    assert updated.terminal_scale_down_grace_seconds == 60


def test_network_isolation_seeds_a_secure_functional_default(client):
    seeded = client.network_isolation.get()
    assert seeded.runner_deny_internal is True
    assert seeded.terminal_deny_internal is True
    assert seeded.deny_tenant_peers is True
    assert seeded.egress_mode == "allowlist"
    assert {entry.host for entry in seeded.egress_allowlist} >= {
        ".anthropic.com",
        "registry.npmjs.org",
        "pypi.org",
    }


def test_corrupt_egress_allowlist_fails_closed():
    record = NetworkIsolationService._to_record({
        "runner_deny_internal": 1,
        "terminal_deny_internal": 1,
        "deny_tenant_peers": 1,
        "egress_mode": "allowlist",
        "egress_allowlist": "{not-json",
    })
    assert record.egress_mode == "allowlist"
    assert record.egress_allowlist == []


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_persisted_invalid_egress_port_fails_closed(port):
    record = NetworkIsolationService._to_record({
        "runner_deny_internal": 1,
        "terminal_deny_internal": 1,
        "deny_tenant_peers": 1,
        "egress_mode": "allowlist",
        "egress_allowlist": json.dumps([
            {"host": "api.example.com", "port": port},
        ]),
    })
    assert record.egress_allowlist == []


def test_network_isolation_grpc_rejects_zero_port(client):
    with pytest.raises(grpc.RpcError) as exc:
        client.network_isolation.set(egress_allowlist=[
            SimpleNamespace(host="api.example.com", port=0),
        ])
    assert exc.value.code() == grpc.StatusCode.INVALID_ARGUMENT


def test_network_isolation_client_applies_configured_rpc_deadline(monkeypatch):
    from priva_common.dataplane.v1 import (
        network_isolation_pb2,
        network_isolation_pb2_grpc,
    )

    calls: list[float | None] = []

    class Stub:
        def __init__(self, _channel):
            pass

        def Get(self, _request, timeout=None):
            calls.append(timeout)
            return network_isolation_pb2.NetworkIsolation(
                runner_deny_internal=True,
                terminal_deny_internal=True,
                deny_tenant_peers=True,
                egress_mode="deny_all",
            )

    monkeypatch.setattr(
        network_isolation_pb2_grpc,
        "NetworkIsolationServiceStub",
        Stub,
    )
    settings = Settings()
    settings.dataspine.grpc_dsn = "127.0.0.1:1"
    settings.dataspine.network_isolation_rpc_timeout_seconds = 2.5
    _cache.clear()
    try:
        record = build_grpc_client(settings).network_isolation.get()
    finally:
        _cache.clear()

    assert record.egress_mode == "deny_all"
    assert calls == [2.5]


def test_network_isolation_service_rejects_zero_port(tmp_path):
    settings = Settings()
    settings.dataspine.backend = "sqlite"
    settings.dataspine.sqlite_path = str(tmp_path / "service.db")
    repo = build_repo(settings)
    try:
        service = NetworkIsolationService(repo)
        with pytest.raises(ValueError):
            service.set(egress_allowlist=[
                SimpleNamespace(host="api.example.com", port=0),
            ])
    finally:
        repo.close()


def test_missing_isolation_scalars_fail_closed():
    record = NetworkIsolationService._to_record({
        "egress_allowlist": "[]",
    })
    assert record.runner_deny_internal is True
    assert record.terminal_deny_internal is True
    assert record.deny_tenant_peers is True
    assert record.egress_mode == "deny_all"
