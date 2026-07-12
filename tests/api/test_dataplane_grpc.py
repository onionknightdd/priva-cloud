"""gRPC data-plane transport round-trip: the server (wrapping the in-process
services over the repo) ↔ the build_grpc_client stores. Covers accounts (incl.
the UNSET api_key semantics), quota, bindings, and admin. The scheduler domain
is exercised in test_scheduler_dataplane.py.

Runs against SQLite always; parametrized to also run against Postgres when
TEST_POSTGRES_DSN is set (e.g. postgresql://postgres:test@127.0.0.1:5433/priva).
"""

from __future__ import annotations

import os

import pytest

from priva_common.config import Settings
from priva_common.dataplane.grpc_client import _cache, build_grpc_client
from priva_data_spine.server import build_server
from priva_data_spine.service import build_repo

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


def test_accounts_crud_and_lookups(client):
    u = client.accounts.create("alice", "pw", "admin")
    assert u.account_id and u.username == "alice"
    aid = u.account_id

    assert client.accounts.get(aid).username == "alice"
    assert client.accounts.get_by_username("alice").account_id == aid
    assert client.accounts.get("missing") is None
    assert client.accounts.has_users() is True
    assert client.accounts.count_admins() == 1
    assert client.accounts.verify_password("alice", "pw") is True
    assert client.accounts.verify_password("alice", "nope") is False
    assert len(client.accounts.list()) == 1

    with pytest.raises(ValueError):
        client.accounts.create("alice", "pw")

    client.accounts.delete(aid)
    assert client.accounts.get(aid) is None


def test_account_api_key_unset_set_clear(client):
    aid = client.accounts.create("bob", "pw").account_id
    # UNSET (not passed) leaves it absent
    assert client.accounts.get(aid).api_key is None
    # set
    client.accounts.update(aid, api_key="sk-key")
    assert client.accounts.get(aid).api_key == "sk-key"
    assert client.accounts.find_by_api_key("sk-key").account_id == aid
    # clear (None)
    client.accounts.update(aid, api_key=None)
    assert client.accounts.get(aid).api_key is None
    assert client.accounts.find_by_api_key("sk-key") is None


def test_quota_ensure_and_set(client):
    aid = client.accounts.create("dave", "pw").account_id
    assert client.quota.ensure(aid).max_concurrent_sessions == 3
    client.quota.set(aid, max_concurrent_sessions=5, tier="pro")
    q = client.quota.get(aid)
    assert q.max_concurrent_sessions == 5 and q.tier == "pro"


def test_bindings_and_first_run_cas(client):
    aid = client.accounts.create("erin", "pw").account_id
    b = client.bindings.bind(aid, "sess-1")
    assert b.binding_id
    assert client.bindings.get_binding(b.binding_id).session_uuid == "sess-1"
    assert len(client.bindings.list_bindings(aid)) == 1
    assert client.bindings.claim_first_run_im(b.binding_id) is True
    assert client.bindings.claim_first_run_im(b.binding_id) is False


def test_admin_health_and_stats(client, backend):
    client.accounts.create("frank", "pw")
    assert client.admin.healthz() == "ok"
    ready, _ = client.admin.readyz()
    assert ready is True
    stats = client.admin.stats()
    assert stats["accounts"] >= 1
    assert stats["backend"] == backend  # self-reported truthfully (System Map label)
