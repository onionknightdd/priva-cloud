"""指令引导卡片（feat_feishu_DM.md §9.1）：`/link` 绑定成功的欢迎卡、`/help` 使用指南卡，
以及卡片按钮 == 用户手打该指令的等价链路。全对 fake：无 lark_oapi、无 pod、无集群。"""

import asyncio
import json
import os
import sys
import uuid

import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_CONN_SRC = os.path.join(_REPO, "services", "channel-connector", "src")
if _CONN_SRC not in sys.path:
    sys.path.insert(0, _CONN_SRC)

from priva_channel_connector import card_actions, menu_cards, worker as worker_mod  # noqa: E402
from priva_channel_connector.router import SessionRouter              # noqa: E402
from priva_channel_connector.sse import RunOutcome                    # noqa: E402
from priva_channel_connector.transport import InboundMessage          # noqa: E402
from priva_channel_connector.worker import AppWorker, get_worker      # noqa: E402

from priva_common.dataplane import FeishuChannelConfigRecord, FeishuSecretRecord  # noqa: E402

from .test_connector import FakeClient, FakeDialer, _transport_factory  # noqa: E402

_OWNER_OPEN = "ou_owner"
_OWNER_UNION = "on_owner"


def _cfg(account_id="A", *, owner=_OWNER_UNION, mode="owner_only", group=False):
    return FeishuChannelConfigRecord(
        account_id=account_id, app_id="cli_x", has_app_secret=True, user_enabled=True,
        effective_enabled=True, single_chat_access_mode=mode, owner_union_id=owner,
        group_chat_enabled=group, effective_group_enabled=group, desired_digest="d1")


def _secret(account_id="A"):
    return FeishuSecretRecord(account_id=account_id, app_id="cli_x", app_secret="s3cr3t",
                              domain="feishu")


def _msg(text, *, chat="oc_1", open_id=_OWNER_OPEN, union=_OWNER_UNION, chat_type="p2p"):
    return InboundMessage(account_id="A", sender_open_id=open_id, chat_id=chat, text=text,
                          message_id="om_" + uuid.uuid4().hex[:6], sender_union_id=union,
                          chat_type=chat_type)


async def _worker(client, dialer=None, *, cfg=None, created=None):
    created = [] if created is None else created
    w = AppWorker(client, dialer or FakeDialer(RunOutcome(session_id="s1", text="ok")),
                  SessionRouter(client), cfg or _cfg(), _secret(),
                  client.accounts.get("A"), _transport_factory(created))
    await w.start()
    return w, created[0]


def _buttons(card):
    return [e for e in card["body"]["elements"] if e.get("tag") == "button"]


def _value(button):
    return button["behaviors"][0]["value"]


# --- 渲染 -------------------------------------------------------------------
def test_welcome_card_shape():
    card = menu_cards.welcome_card(open_id="ou_x", union_id="on_x", max_images=5,
                                   max_image_bytes=5 * 1024 * 1024)
    assert card["schema"] == "2.0"
    assert card["header"]["template"] == "green" and "绑定成功" in card["header"]["title"]["content"]
    # 欢迎卡只带 🆕（裁定 2026-07-24：刚绑定，clear/compact 无意义）
    btns = _buttons(card)
    assert len(btns) == 1
    assert _value(btns[0]) == {"act": "menu", "cmd": "/new", "uid": "ou_x", "ct": "p2p", "un": "on_x"}
    body = " ".join(e.get("content", "") for e in card["body"]["elements"])
    assert "/help" in body and "5 张" in body and "5MB" in body
    assert "/clear" not in body and "/compact" not in body
    # 与使用指南卡同一套文案：无别名、同一句上下文说明
    assert "以下是特殊指令" in body and "不会出现在对话的上下文中" in body
    assert "别名" not in body and "独立会话" not in body


