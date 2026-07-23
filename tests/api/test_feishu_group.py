"""Feishu group-chat participation (feat_feishu_DM.md §5) — Phase 2 slice.

Covers the three layers the feature spans:
- data-spine: channel_platform_config singleton, effective_group_enabled
  composition, digest re-arm on user toggle AND admin global flip, per-chat
  channel_binding semantics.
- control-panel: user group toggle + globally-disabled read view; admin
  /channel-platform routes.
- connector: worker group gate (disabled / no-@ / allowed), access-gate bypass
  in groups, per-chat sessions with per-group "/new", mention stripping.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_CONN_SRC = os.path.join(_REPO, "services", "channel-connector", "src")
if _CONN_SRC not in sys.path:
    sys.path.insert(0, _CONN_SRC)

from priva_channel_connector.lark_ws import _strip_mention_placeholders  # noqa: E402
from priva_channel_connector.router import SessionRouter  # noqa: E402
from priva_channel_connector.sse import RunOutcome  # noqa: E402
from priva_channel_connector.transport import InboundMessage  # noqa: E402
from priva_channel_connector.worker import AppWorker  # noqa: E402

from priva_common.dataplane import FeishuChannelConfigRecord  # noqa: E402

from tests.api.test_connector import (  # noqa: E402
    FakeClient,
    FakeDialer,
    _secret,
    _transport_factory,
)


# =============================================================================
# data-spine + control-panel slice (in-process transport over a sqlite tmp DB)
# =============================================================================

@pytest.fixture(scope="module")
def app_client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("feishu-group")
    os.environ["PRIVA_DATASPINE__BACKEND"] = "sqlite"
    os.environ["PRIVA_DATASPINE__SQLITE_PATH"] = str(tmp / "spine.db")
    os.environ["PRIVA_DATASPINE__TRANSPORT"] = "in_process"
    os.environ["PRIVA_HOME"] = str(tmp / "home")

    from priva_common.config import get_settings
    if hasattr(get_settings, "cache_clear"):
        get_settings.cache_clear()

    from priva_data_spine.service import compose
    compose()

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
    # give alice a working bot so digest assertions run on an effective row
    client.put("/api/auth/me/feishu-config", headers=alice_h,
               json={"app_id": "cli_alice", "app_secret": "sec", "user_enabled": True})
    return client, admin_h, alice_h


def _spine():
    from priva_common.dataplane import get_client
    return get_client()


def test_platform_config_defaults_and_admin_flip(app_client):
    client, admin_h, _ = app_client
    d = client.get("/api/admin/channel-platform", headers=admin_h).json()
    assert d["group_chat_disabled"] is False  # never-written singleton reads as off
    d = client.put("/api/admin/channel-platform", headers=admin_h,
                   json={"group_chat_disabled": True}).json()
    assert d["group_chat_disabled"] is True and d["updated_by"] == "boss"
    d = client.put("/api/admin/channel-platform", headers=admin_h,
                   json={"group_chat_disabled": False}).json()
    assert d["group_chat_disabled"] is False


def test_platform_config_is_admin_only(app_client):
    client, _, alice_h = app_client
    assert client.get("/api/admin/channel-platform", headers=alice_h).status_code == 403
    assert client.put("/api/admin/channel-platform", headers=alice_h,
                      json={"group_chat_disabled": True}).status_code == 403


def test_group_toggle_composition_and_digest_rearm(app_client):
    client, admin_h, alice_h = app_client
    fc = _spine().feishu_configs

    d0 = client.get("/api/auth/me/feishu-config", headers=alice_h).json()
    assert d0["group_chat_enabled"] is False
    assert d0["effective_group_enabled"] is False
    digest0 = fc.get(d0["account_id"]).desired_digest

    # user opt-in → effective on (global switch off) + digest changed (re-arm)
    d1 = client.put("/api/auth/me/feishu-config", headers=alice_h,
                    json={"group_chat_enabled": True}).json()
    assert d1["group_chat_enabled"] is True
    assert d1["effective_group_enabled"] is True
    assert d1["group_chat_globally_disabled"] is False
    digest1 = fc.get(d0["account_id"]).desired_digest
    assert digest1 != digest0

    # admin global flip → effective off for everyone + digest changed again
    client.put("/api/admin/channel-platform", headers=admin_h, json={"group_chat_disabled": True})
    d2 = client.get("/api/auth/me/feishu-config", headers=alice_h).json()
    assert d2["group_chat_enabled"] is True          # user's own bit untouched
    assert d2["effective_group_enabled"] is False    # composed off
    assert d2["group_chat_globally_disabled"] is True
    digest2 = fc.get(d0["account_id"]).desired_digest
    assert digest2 != digest1

    # while globally off, the user toggling their bit must NOT bounce the WS
    client.put("/api/auth/me/feishu-config", headers=alice_h, json={"group_chat_enabled": False})
    assert fc.get(d0["account_id"]).desired_digest == digest2
    client.put("/api/auth/me/feishu-config", headers=alice_h, json={"group_chat_enabled": True})
    assert fc.get(d0["account_id"]).desired_digest == digest2

    # admin re-enable → effective on again, digest back in play
    client.put("/api/admin/channel-platform", headers=admin_h, json={"group_chat_disabled": False})
    d3 = client.get("/api/auth/me/feishu-config", headers=alice_h).json()
    assert d3["effective_group_enabled"] is True
    assert fc.get(d0["account_id"]).desired_digest == digest1  # same desired state as before


def test_global_flip_skips_rows_without_group_optin(app_client):
    client, admin_h, alice_h = app_client
    fc = _spine().feishu_configs
    account_id = client.get("/api/auth/me/feishu-config", headers=alice_h).json()["account_id"]

    # group off for alice → the global switch is a no-op for her digest
    client.put("/api/auth/me/feishu-config", headers=alice_h, json={"group_chat_enabled": False})
    digest = fc.get(account_id).desired_digest
    client.put("/api/admin/channel-platform", headers=admin_h, json={"group_chat_disabled": True})
    assert fc.get(account_id).desired_digest == digest
    client.put("/api/admin/channel-platform", headers=admin_h, json={"group_chat_disabled": False})
    assert fc.get(account_id).desired_digest == digest


def test_per_chat_bindings_in_spine(app_client):
    _ = app_client
    spine = _spine()
    account_id = spine.accounts.get_by_username("alice").account_id

    b1 = spine.bindings.bind(account_id, "sess-group-1", "oc_g1")
    b2 = spine.bindings.bind(account_id, "sess-group-2", "oc_g2")  # coexists: per-chat index
    assert b1.binding_id != b2.binding_id
    assert {b.feishu_chat_id for b in spine.bindings.list_bindings(account_id)} == {"oc_g1", "oc_g2"}

    # rebind targets ONLY its chat's row
    spine.bindings.rebind(account_id, None, "oc_g1")
    by_chat = {b.feishu_chat_id: b.session_uuid for b in spine.bindings.list_bindings(account_id)}
    assert by_chat == {"oc_g1": None, "oc_g2": "sess-group-2"}


# =============================================================================
# connector — worker group gate + per-chat sessions
# =============================================================================

def _group_cfg(account_id, *, group=True, owner="", mode="owner_only"):
    return FeishuChannelConfigRecord(
        account_id=account_id, app_id="cli_x", has_app_secret=True,
        user_enabled=True, effective_enabled=True,
        single_chat_access_mode=mode, owner_union_id=owner,
        group_chat_enabled=group, effective_group_enabled=group,
        desired_digest="d1")


def _group_msg(account_id, text, chat="oc_g1", *, mentioned=True, union="on_visitor"):
    import uuid
    return InboundMessage(account_id=account_id, sender_open_id="ou_member", chat_id=chat,
                          text=text, message_id="om_" + uuid.uuid4().hex[:6],
                          sender_union_id=union, chat_type="group", mentioned=mentioned)


def _group_worker(cfg, dialer=None):
    client = FakeClient()
    created = []
    dialer = dialer or FakeDialer(RunOutcome(session_id="s-g", text="ok"))
    worker = AppWorker(client, dialer, SessionRouter(client), cfg,
                       _secret(cfg.account_id), client.accounts.get(cfg.account_id),
                       _transport_factory(created))
    return worker, created, dialer, client


def test_group_message_skipped_when_group_disabled():
    async def go():
        worker, created, dialer, _ = _group_worker(_group_cfg("A", group=False))
        await worker.start()
        t = created[0]
        await t.inject(_group_msg("A", "hello"))
        assert dialer.calls == []
        assert t.sent == [] and t.cards == [] and t.reactions == []

    asyncio.run(go())


def test_group_message_skipped_without_mention():
    async def go():
        worker, created, dialer, _ = _group_worker(_group_cfg("A"))
        await worker.start()
        t = created[0]
        await t.inject(_group_msg("A", "chatter between humans", mentioned=False))
        assert dialer.calls == []
        assert t.reactions == []

    asyncio.run(go())


def test_group_mention_runs_and_bypasses_single_chat_gate():
    async def go():
        # owner bound + owner_only: the same sender would be REJECTED in p2p, but
        # group access is granted by group membership (拉群即授权, ruling #5).
        cfg = _group_cfg("A", owner="on_owner", mode="owner_only")
        worker, created, dialer, client = _group_worker(cfg)
        await worker.start()
        t = created[0]
        await t.inject(_group_msg("A", "帮我总结", union="on_visitor"))
        assert len(dialer.calls) == 1
        assert dialer.calls[0]["prompt"] == "帮我总结"
        # session committed onto THIS group's binding
        bindings = client.bindings.list_bindings("A")
        assert [(b.feishu_chat_id, b.session_uuid) for b in bindings] == [("oc_g1", "s-g")]

        # sanity: identical sender in p2p IS rejected by the owner gate
        p2p = InboundMessage(account_id="A", sender_open_id="ou_member", chat_id="oc_p2p",
                             text="hi", message_id="om_p", sender_union_id="on_visitor",
                             chat_type="p2p")
        await t.inject(p2p)
        assert len(dialer.calls) == 1  # no new dial

    asyncio.run(go())


def test_per_group_sessions_and_scoped_new():
    async def go():
        class SeqDialer(FakeDialer):
            def __init__(self):
                super().__init__(RunOutcome())
                self._n = 0

            async def run(self, account_id, username, *, prompt, session_id=None, model=None,
                          images=None, do_wake=True, state=None, on_permission=None):
                self._n += 1
                self.calls.append({"prompt": prompt, "session_id": session_id})
                from priva_channel_connector.sse import StreamState
                state = state or StreamState()
                state.session_id = f"sess-{self._n}"
                state.timeline.append("ok")
                return state

        worker, created, dialer, client = _group_worker(_group_cfg("A"), SeqDialer())
        await worker.start()
        t = created[0]

        await t.inject(_group_msg("A", "hi", chat="oc_g1"))       # → sess-1 on g1
        await t.inject(_group_msg("A", "hi", chat="oc_g2"))       # → sess-2 on g2 (independent)
        by_chat = {b.feishu_chat_id: b.session_uuid for b in client.bindings.list_bindings("A")}
        assert by_chat == {"oc_g1": "sess-1", "oc_g2": "sess-2"}
        # second turn in g1 resumes g1's session, not g2's
        await t.inject(_group_msg("A", "again", chat="oc_g1"))
        assert dialer.calls[-1]["session_id"] == "sess-1"

        # "/new" in g1 (typed as "@bot /new" → transport strips to "/new") detaches g1 ONLY
        await t.inject(_group_msg("A", "/new", chat="oc_g1"))
        by_chat = {b.feishu_chat_id: b.session_uuid for b in client.bindings.list_bindings("A")}
        assert by_chat["oc_g1"] is None
        assert by_chat["oc_g2"] == "sess-2"

    asyncio.run(go())


def test_link_code_not_processed_in_groups():
    async def go():
        worker, created, dialer, client = _group_worker(_group_cfg("A"))
        await worker.start()
        t = created[0]
        await t.inject(_group_msg("A", "/link ABC234"))
        # group path never reaches _handle_link — the text rides into a normal run
        assert client.feishu_configs.bind_calls == []
        assert len(dialer.calls) == 1 and dialer.calls[0]["prompt"] == "/link ABC234"

    asyncio.run(go())


# =============================================================================
# connector — mention placeholder stripping (transport-level)
# =============================================================================

class _Mention:
    def __init__(self, key, name):
        self.key, self.name = key, name


def test_strip_leading_bot_mention_keeps_command():
    assert _strip_mention_placeholders("@_user_1 /new", [_Mention("@_user_1", "小助手")]) == "/new"


def test_strip_inner_mention_becomes_readable_name():
    out = _strip_mention_placeholders(
        "@_user_1 帮 @_user_2 排个日程", [_Mention("@_user_1", "小助手"), _Mention("@_user_2", "Alice")])
    assert out == "帮 @Alice 排个日程"


def test_strip_mention_without_name_is_dropped():
    assert _strip_mention_placeholders("hi @_user_1 there", [_Mention("@_user_1", "")]) == "hi  there".strip()


def test_strip_no_mentions_passthrough():
    assert _strip_mention_placeholders("@_user_1 raw", []) == "@_user_1 raw"


def test_dispatch_group_text_sets_mentioned_and_strips(monkeypatch):
    from priva_channel_connector.lark_ws import LarkTransport

    received = []

    async def on_msg(m):
        received.append(m)

    t = LarkTransport("acct", "app", "secret", "feishu", on_msg)
    monkeypatch.setattr(LarkTransport, "_get_chat_name_sync", lambda self, cid: "群")

    class _Obj:
        pass

    data = _Obj()
    data.event = _Obj()
    m = _Obj()
    m.message_type = "text"
    m.content = json.dumps({"text": "@_user_1 帮我查一下"})
    m.chat_id = "oc_g"
    m.message_id = "om_g"
    m.chat_type = "group"
    m.mentions = [_Mention("@_user_1", "小助手")]
    data.event.message = m
    data.event.sender = _Obj()
    data.event.sender.sender_id = _Obj()
    data.event.sender.sender_id.open_id = "ou_s"

    async def main():
        t._loop = asyncio.get_running_loop()
        t._dispatch(data)
        await asyncio.sleep(0.05)

    asyncio.run(main())
    assert len(received) == 1
    got = received[0]
    assert got.chat_type == "group" and got.mentioned is True
    assert got.text == "帮我查一下"


# =============================================================================
# session list — display metadata (settings-page 已激活会话)
# =============================================================================

def test_spine_binding_set_display_roundtrip(app_client):
    _ = app_client
    spine = _spine()
    account_id = spine.accounts.get_by_username("alice").account_id
    spine.bindings.bind(account_id, "sess-disp", "oc_disp")
    rec = spine.bindings.set_display(account_id, "oc_disp", chat_type="group", chat_name="产品讨论组")
    assert rec.chat_type == "group" and rec.chat_name == "产品讨论组"
    # missing row → no-op, no crash
    spine.bindings.set_display(account_id, "oc_missing", chat_type="p2p", chat_name="x")
    got = {b.feishu_chat_id: (b.chat_type, b.chat_name) for b in spine.bindings.list_bindings(account_id)}
    assert got["oc_disp"] == ("group", "产品讨论组")
    assert "oc_missing" not in got


def test_sessions_endpoint_lists_active_first(app_client):
    client, _, alice_h = app_client
    spine = _spine()
    account_id = spine.accounts.get_by_username("alice").account_id
    spine.bindings.bind(account_id, "sess-active-1", "oc_s1")
    spine.bindings.set_display(account_id, "oc_s1", chat_type="p2p", chat_name="Derek")
    spine.bindings.bind(account_id, None, "oc_s2")  # reset chat (no session)
    spine.bindings.set_display(account_id, "oc_s2", chat_type="group", chat_name="测试群")

    r = client.get("/api/auth/me/feishu-sessions", headers=alice_h)
    assert r.status_code == 200, r.text
    rows = {s["chat_id"]: s for s in r.json()["sessions"]}
    assert rows["oc_s1"]["session_id"] == "sess-active-1"
    assert rows["oc_s1"]["chat_type"] == "p2p" and rows["oc_s1"]["chat_name"] == "Derek"
    assert rows["oc_s2"]["session_id"] is None
    assert rows["oc_s2"]["chat_type"] == "group" and rows["oc_s2"]["chat_name"] == "测试群"
    # active rows sort ahead of reset rows
    order = [s["chat_id"] for s in r.json()["sessions"]]
    assert order.index("oc_s1") < order.index("oc_s2")


def test_worker_stamps_display_after_run():
    async def go():
        worker, created, dialer, client = _group_worker(_group_cfg("A"))
        await worker.start()
        t = created[0]
        t.display_names["oc_g1"] = "冲浪群"           # group name resolves via chat_id
        t.display_names["ou_member"] = "Alice"        # p2p name resolves via sender open_id
        await t.inject(_group_msg("A", "hi", chat="oc_g1"))
        p2p = InboundMessage(account_id="A", sender_open_id="ou_member", chat_id="oc_p1",
                             text="hello", message_id="om_pp", sender_union_id="on_x",
                             chat_type="p2p")
        await t.inject(p2p)
        by_chat = {b.feishu_chat_id: (b.chat_type, b.chat_name, b.session_uuid)
                   for b in client.bindings.list_bindings("A")}
        assert by_chat["oc_g1"] == ("group", "冲浪群", "s-g")
        assert by_chat["oc_p1"][0] == "p2p" and by_chat["oc_p1"][1] == "Alice"

    asyncio.run(go())
