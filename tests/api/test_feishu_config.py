"""Feishu per-account bot config — control-panel REST + data-plane slice.

Exercises the write-authority split and the write-only-secret contract over HTTP
on the in-process transport:
- user self-serve (/api/auth/me/feishu-config) is the ONLY writer of credentials
  + user_enabled + behaviour; app_secret never comes back in cleartext.
- admin (/api/admin/users/{u}/feishu-config) writes ONLY admin_disabled; the
  kill-switch is a hard floor the user cannot lift.
- extra/foreign fields sent to either endpoint are ignored (DTO-scoped authority).
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="module")
def app_client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("feishu-cfg")
    os.environ["PRIVA_DATASPINE__BACKEND"] = "sqlite"
    os.environ["PRIVA_DATASPINE__SQLITE_PATH"] = str(tmp / "spine.db")
    os.environ["PRIVA_DATASPINE__TRANSPORT"] = "in_process"
    os.environ["PRIVA_HOME"] = str(tmp / "home")

    from priva_common.config import get_settings
    if hasattr(get_settings, "cache_clear"):
        get_settings.cache_clear()

    from priva_data_spine.service import compose
    compose()  # register the in-process data-plane client

    from fastapi.testclient import TestClient
    from priva_control_panel.app import create_app
    from priva_common.user_store import get_user_store
    from priva_control_panel.services.auth import create_jwt

    store = get_user_store()
    store.create_user("boss", password="pw", role="admin")
    store.create_user("alice", password="pw", role="user")

    client = TestClient(create_app())
    admin_h = {"Authorization": f"Bearer {create_jwt('boss', 'admin')}"}
    alice_h = {"Authorization": f"Bearer {create_jwt('alice', 'user')}"}
    return client, admin_h, alice_h


def test_unconfigured_defaults(app_client):
    client, _, alice_h = app_client
    r = client.get("/api/auth/me/feishu-config", headers=alice_h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["app_secret_set"] is False
    assert d["effective_enabled"] is False
    assert d["connection"]["conn_status"] == "disabled"
    assert "app_secret" not in d  # write-only: never present


def test_user_self_serve_write_only_secret(app_client):
    client, _, alice_h = app_client
    r = client.put("/api/auth/me/feishu-config", headers=alice_h,
                   json={"app_id": "cli_alice", "app_secret": "super-secret-value", "user_enabled": True})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["app_id"] == "cli_alice"
    assert d["app_secret_set"] is True
    assert "app_secret" not in d                 # the value is NEVER returned
    assert d["effective_enabled"] is True        # user_enabled + creds present, not admin-disabled
    # GET agrees, still no plaintext
    d2 = client.get("/api/auth/me/feishu-config", headers=alice_h).json()
    assert d2["app_secret_set"] is True and "app_secret" not in d2


def test_empty_secret_rejected(app_client):
    client, _, alice_h = app_client
    r = client.put("/api/auth/me/feishu-config", headers=alice_h, json={"app_secret": ""})
    assert r.status_code == 400


def test_admin_kill_switch_is_a_hard_floor(app_client):
    client, admin_h, alice_h = app_client
    # admin disables alice's bot
    r = client.put("/api/admin/users/alice/feishu-config", headers=admin_h, json={"admin_disabled": True})
    assert r.status_code == 200, r.text
    assert r.json()["admin_disabled"] is True
    assert r.json()["effective_enabled"] is False
    # alice re-asserting user_enabled cannot lift it
    d = client.put("/api/auth/me/feishu-config", headers=alice_h, json={"user_enabled": True}).json()
    assert d["admin_disabled"] is True and d["effective_enabled"] is False
    # admin clears the kill-switch -> bot resumes (user still enabled)
    d2 = client.put("/api/admin/users/alice/feishu-config", headers=admin_h, json={"admin_disabled": False}).json()
    assert d2["admin_disabled"] is False and d2["effective_enabled"] is True


def test_admin_cannot_write_credentials(app_client):
    client, admin_h, _ = app_client
    before = client.get("/api/admin/users/alice/feishu-config", headers=admin_h).json()["app_id"]
    # foreign fields (app_id/app_secret/user_enabled) are not on the admin DTO -> ignored
    client.put("/api/admin/users/alice/feishu-config", headers=admin_h,
               json={"app_id": "cli_HIJACK", "app_secret": "x", "user_enabled": False})
    after = client.get("/api/admin/users/alice/feishu-config", headers=admin_h).json()["app_id"]
    assert after == before == "cli_alice"  # admin never touched the credential


def test_user_cannot_set_admin_disabled(app_client):
    client, admin_h, alice_h = app_client
    # ensure not disabled
    client.put("/api/admin/users/alice/feishu-config", headers=admin_h, json={"admin_disabled": False})
    # alice sends admin_disabled (foreign to her DTO) -> ignored
    client.put("/api/auth/me/feishu-config", headers=alice_h, json={"admin_disabled": True})
    d = client.get("/api/auth/me/feishu-config", headers=alice_h).json()
    assert d["admin_disabled"] is False


def test_admin_get_404_unknown_user(app_client):
    client, admin_h, _ = app_client
    assert client.get("/api/admin/users/ghost/feishu-config", headers=admin_h).status_code == 404


def test_secret_clear(app_client):
    client, admin_h, alice_h = app_client
    client.put("/api/auth/me/feishu-config", headers=alice_h, json={"app_secret": "to-be-cleared"})
    assert client.get("/api/auth/me/feishu-config", headers=alice_h).json()["app_secret_set"] is True
    client.put("/api/auth/me/feishu-config", headers=alice_h, json={"app_secret": "__clear__"})
    assert client.get("/api/auth/me/feishu-config", headers=alice_h).json()["app_secret_set"] is False


# --- owner link-code binding (feat_feishu_DM.md §4) --------------------------
def _fc():
    from priva_common.dataplane import get_client
    return get_client().feishu_configs


def test_link_code_mint_bind_lifecycle(app_client):
    client, _, alice_h = app_client
    r = client.post("/api/auth/me/feishu-link-code", headers=alice_h)
    assert r.status_code == 200, r.text
    code, expires = r.json()["code"], r.json()["expires_at"]
    assert len(code) == 6 and expires
    d = client.get("/api/auth/me/feishu-config", headers=alice_h).json()
    acc = d["account_id"]
    assert d["owner_bound"] is False
    assert code not in str(d)  # plaintext code appears ONLY in the mint response

    fc = _fc()
    digest_before = fc.get(acc).desired_digest
    assert fc.bind_owner_with_code(acc, "WRONG0", "on_user1", "ou_1") is False
    assert fc.bind_owner_with_code(acc, code.lower(), "on_user1", "ou_1") is True  # case-insensitive
    assert fc.bind_owner_with_code(acc, code, "on_user2", "ou_2") is False         # single-use

    d = client.get("/api/auth/me/feishu-config", headers=alice_h).json()
    assert d["owner_bound"] is True and d["owner_bound_at"]
    assert "on_user1" not in str(d)  # only the masked form crosses the HTTP boundary
    assert d["owner_union_id_masked"]
    rec = fc.get(acc)
    assert rec.owner_union_id == "on_user1" and rec.owner_open_id == "ou_1"
    assert rec.desired_digest != digest_before  # bind re-arms the connector


def test_link_code_expired_rejected(app_client):
    client, _, alice_h = app_client
    code = client.post("/api/auth/me/feishu-link-code", headers=alice_h).json()["code"]
    acc = client.get("/api/auth/me/feishu-config", headers=alice_h).json()["account_id"]
    fc = _fc()
    fc.repo.feishu_upsert(acc, {"link_code_expires_at": "2020-01-01T00:00:00+00:00"})
    assert fc.bind_owner_with_code(acc, code, "on_late", "ou_l") is False


def test_link_code_remint_invalidates_previous(app_client):
    client, _, alice_h = app_client
    first = client.post("/api/auth/me/feishu-link-code", headers=alice_h).json()["code"]
    second = client.post("/api/auth/me/feishu-link-code", headers=alice_h).json()["code"]
    acc = client.get("/api/auth/me/feishu-config", headers=alice_h).json()["account_id"]
    fc = _fc()
    if first != second:  # 1-in-32^6 collision guard
        assert fc.bind_owner_with_code(acc, first, "on_x", "ou_x") is False
    assert fc.bind_owner_with_code(acc, second, "on_x", "ou_x") is True


def test_unbind_owner_clears_and_rearms(app_client):
    client, _, alice_h = app_client
    code = client.post("/api/auth/me/feishu-link-code", headers=alice_h).json()["code"]
    acc = client.get("/api/auth/me/feishu-config", headers=alice_h).json()["account_id"]
    fc = _fc()
    assert fc.bind_owner_with_code(acc, code, "on_user9", "ou_9") is True
    digest_bound = fc.get(acc).desired_digest

    r = client.delete("/api/auth/me/feishu-owner", headers=alice_h)
    assert r.status_code == 200, r.text
    assert r.json()["owner_bound"] is False
    rec = fc.get(acc)
    assert rec.owner_union_id == "" and rec.owner_bound_at is None
    assert rec.desired_digest != digest_bound  # unbind re-arms too