def test_help_card_shape():
    card = menu_cards.help_card(open_id="ou_x", chat_type="group", union_id="on_x")
    assert card["header"]["template"] == "blue" and "使用指南" in card["header"]["title"]["content"]
    cmds = [_value(b)["cmd"] for b in _buttons(card)]
    assert cmds == ["/new", "/clear", "/compact", "/skill"]   # 与自定义菜单同构
    assert all(_value(b)["ct"] == "group" for b in _buttons(card))
    body = " ".join(e.get("content", "") for e in card["body"]["elements"])
    assert "/compact" in body and "所有者" not in body   # 指南卡不含所有者行
    assert "会话 id" in body                             # /info 说明已更新
    assert "不会出现在对话的上下文中" in body            # 实测：/context 不累积进会话
    assert "5MB" in body


# --- /link 回执 -------------------------------------------------------------
def test_link_success_replies_with_welcome_card():
    async def go():
        client = FakeClient()
        w, t = await _worker(client)
        try:
            await t.inject(_msg("/link A7K2MQ", open_id="ou_new", union="on_new"))
        finally:
            await w.stop()
        assert client.feishu_configs.bind_calls == [("A", "A7K2MQ", "on_new", "ou_new")]
        assert len(t.cards) == 1 and t.cards[0][0] == "oc_1"
        card = t.cards[0][1]
        assert "绑定成功" in card["header"]["title"]["content"]
        assert _value(_buttons(card)[0])["uid"] == "ou_new"      # 只有新 owner 能点
        assert t.sent == []                                      # 卡片成功 → 不发纯文本
    asyncio.run(go())


def test_link_failure_still_plain_text():
    async def go():
        client = FakeClient()
        client.feishu_configs.bind_result = False
        w, t = await _worker(client)
        try:
            await t.inject(_msg("/link BAD123", open_id="ou_new", union="on_new"))
        finally:
            await w.stop()
        assert t.cards == [] and len(t.sent) == 1 and "无效" in t.sent[0][1]
    asyncio.run(go())


def test_welcome_card_send_failure_falls_back_to_text():
    async def go():
        client = FakeClient()
        w, t = await _worker(client)

        async def _fail(chat_id, card):
            return None
        t.send_card = _fail
        try:
            await t.inject(_msg("/link A7K2MQ", open_id="ou_new", union="on_new"))
        finally:
            await w.stop()
        assert len(t.sent) == 1 and "绑定成功" in t.sent[0][1]
    asyncio.run(go())


# --- /help ------------------------------------------------------------------
def test_help_command_answers_card_without_running():
    async def go():
        client = FakeClient()
        dialer = FakeDialer(RunOutcome(session_id="s1", text="nope"))
        w, t = await _worker(client, dialer)
        try:
            for text in ("/help", "/帮助"):
                await t.inject(_msg(text))
        finally:
            await w.stop()
        assert len(t.cards) == 2
        assert all("使用指南" in c["header"]["title"]["content"] for _, c in t.cards)
        assert dialer.calls == []                     # 本地回执，不进 agent
        assert client.bindings.list_bindings("A") == []   # 不动会话
    asyncio.run(go())


# --- 按钮 = 手打指令 --------------------------------------------------------
def _tap(cmd, *, open_id=_OWNER_OPEN, chat="oc_1", union=_OWNER_UNION, ct="p2p"):
    return {"open_id": open_id, "union_id": union, "chat_id": chat, "message_id": "om_card",
            "tag": "button", "name": None, "value": {"act": "menu", "cmd": cmd, "uid": _OWNER_OPEN,
                                                     "ct": ct, "un": _OWNER_UNION}}


def test_menu_new_button_detaches_session():
    async def go():
        client = FakeClient()
        client.bindings.bind("A", "sess-old", "oc_1")
        w, t = await _worker(client)
        try:
            assert get_worker("A") is w
            resp, coro = card_actions.handle("A", _tap("/new"))
            assert "新对话" in resp["toast"]["content"] and coro is not None
            await coro
        finally:
            await w.stop()
        assert client.bindings.list_bindings("A")[0].session_uuid is None
        # 先回执，再发「已成功重置」完成卡（卡片只在重置真的做完之后才出现）
        assert t.sent == [("oc_1", "🔔 收到菜单指令：/new")]
        assert len(t.cards) == 1 and "已成功重置" in t.cards[0][1]["header"]["title"]["content"]
        assert t.reactions == []      # 合成消息没有 message_id → 表情链路 no-op
    asyncio.run(go())


