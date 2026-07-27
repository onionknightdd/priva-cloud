"""Owner link-code binding + access gate (feat_feishu_DM.md §4, Phase 1).

Covers: /link command parsing, the access-gate matrix (mode × bound/unbound ×
sender), the worker's bind flow (bypasses the gate, replies OK/FAIL, never dials
the agent), and rejection of non-owner DMs once bound.
"""

import asyncio
import os
import sys

# same shim as test_connector.py
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_CONN_SRC = os.path.join(_REPO, "services", "channel-connector", "src")
if _CONN_SRC not in sys.path:
    sys.path.insert(0, _CONN_SRC)

from priva_channel_connector.router import SessionRouter, match_link_code  # noqa: E402
from priva_channel_connector.sse import RunOutcome  # noqa: E402
from priva_channel_connector.transport import InboundMessage  # noqa: E402
from priva_channel_connector.worker import AppWorker, _LINK_FAIL  # noqa: E402

from priva_common.dataplane import FeishuChannelConfigRecord  # noqa: E402

from tests.api.test_connector import (  # noqa: E402
    FakeClient,
    FakeDialer,
    _secret,
    _transport_factory,
)

OWNER = "on_owner_union"
OTHER = "on_other_union"


def _msg(text, union_id, account="A"):
    return InboundMessage(account_id=account, sender_open_id="ou_x", chat_id="oc_1",
                          text=text, message_id="om_1", sender_union_id=union_id)


def _cfg(mode="owner_only", owner="", allowed="[]", reject=""):
    return FeishuChannelConfigRecord(
        account_id="A", app_id="cli_x", has_app_secret=True, user_enabled=True,
        effective_enabled=True, single_chat_access_mode=mode, desired_digest="d1",
        owner_union_id=owner, allowed_union_ids=allowed, reject_message=reject)


# --- /link parsing -----------------------------------------------------------
def test_match_link_code_variants():
    assert match_link_code("/link A7K2MQ") == "A7K2MQ"
    assert match_link_code("  /绑定 a7k2mq  ") == "A7K2MQ"  # alias + case-normalize
    assert match_link_code("/link") is None
    assert match_link_code("/linkA7K2MQ") is None
    assert match_link_code("帮我 /link A7K2MQ") is None      # must be the whole message
    assert match_link_code("/new") is None


# --- access gate matrix ------------------------------------------------------
def test_gate_unbound_allows_every_mode():
    r = SessionRouter(FakeClient())
    for mode in ("owner_only", "allowlist", "all"):
        assert r.access_allowed(_cfg(mode=mode), _msg("hi", OTHER)) is True


def test_gate_owner_only_bound():
    r = SessionRouter(FakeClient())
    cfg = _cfg(owner=OWNER)
    assert r.access_allowed(cfg, _msg("hi", OWNER)) is True
    assert r.access_allowed(cfg, _msg("hi", OTHER)) is False
    assert r.access_allowed(cfg, _msg("hi", "")) is False  # no union_id → not owner


def test_gate_allowlist_bound():
    r = SessionRouter(FakeClient())
    cfg = _cfg(mode="allowlist", owner=OWNER, allowed=f'["{OTHER}"]')
    assert r.access_allowed(cfg, _msg("hi", OWNER)) is True   # owner always in
    assert r.access_allowed(cfg, _msg("hi", OTHER)) is True   # listed
    assert r.access_allowed(cfg, _msg("hi", "on_stranger")) is False


def test_gate_all_mode_ignores_binding():
    r = SessionRouter(FakeClient())
    assert r.access_allowed(_cfg(mode="all", owner=OWNER), _msg("hi", OTHER)) is True


# --- worker flows ------------------------------------------------------------
def _worker(cfg, dialer):
    client = FakeClient()
    created = []
    worker = AppWorker(client, dialer, SessionRouter(client), cfg,
                       _secret("A"), client.accounts.get("A"), _transport_factory(created))
    return worker, client, created


def _inject(cfg, msg, *, bind_result=True):
    dialer = FakeDialer(RunOutcome(session_id="s", text="ok"))

    async def go():
        worker, client, created = _worker(cfg, dialer)
        client.feishu_configs.bind_result = bind_result
        await worker.start()
        await created[0].inject(msg)
        return dialer, client, created[0]

    return asyncio.run(go())


def test_link_dm_binds_and_replies_ok():
    # Sender is NOT the current owner — link must bypass the gate (re-bind path).
    dialer, client, t = _inject(_cfg(owner=OWNER), _msg("/link A7K2MQ", OTHER))
    assert client.feishu_configs.bind_calls == [("A", "A7K2MQ", OTHER, "ou_x")]
    # 回执 = 欢迎卡（§9.1），不再是纯文本
    assert t.sent == [] and len(t.cards) == 1
    assert "绑定成功" in t.cards[0][1]["header"]["title"]["content"]
    assert dialer.calls == []  # never enters the agent


def test_link_dm_invalid_code_replies_fail():
    dialer, client, t = _inject(_cfg(), _msg("/绑定 BADCOD", OTHER), bind_result=False)
    assert client.feishu_configs.bind_calls == [("A", "BADCOD", OTHER, "ou_x")]
    assert t.sent == [("oc_1", _LINK_FAIL)]
    assert dialer.calls == []


def test_link_dm_without_union_id_fails_without_rpc():
    dialer, client, t = _inject(_cfg(), _msg("/link A7K2MQ", ""))
    assert client.feishu_configs.bind_calls == []
    assert t.sent == [("oc_1", _LINK_FAIL)]


def test_bound_owner_only_rejects_stranger_with_message():
    dialer, _, t = _inject(_cfg(owner=OWNER, reject="仅限所有者使用"),
                           _msg("你好", OTHER))
    assert dialer.calls == []                      # gate blocked the run
    assert t.sent == [("oc_1", "仅限所有者使用")]  # reject_message sent
    assert t.cards == []


def test_bound_owner_only_allows_owner_run():
    dialer, _, _t = _inject(_cfg(owner=OWNER), _msg("你好", OWNER))
    assert len(dialer.calls) == 1
    assert dialer.calls[0]["prompt"] == "你好"
