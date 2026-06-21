"""gRPC data-plane transport round-trip: the server (wrapping the in-process
services over SQLite) ↔ the build_grpc_client stores. Covers accounts (incl. the
UNSET api_key semantics), the new secret store, quota, bindings, and admin.
scheduler is deferred over gRPC (Phase 4) and not exercised here.
"""

from __future__ import annotations

import pytest

from priva_common.config import Settings
from priva_common.dataplane.grpc_client import _cache, build_grpc_client
from priva_data_spine.server import build_server


@pytest.fixture
def client(tmp_path):
    s = Settings()
    s.dataspine.sqlite_path = str(tmp_path / "ds.db")
    server = build_server(s)
    port = server.add_insecure_port("127.0.0.1:0")  # 0 -> OS picks a free port, returned
    server.start()
    s.dataspine.grpc_dsn = f"127.0.0.1:{port}"
    try:
        yield build_grpc_client(s)
    finally:
        server.stop(None)
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


def test_secret_store_roundtrip(client):
    aid = client.accounts.create("carol", "pw").account_id
    sec = client.secrets.put(aid, {"ANTHROPIC_AUTH_TOKEN": "t", "ANTHROPIC_MODEL": "claude-opus-4-8"})
    assert sec.generation == 1
    got = client.secrets.get(aid)
    assert got.bundle["ANTHROPIC_AUTH_TOKEN"] == "t"
    assert got.bundle["ANTHROPIC_MODEL"] == "claude-opus-4-8"
    assert client.secrets.put(aid, {"ANTHROPIC_AUTH_TOKEN": "t2"}).generation == 2
    assert client.secrets.list_account_ids() == [aid]
    assert client.secrets.get("missing") is None


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


def test_admin_health_and_stats(client):
    client.accounts.create("frank", "pw")
    assert client.admin.healthz() == "ok"
    ready, _ = client.admin.readyz()
    assert ready is True
    assert client.admin.stats()["accounts"] >= 1