def test_menu_clear_button_runs_full_pipeline():
    async def go():
        client = FakeClient()
        client.bindings.bind("A", "sess-old", "oc_1")
        dialer = FakeDialer(RunOutcome(session_id="sess-old", text="cleared"))
        w, t = await _worker(client, dialer)
        try:
            resp, coro = card_actions.handle("A", _tap("/compact"))
            assert coro is not None and "compact" in resp["toast"]["content"]
            await coro
        finally:
            await w.stop()
        assert [c["prompt"] for c in dialer.calls] == ["/compact"]
        assert dialer.calls[0]["session_id"] == "sess-old"      # 续用当前会话
        assert t.sent == [("oc_1", "🔔 收到菜单指令：/compact")]
        # 进行中卡「正在压缩会话…」→ 终态 patch 成「会话已压缩」，中间产物不外露
        assert "正在压缩会话" in t.cards[0][1]["header"]["title"]["content"]
        assert "已压缩" in t.patches[-1][1]["header"]["title"]["content"]
        assert "cleared" not in json.dumps(t.patches[-1][1], ensure_ascii=False)
    asyncio.run(go())


def test_compact_progress_dots_cycle_and_error_falls_back():
    from priva_channel_connector import menu_cards as mc
    assert [mc.compacting_card(d)["body"]["elements"][0]["content"].count(".")
            for d in (1, 2, 3)] == [1, 2, 3]

    async def go():
        client = FakeClient()
        client.bindings.bind("A", "sess-old", "oc_1")
        dialer = FakeDialer(RunOutcome(session_id=None, text="", is_error=True,
                                       error_text="dial_failed"))
        w, t = await _worker(client, dialer)
        try:
            await t.inject(_msg("/compact"))
        finally:
            await w.stop()
        # 跑挂了：退回通用终态卡，错误原样可见——绝不用「已压缩」盖住失败
        final = t.patches[-1][1]
        assert "header" not in final and "dial_failed" in json.dumps(final, ensure_ascii=False)
    asyncio.run(go())


def test_menu_tap_gated_to_card_recipient():
    async def go():
        client = FakeClient()
        w, _ = await _worker(client)
        try:
            resp, coro = card_actions.handle("A", _tap("/new", open_id="ou_someone_else"))
        finally:
            await w.stop()
        assert coro is None and "本人" in resp["toast"]["content"]
    asyncio.run(go())


def test_menu_tap_unknown_command_and_dead_worker():
    async def go():
        client = FakeClient()
        w, _ = await _worker(client)
        try:
            resp, coro = card_actions.handle("A", _tap("/rm -rf"))
            assert coro is None and "失效" in resp["toast"]["content"]
        finally:
            await w.stop()
        # worker 已注销（teardown / 重启）→ 明确告知，不静默丢弃
        resp, coro = card_actions.handle("A", _tap("/new"))
        assert coro is None and "连接已关闭" in resp["toast"]["content"]
        assert get_worker("A") is None
    asyncio.run(go())


def test_menu_tap_falls_back_to_card_union_id():
    """operator 不带 union_id 时用卡片里记的那个——否则 owner_only gate 会拒掉自己的 owner。"""
    async def go():
        client = FakeClient()
        client.bindings.bind("A", "sess-old", "oc_1")
        dialer = FakeDialer(RunOutcome(session_id="sess-old", text="ok"))
        w, _ = await _worker(client, dialer)
        try:
            tap = _tap("/clear")
            tap["union_id"] = None
            _, coro = card_actions.handle("A", tap)
            await coro
        finally:
            await w.stop()
        assert [c["prompt"] for c in dialer.calls] == ["/clear"]
    asyncio.run(go())


def test_menu_tap_rejected_when_not_owner_union():
    """按钮不是绕过 access gate 的后门：合成消息照样过 owner gate。"""
    async def go():
        client = FakeClient()
        dialer = FakeDialer(RunOutcome(session_id="s1", text="ok"))
        w, t = await _worker(client, dialer, cfg=_cfg(owner="on_someone_else"))
        try:
            _, coro = card_actions.handle("A", _tap("/clear"))
            await coro
        finally:
            await w.stop()
        assert dialer.calls == []
    asyncio.run(go())


