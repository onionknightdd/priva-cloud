"""Account disable / enable / purge — the control-plane half (feat_account_deletion).

Endpoints run against a real sqlite data-spine; the kube hop is a fake CustomObjects
API so the CR patch/delete ordering is asserted without a cluster.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from priva_common.config import Settings
from priva_common.dataplane.grpc_client import _cache, build_grpc_client
from priva_common.models.auth import LoginRequest, UserRecord
from priva_control_panel import provisioner as P
from priva_data_spine.server import build_server
from priva_data_spine.service import build_repo

# Not a DB row: the bootstrap admin (global api key / settings.auth.admins), so the
# last-admin guards can be exercised against the single admin account that IS a row.
ADMIN = UserRecord(username="root", password_hash="x", role="admin")


def _kube_settings():
    return SimpleNamespace(kubernetes=SimpleNamespace(
        namespace_tenants="tenants", max_concurrent_sessions=3))


class _FakeCustom:
    def __init__(self):
        self.patches: list[tuple[str, dict]] = []
        self.deleted: list[str] = []
        self.missing: set[str] = set()

    def patch_namespaced_custom_object(self, group, version, ns, plural, name, body):
        if name in self.missing:
            raise P.client.ApiException(status=404)
        self.patches.append((name, body))

    def delete_namespaced_custom_object(self, group, version, ns, plural, name):
        if name in self.missing:
            raise P.client.ApiException(status=404)
        self.deleted.append(name)
        self.missing.add(name)


@pytest.fixture
def harness(tmp_path, monkeypatch):
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
    import priva_common.dataplane as dp_mod
    import priva_common.user_store as user_store
    monkeypatch.setattr(dp_mod, "get_client", lambda: dataplane)
    monkeypatch.setattr(user_store, "get_client", lambda: dataplane)

    custom = _FakeCustom()
    monkeypatch.setattr(P, "get_settings", _kube_settings)
    monkeypatch.setattr(P, "_custom", lambda: custom)

    from priva_control_panel.routers import admin as mod
    nudges: list[str] = []

    async def _nudge(account_id, username=""):
        nudges.append(account_id)

    monkeypatch.setattr(mod, "nudge_reconcile", _nudge)

    app = FastAPI()
    app.include_router(mod.router)
    app.dependency_overrides[mod.require_admin] = lambda: ADMIN

    alice = dataplane.accounts.create("alice", "pw", "admin").account_id
    bob = dataplane.accounts.create("bob", "pw").account_id

    with TestClient(app) as http:
        yield SimpleNamespace(http=http, dataplane=dataplane, custom=custom,
                              nudges=nudges, alice=alice, bob=bob)

    server.stop(None)
    repo.close()
    _cache.clear()


def test_disable_then_enable_round_trips_status_and_desired_state(harness):
    body = harness.http.post("/api/admin/users/bob/disable").json()
    assert body["status"] == "disabled"
    assert body["memory_mb"]  # same shape as list/update: effective resource spec joined
    assert harness.dataplane.accounts.get_by_username("bob").status == "disabled"
    assert harness.custom.patches[-1] == (
        harness.bob, {"spec": {"desiredState": "offboarding"}})
    assert harness.nudges == [harness.bob]

    body = harness.http.post("/api/admin/users/bob/enable").json()
    assert body["status"] == "active"
    assert harness.dataplane.accounts.get_by_username("bob").status == "active"
    assert harness.custom.patches[-1] == (harness.bob, {"spec": {"desiredState": "active"}})


def test_disable_without_a_cr_still_persists_the_status(harness):
    harness.custom.missing.add(harness.bob)

    assert harness.http.post("/api/admin/users/bob/disable").status_code == 200
    assert harness.dataplane.accounts.get_by_username("bob").status == "disabled"


def test_disable_guards(harness):
    assert harness.http.post("/api/admin/users/nobody/disable").status_code == 404
    assert harness.http.post("/api/admin/users/root/disable").status_code == 400   # self
    assert harness.http.post("/api/admin/users/alice/disable").status_code == 400  # last admin
    assert harness.dataplane.accounts.get_by_username("alice").status == "active"


def test_delete_tombstones_the_row_and_drops_the_cr(harness):
    r = harness.http.delete("/api/admin/users/bob")

    assert r.status_code == 202
    assert r.json() == {"status": "purging", "account_id": harness.bob}
    # The row survives as a tombstone; the sweep reaps it once the CR is gone.
    assert harness.dataplane.accounts.get_by_username("bob").status == "purged"
    assert harness.custom.deleted == [harness.bob]
    assert harness.nudges == [harness.bob]


def test_delete_is_retryable_after_the_cr_is_already_gone(harness):
    assert harness.http.delete("/api/admin/users/bob").status_code == 202
    assert harness.http.delete("/api/admin/users/bob").status_code == 202
    assert harness.dataplane.accounts.get_by_username("bob").status == "purged"


def test_delete_keeps_the_last_admin_guard(harness):
    assert harness.http.delete("/api/admin/users/alice").status_code == 400
    assert harness.dataplane.accounts.get_by_username("alice").status == "active"


def test_a_purged_account_can_never_be_enabled(harness):
    harness.http.delete("/api/admin/users/bob")

    assert harness.http.post("/api/admin/users/bob/enable").status_code == 400
    assert harness.http.post("/api/admin/users/bob/disable").status_code == 400
    assert harness.dataplane.accounts.get_by_username("bob").status == "purged"


# --- provisioner sweep (no server, no cluster) ------------------------------

def _sync_fakes(monkeypatch, users, tenants):
    import priva_common.dataplane as dataplane
    import priva_common.user_store as user_store

    defaults = SimpleNamespace(
        idle_grace_seconds=1800, min_alive_after_wake_seconds=1800,
        cpu_cores=1.0, memory_mb=2048, storage_gb=10,
        terminal_resource_percent=25, terminal_max_sessions=2,
        terminal_idle_timeout_seconds=1800, terminal_max_lifetime_seconds=14400,
        terminal_scale_down_grace_seconds=120,
    )
    reaped: list[str] = []
    dp = SimpleNamespace(
        runner_defaults=SimpleNamespace(get=lambda: defaults),
        resource_specs=SimpleNamespace(list=lambda: []),
        accounts=SimpleNamespace(delete=reaped.append),
    )
    monkeypatch.setattr(dataplane, "get_client", lambda: dp)
    monkeypatch.setattr(user_store, "get_user_store",
                        lambda: SimpleNamespace(list_users=lambda: users))
    monkeypatch.setattr(P, "list_tenants", lambda: tenants)
    return reaped


def _tenant(account_id, username, desired_state="active", runtime_defaults=None):
    return {"metadata": {"name": account_id},
            "spec": {"accountId": account_id, "username": username,
                     "desiredState": desired_state,
                     "runtimeDefaults": runtime_defaults}}


def _user(account_id, username, status):
    return SimpleNamespace(account_id=account_id, username=username, status=status,
                           agent_runner_type="auto_scale")


def test_sync_reaps_the_tombstone_row_once_the_cr_is_gone(monkeypatch):
    reaped = _sync_fakes(monkeypatch, [_user("acct-1", "alice", "purged")], [])
    monkeypatch.setattr(P, "ensure_tenant", lambda *a, **k: pytest.fail("resurrected"))

    result = P.sync_all_tenants()

    assert reaped == ["acct-1"]
    assert result["purged"] == 1 and result["created"] == 0


def test_sync_reissues_the_cr_delete_while_the_tombstone_still_has_one(monkeypatch):
    reaped = _sync_fakes(monkeypatch, [_user("acct-1", "alice", "purged")],
                         [_tenant("acct-1", "alice")])
    monkeypatch.setattr(P, "ensure_tenant", lambda *a, **k: pytest.fail("resurrected"))
    deleted: list[str] = []
    monkeypatch.setattr(P, "delete_tenant", deleted.append)

    result = P.sync_all_tenants()

    assert deleted == ["acct-1"]
    assert reaped == []  # the row outlives the CR
    assert result["purged"] == 0


def test_sync_converges_desired_state_for_a_disabled_account(monkeypatch):
    _sync_fakes(monkeypatch, [_user("acct-1", "alice", "disabled")],
                [_tenant("acct-1", "alice", runtime_defaults=P._runtime_defaults_spec(
                    SimpleNamespace(
                        idle_grace_seconds=1800, min_alive_after_wake_seconds=1800,
                        cpu_cores=1.0, memory_mb=2048, storage_gb=10,
                        terminal_resource_percent=25, terminal_max_sessions=2,
                        terminal_idle_timeout_seconds=1800,
                        terminal_max_lifetime_seconds=14400,
                        terminal_scale_down_grace_seconds=120,
                    )))])
    states: list[tuple[str, str]] = []
    monkeypatch.setattr(P, "set_tenant_desired_state",
                        lambda aid, state: states.append((aid, state)))

    result = P.sync_all_tenants()

    assert states == [("acct-1", "offboarding")]
    assert result["repaired"] == 1


def test_sync_does_not_provision_a_disabled_account_without_a_cr(monkeypatch):
    _sync_fakes(monkeypatch, [_user("acct-1", "alice", "disabled")], [])
    monkeypatch.setattr(P, "ensure_tenant", lambda *a, **k: pytest.fail("provisioned"))

    assert P.sync_all_tenants()["skipped"] == 1


def test_ensure_tenant_creates_a_non_active_account_quiesced(monkeypatch):
    created: list[dict] = []
    custom = SimpleNamespace(
        create_namespaced_custom_object=lambda g, v, ns, p, body: created.append(body))
    monkeypatch.setattr(P, "get_settings", _kube_settings)
    monkeypatch.setattr(P, "_custom", lambda: custom)

    P.ensure_tenant("acct-1", "alice", status="disabled",
                    runtime_defaults={"idleGraceSeconds": 1800})

    assert created[0]["spec"]["desiredState"] == "offboarding"


def test_delete_tenant_treats_an_absent_cr_as_success(monkeypatch):
    custom = _FakeCustom()
    custom.missing.add("acct-1")
    monkeypatch.setattr(P, "get_settings", _kube_settings)
    monkeypatch.setattr(P, "_custom", lambda: custom)

    P.delete_tenant("acct-1")

    assert custom.deleted == []


# --- access revocation (no expiry wait) -------------------------------------

def _auth_settings(global_api_key: str = ""):
    return SimpleNamespace(auth=SimpleNamespace(
        jwt_secret="s3cret", jwt_expire_hours=1, global_api_key=global_api_key,
        enable_anonymous=False, admins=[]))


def _frozen_store(user):
    return SimpleNamespace(get_user=lambda username: user,
                           find_by_api_key=lambda key: user if key.startswith("sk-") else None,
                           has_users=lambda: True)


def test_disabling_revokes_already_issued_tokens(monkeypatch):
    from priva_control_panel.services import auth as A

    frozen = UserRecord(username="bob", password_hash="x", account_id="acct-1",
                        status="disabled")
    monkeypatch.setattr(A, "get_settings", _auth_settings)
    monkeypatch.setattr(A, "get_user_store", lambda: _frozen_store(frozen))

    for token in (A.create_jwt("bob", "user"), "sk-per-user-api-key"):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(A.authenticate_raw_token(token))
        assert exc.value.status_code == 403


def test_the_global_api_key_still_resolves_a_frozen_account(monkeypatch):
    from priva_control_panel.services import auth as A

    frozen = UserRecord(username="bob", password_hash="x", account_id="acct-1",
                        status="disabled")
    monkeypatch.setattr(A, "get_settings", lambda: _auth_settings("global-key"))
    monkeypatch.setattr(A, "get_user_store", lambda: _frozen_store(frozen))

    user = asyncio.run(A.authenticate_raw_token("global-key", "bob"))

    assert user.username == "bob" and user.role == "admin"


def test_login_refuses_a_frozen_account(monkeypatch, tmp_path):
    from priva_control_panel.routers import auth as R

    monkeypatch.setenv("PRIVA_HOME", str(tmp_path))
    frozen = UserRecord(username="bob", password_hash="x", account_id="acct-1",
                        status="disabled")
    monkeypatch.setattr(R, "get_user_store", lambda: SimpleNamespace(
        verify_password=lambda username, password: True,
        get_user=lambda username: frozen))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(R.login(LoginRequest(username="bob", password="pw")))

    assert exc.value.status_code == 403