# --- 机器人自定义菜单（application.bot.menu_v6，§9 方案②）-------------------
_SKILLS = {"personal": [{"name": "officecli", "description": "Office 文档", "enabled": True},
                        {"name": "old-thing", "description": "", "enabled": False}],
           "groups": [{"cwd": "/workspace/proj", "skills": [{"name": "deploy", "description": "上线"}]}]}


@pytest.fixture(autouse=True)
def _clear_chat_cache():
    worker_mod._P2P_CHATS.clear()
    yield
    worker_mod._P2P_CHATS.clear()


def test_bot_menu_uses_cached_chat_from_prior_dm():
    async def go():
        client = FakeClient()
        client.bindings.bind("A", "sess-old", "oc_1")
        w, t = await _worker(client)
        try:
            await t.inject(_msg("你好"))            # 任意私聊消息 → 缓存 open_id→chat_id
            t.p2p_chats.clear()                     # 证明没有回落到按 open_id 投递
            t.sent.clear()
            await w.handle_bot_menu("new_session", _OWNER_OPEN, _OWNER_UNION)
        finally:
            await w.stop()
        # 命中缓存也照发回执（点击必须有即时反馈），且只发一条
        assert t.sent == [("oc_1", "🔔 收到菜单指令：/new")]
        assert any("已成功重置" in c.get("header", {}).get("title", {}).get("content", "")
                   for _, c in t.cards)
    asyncio.run(go())


def test_bot_menu_cold_start_ack_resolves_chat():
    """冷启动（无缓存）：回执按 open_id 投出去，顺手从响应体学到 chat_id，只发一条。"""
    async def go():
        client = FakeClient()
        client.bindings.bind("A", "sess-old", "oc_1")
        w, t = await _worker(client)
        t.p2p_chats[_OWNER_OPEN] = "oc_1"           # 飞书从响应体回吐的 p2p chat_id
        try:
            await w.handle_bot_menu("new_session", _OWNER_OPEN, _OWNER_UNION)
        finally:
            await w.stop()
        assert t.sent == [("oc_1", "🔔 收到菜单指令：/new")]
        assert client.bindings.list_bindings("A")[0].session_uuid is None   # detach 照做
        assert "已成功重置" in t.cards[0][1]["header"]["title"]["content"]
        assert worker_mod._P2P_CHATS[("A", _OWNER_OPEN)] == "oc_1"
    asyncio.run(go())


def test_bot_menu_cold_start_help_acks_then_cards():
    async def go():
        client = FakeClient()
        w, t = await _worker(client)
        t.p2p_chats[_OWNER_OPEN] = "oc_1"
        try:
            await w.handle_bot_menu("help", _OWNER_OPEN, _OWNER_UNION)
        finally:
            await w.stop()
        assert t.sent == [("oc_1", "🔔 收到菜单指令：/help")]
        assert len(t.cards) == 1 and "使用指南" in t.cards[0][1]["header"]["title"]["content"]
    asyncio.run(go())


def test_bot_menu_cold_start_gated_before_any_send():
    """非授权用户点菜单：一条消息都不能发出去（gate 必须先于第一条输出）。"""
    async def go():
        client = FakeClient()
        w, t = await _worker(client, cfg=_cfg(owner="on_someone_else"))
        t.p2p_chats[_OWNER_OPEN] = "oc_1"
        try:
            await w.handle_bot_menu("help", _OWNER_OPEN, _OWNER_UNION)
        finally:
            await w.stop()
        assert t.sent == [] and t.cards == []
    asyncio.run(go())


def test_bot_menu_unresolvable_chat_is_dropped_not_crashed():
    async def go():
        client = FakeClient()
        dialer = FakeDialer(RunOutcome(session_id="s1", text="ok"))
        w, t = await _worker(client, dialer)         # p2p_chats 空 → send_text_to_user 返回 None
        try:
            await w.handle_bot_menu("compact", _OWNER_OPEN, _OWNER_UNION)
        finally:
            await w.stop()
        assert dialer.calls == [] and t.cards == []
    asyncio.run(go())


def test_bot_menu_event_keys_map_to_commands():
    assert menu_cards.BOT_MENU_KEYS["new_session"][0] == "/new"
    assert menu_cards.BOT_MENU_KEYS["compact"][0] == "/compact"
    assert menu_cards.BOT_MENU_KEYS["help"][0] == "/help"
    assert menu_cards.BOT_MENU_KEYS["list_skill"][0] == "/skill"
    # 未知 key / 未 arm 的账号：明确记日志后丢弃，绝不炸线程
    assert card_actions.handle_bot_menu("A", "nope", "ou_x", "on_x") is None
    assert card_actions.handle_bot_menu("A", "help", "ou_x", "on_x") is None


def test_bot_menu_compact_runs_pipeline_in_resolved_chat():
    async def go():
        client = FakeClient()
        client.bindings.bind("A", "sess-old", "oc_1")
        dialer = FakeDialer(RunOutcome(session_id="sess-old", text="done"))
        w, t = await _worker(client, dialer)
        t.p2p_chats[_OWNER_OPEN] = "oc_1"
        try:
            await w.handle_bot_menu("compact", _OWNER_OPEN, _OWNER_UNION)
        finally:
            await w.stop()
        assert [c["prompt"] for c in dialer.calls] == ["/compact"]
        assert dialer.calls[0]["session_id"] == "sess-old"
        assert t.sent == [("oc_1", "🔔 收到菜单指令：/compact")]
        assert t.cards and t.cards[0][0] == "oc_1"       # 进行中卡发到解出来的会话
        assert "正在压缩会话" in t.cards[0][1]["header"]["title"]["content"]
        assert "已压缩" in t.patches[-1][1]["header"]["title"]["content"]
    asyncio.run(go())


# --- /skill ------------------------------------------------------------------
def test_skill_command_renders_inventory_card(monkeypatch):
    async def go():
        client = FakeClient()
        dialer = FakeDialer(RunOutcome(session_id="s1", text="nope"))
        w, t = await _worker(client, dialer)

        async def _fake_list(account_id, username, **kw):
            return _SKILLS
        monkeypatch.setattr("priva_channel_connector.skills.list_skills", _fake_list)
        try:
            await t.inject(_msg("/skill"))
        finally:
            await w.stop()
        assert dialer.calls == []                       # 只读，不进 agent
        card = t.cards[0][1]
        assert "Skill" in card["header"]["title"]["content"]
        tables = [e for e in card["body"]["elements"] if e.get("tag") == "table"]
        assert len(tables) == 2                          # 个人 + 一个工作区分组
        assert [c["display_name"] for c in tables[0]["columns"]] == ["Skill", "说明"]
        cells = " ".join(v for tb in tables for r in tb["rows"] for v in r.values())
        assert "officecli" in cells and "deploy" in cells
        assert "已停用" in cells                          # enabled=False 标灰
        assert "—" in cells                              # 无 description 的占位
    asyncio.run(go())


def test_skill_command_reports_fetch_failure(monkeypatch):
    async def go():
        client = FakeClient()
        w, t = await _worker(client)

        async def _fail(account_id, username, **kw):
            return None
        monkeypatch.setattr("priva_channel_connector.skills.list_skills", _fail)
        try:
            await t.inject(_msg("/skill"))
        finally:
            await w.stop()
        assert t.cards == [] and "读不到 skill" in t.sent[0][1]   # 不静默
    asyncio.run(go())


def test_skills_card_empty_and_cap():
    empty = menu_cards.skills_card({"personal": [], "groups": []})
    assert "还没有安装" in empty["body"]["elements"][0]["content"]
    assert not [e for e in empty["body"]["elements"] if e.get("tag") == "table"]
    many = {"personal": [{"name": f"s{i}"} for i in range(40)], "groups": []}
    card = menu_cards.skills_card(many)
    table = [e for e in card["body"]["elements"] if e.get("tag") == "table"][0]
    assert len(table["rows"]) == 30 and table["page_size"] == 10   # 飞书自带翻页
    body = " ".join(e.get("content", "") for e in card["body"]["elements"])
    assert "另有 10 个未列出" in body          # 截断必须报数，不能假装列全了


# --- /info（CLI /context + 会话 id）-----------------------------------------
_CONTEXT_OUT = """## Context Usage

**Model:** claude-haiku-4-5-20251001
**Tokens:** 38.4k / 200k (19%)

### Estimated usage by category

| Category | Tokens | Percentage |
|----------|--------|------------|
| System prompt | 7.4k | 3.7% |
| Free space | 161.6k | 80.8% |
"""


def test_router_info_rewrites_to_context_and_keeps_session():
    client = FakeClient()
    client.bindings.bind("A", "sess-1", "oc_1")
    r = SessionRouter(client)
    d = r.decide(_msg("/info"))
    assert d.kind == "info" and d.prompt == "/context" and d.resume_session_id == "sess-1"


def test_annotate_session_id_placement():
    from priva_channel_connector.sse import StreamState, annotate_session_id
    st = StreamState()
    st.timeline.append(_CONTEXT_OUT)
    annotate_session_id(st, "sess-abc")
    lines = st.timeline[0].split("\n")
    i = next(i for i, ln in enumerate(lines) if ln.startswith("**Tokens:**"))
    assert lines[i + 1] == "**Session id:** `sess-abc`"      # 紧跟 Tokens 行

    # CLI 换了格式 / 没有 Tokens 行 → 单独起一行，绝不丢
    st2 = StreamState()
    st2.timeline.append("something else")
    annotate_session_id(st2, "sess-x")
    assert st2.timeline[0].startswith("**Session id:** `sess-x`")

    # 还没有会话 → 说明白，不留空
    st3 = StreamState()
    annotate_session_id(st3, None)
    assert "尚未建立会话" in st3.timeline[0]


def test_info_command_renders_session_info_card():
    async def go():
        client = FakeClient()
        client.bindings.bind("A", "sess-1", "oc_1")
        dialer = FakeDialer(RunOutcome(session_id="sess-1", text=_CONTEXT_OUT))
        w, t = await _worker(client, dialer)
        try:
            await t.inject(_msg("/info"))
        finally:
            await w.stop()
        assert [c["prompt"] for c in dialer.calls] == ["/context"]     # 跑的是 CLI 原生指令
        final = t.patches[-1][1]
        assert "当前会话信息" in final["header"]["title"]["content"]
        assert final["header"]["subtitle"]["content"] == "sess-1"   # 裸 id，无标签
        tables = [e for e in final["body"]["elements"] if e.get("tag") == "table"]
        assert tables and [c["display_name"] for c in tables[0]["columns"]] == [
            "Category", "Tokens", "Percentage"]                        # 原生表格
        md = " ".join(e.get("content", "") for e in final["body"]["elements"]
                      if e.get("tag") == "markdown")
        assert "**模型：**" in md and "Token 消耗大致分布" in md         # 标签汉化
        assert "Context Usage" not in md                               # 标题上提到卡头
        assert "Session id" not in md                                  # 正文不再重复
    asyncio.run(go())


def test_info_falls_back_to_generic_card_on_error():
    """跑挂了：退回通用终态卡，会话 id 补进正文，不能因为没有 /context 输出就没影了。"""
    async def go():
        client = FakeClient()
        client.bindings.bind("A", "sess-1", "oc_1")
        dialer = FakeDialer(RunOutcome(session_id=None, text="", is_error=True,
                                       error_text="dial_failed"))
        w, t = await _worker(client, dialer)
        try:
            await t.inject(_msg("/info"))
        finally:
            await w.stop()
        final = t.patches[-1][1]
        assert "header" not in final                                   # 通用卡无 header
        md = " ".join(e.get("content", "") for e in final["body"]["elements"])
        assert "**Session id:** `sess-1`" in md
    asyncio.run(go())


def test_bot_menu_session_info_key():
    assert menu_cards.BOT_MENU_KEYS["session_info"][0] == "/info"


if __name__ == "__main__":   # pragma: no cover
    pytest.main([__file__])
