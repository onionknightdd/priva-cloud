"""指令引导卡片（feat_feishu_DM.md §9.1）——飞书没有指令注册 API（§9 调研结论），
所以「机器人有哪些指令」只能靠卡片自己说。两个版本：

  - 欢迎卡：`/link` 绑定成功的回执（绿标题），只带 🆕 一个按钮 —— 刚绑定还没开始用，
    `/clear` `/compact` 这种上下文指令没有意义（裁定 2026-07-24）。
  - 使用指南卡：`/help`（`/帮助`）随时唤起（蓝标题），完整指令列表 + 三个按钮。

按钮 = 用户手打该指令的等价物：``value`` 自带授权信息（``uid`` 卡片接收者的
open_id，只有本人可点）与上下文（``ct`` chat_type、``un`` union_id 用于合成入站消息
过 access gate），所以卡片可以永久留在聊天记录里当常驻菜单——不需要 pending 注册表，
重启/re-arm 之后老卡片照样能点。

纯渲染，无 ``lark_oapi`` 依赖（与 cards.py / permission_cards.py 同一分层）。
"""

from __future__ import annotations

import re

MENU_ACT = "menu"

# 按钮允许注入的指令白名单 → 点击后的即时 toast。指令文本本身就是注入 pipeline 的
# 消息文本（`/new` `/skill` 走 router 拦截，`/clear` `/compact` 交给 SDK 解释）。
MENU_COMMANDS = {
    "/new": "🆕 已开启新对话",
    "/clear": "🧹 已发送 /clear",
    "/compact": "📦 已发送 /compact",
    "/skill": "🧩 正在读取 skill 列表",
}

# 飞书「机器人自定义菜单」（方案②，§9）——租户在开发者后台手工配的菜单项，点击推送
# ``application.bot.menu_v6``，事件体只有 operator + event_key（无 chat_id/message_id）。
# event_key 由租户配置时填写，这里是平台约定值 → (注入的指令文本, 展示名)。
BOT_MENU_KEYS = {
    "new_session": ("/new", "重置会话"),
    "compact": ("/compact", "压缩会话"),
    "help": ("/help", "查看帮助"),
    "list_skill": ("/skill", "查看 skill"),
    "session_info": ("/info", "会话信息"),
}


def _pt(s: str) -> dict:
    return {"tag": "plain_text", "content": s}


def _md(content: str) -> dict:
    return {"tag": "markdown", "content": content}


def _card(title: str, template: str, elements: list, subtitle: str = "") -> dict:
    header = {"title": _pt(title), "template": template}
    if subtitle:
        header["subtitle"] = _pt(subtitle)
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": header,
        "body": {"elements": elements},
    }


def _button(text: str, cmd: str, *, open_id: str, chat_type: str, union_id: str,
            primary: bool = False) -> dict:
    return {
        "tag": "button", "text": _pt(text), "width": "fill",
        "type": "primary" if primary else "default",
        "behaviors": [{"type": "callback", "value": {
            "act": MENU_ACT, "cmd": cmd, "uid": open_id or "",
            "ct": chat_type or "p2p", "un": union_id or "",
        }}],
    }


def _footer(max_images: int, max_image_bytes: int) -> dict:
    mb = max_image_bytes // (1024 * 1024)
    return _md(f"<font color='grey'>📷 支持直接发送图片：最多 {max_images} 张，单张 ≤ {mb}MB\n"
               f"访问模式与会话管理请前往网页控制台 · 飞书设置</font>")


def welcome_card(*, open_id: str, chat_type: str = "p2p", union_id: str = "",
                 max_images: int = 5, max_image_bytes: int = 3 * 1024 * 1024) -> dict:
    """`/link` 绑定成功的回执卡。"""
    return _card("✅ 绑定成功", "green", [
        _md("你已成为该机器人的所有者，直接发消息即可开始对话。每个聊天窗口是独立会话。"),
        _md("`/new`　开启新对话（别名 `/新` `/reset`）\n`/help`　查看使用指南"),
        _footer(max_images, max_image_bytes),
        _button("🆕 开始新对话", "/new", open_id=open_id, chat_type=chat_type,
                union_id=union_id, primary=True),
    ])


def help_card(*, open_id: str, chat_type: str = "p2p", union_id: str = "",
              max_images: int = 5, max_image_bytes: int = 3 * 1024 * 1024) -> dict:
    """`/help` 唤起的使用指南卡（使用中唤起 → 保留完整指令集）。"""
    return _card("🤖 使用指南", "blue", [
        _md("直接发消息即可对话。以下是特殊指令："),
        _md("`/new`　　开启新对话\n"
            "`/clear`　清空当前会话上下文\n"
            "`/compact`　压缩当前会话上下文\n"
            "`/skill`　查看已安装的 skill\n"
            "`/info`　　查看当前会话信息（模型 · Token 用量 · 会话 id）\n"
            "`/help`　　查看本指南"),
        _md("<font color='grey'>所有指令的输出不会出现在对话的上下文中。</font>"),
        _footer(max_images, max_image_bytes),
        _button("🆕 开始新对话", "/new", open_id=open_id, chat_type=chat_type,
                union_id=union_id, primary=True),
        _button("🧹 /clear", "/clear", open_id=open_id, chat_type=chat_type, union_id=union_id),
        _button("📦 /compact", "/compact", open_id=open_id, chat_type=chat_type, union_id=union_id),
        _button("🧩 /skill", "/skill", open_id=open_id, chat_type=chat_type, union_id=union_id),
    ])


# --- skill 清单 -------------------------------------------------------------
_SKILL_MAX = 30          # 卡片总条数上限（飞书单卡 30KB，超出的只报数量）
_DESC_MAX = 60


def _skill_row(s: dict) -> list[str]:
    """一行两列：名称 / 说明。停用的整行标灰并缀「· 已停用」（表格单元格是 lark_md）。"""
    name = str(s.get("name") or "").strip() or "(unnamed)"
    desc = " ".join(str(s.get("description") or "").split())[:_DESC_MAX] or "—"
    if s.get("enabled", True):
        return [f"`{name}`", desc]
    return [f"<font color='grey'>`{name}` · 已停用</font>", f"<font color='grey'>{desc}</font>"]


def skills_card(data: dict, *, cwd_label=None) -> dict:
    """`/skill`（自定义菜单 list_skill）：账号 ar pod 上的 skill 清单，用飞书**原生表格**
    渲染（每个分组一张，行数超过 page_size 由飞书自带翻页）。
    ``data`` == runner ``GET /api/sandbox/resource/skills/`` 的响应体。"""
    from .cards import native_table   # 与流式卡片共用同一份实测通过的 table 构造

    personal = list((data or {}).get("personal") or [])
    groups = list((data or {}).get("groups") or [])
    elements: list[dict] = []
    shown = 0
    total = len(personal) + sum(len(g.get("skills") or []) for g in groups)

    def _section(title: str, skills: list) -> None:
        nonlocal shown
        rows = []
        for s in skills:
            if shown >= _SKILL_MAX:
                break
            rows.append(_skill_row(s))
            shown += 1
        if rows:
            elements.append(_md(f"**{title}**"))
            elements.append(native_table(["Skill", "说明"], rows))

    if personal:
        _section("个人 skill", personal)
    for g in groups:
        label = cwd_label(g.get("cwd") or "") if cwd_label else (g.get("cwd") or "工作区")
        _section(f"工作区 · {label}", g.get("skills") or [])
    if not elements:
        elements.append(_md("还没有安装任何 skill。"))
    elif total > shown:
        elements.append(_md(f"<font color='grey'>…… 另有 {total - shown} 个未列出</font>"))
    elements.append(_md("<font color='grey'>安装/停用 skill 请前往网页控制台 · Skills</font>"))
    return _card("🧩 已安装的 Skill", "indigo", elements,
                 subtitle="手机端点击表格可以查看详情")


# --- 指令回执 ---------------------------------------------------------------
def menu_ack(cmd: str) -> str:
    """菜单/按钮点击的即时回执。菜单点击本身没有任何视觉反馈（不像普通消息有表情
    生命周期），所以这条先行发出，用户立刻知道点到了；随后才是指令自己的内容。
    冷启动时它同时承担解析 p2p chat_id 的任务（响应体里带）。"""
    return f"🔔 收到菜单指令：{cmd}"


# --- /new：重置完成卡 -------------------------------------------------------
def reset_card() -> dict:
    return _card("✅ 会话已成功重置", "green",
                 [_md("之后发送的消息会进入一个全新会话，之前的对话不再作为上下文。")])


# --- /compact：压缩进行中 / 完成 --------------------------------------------
def compacting_card(dots: int = 1) -> dict:
    """进行中：`...` 随 ticker 1→2→3 循环，直到压缩结束被完成卡替换。"""
    return _card("📦 正在压缩会话", "blue",
                 [_md(f"<font color='grey'>正在压缩会话{'.' * max(1, min(dots, 3))}</font>")])


def compacted_card() -> dict:
    return _card("✅ 会话已压缩", "green",
                 [_md("上下文已压缩，会话继续保留，可以直接接着聊。")])


# --- /info：会话信息卡 ------------------------------------------------------
# CLI `/context` 的原始 markdown 直接进卡片正文（表格由 answer_elements 提升成原生表格），
# 只改三处：一级标题上提到卡头、两个英文标签改中文。其余原样，跟着 CLI 走。
_INFO_REWRITES = (
    (re.compile(r"^##\s*Context Usage\s*$", re.M), ""),                       # → 卡片标题
    (re.compile(r"\*\*Model:\*\*"), "**模型：**"),
    (re.compile(r"^###\s*Estimated usage by category\s*$", re.M), "### Token 消耗大致分布"),
)


def info_card(context_text: str, session_id: str | None) -> dict:
    """`/info`：CLI `/context` 的输出 → 「当前会话信息」卡。会话 id 只出现在副标题，
    正文不再重复。"""
    from .cards import answer_elements   # GFM 表格 → 原生 table（与终态卡同一份实现）

    text = context_text or ""
    for pattern, repl in _INFO_REWRITES:
        text = pattern.sub(repl, text)
    elements = answer_elements(text.strip()) or [_md("(无输出)")]
    # 副标题只放裸 id（标题已经说明这是会话信息，"Session id:" 是冗余的字）。飞书标题
    # 组件不接受任何字号字段（实测 ErrCode 11311 header text_size invalid），所以减轻
    # 视觉重量只能靠减字。
    return _card("📊 当前会话信息", "wathet", elements,
                 subtitle=session_id or "尚未建立会话")


def info_text(context_text: str, session_id: str | None) -> str:
    return f"📊 当前会话信息\nSession id: {session_id or '尚未建立会话'}\n\n{context_text or ''}".strip()


# --- plain-text fallbacks --------------------------------------------------
# 卡片发送失败（send_card → None：接口报错/字段被拒）时至少把同样的内容说清楚，
# 绝不让用户对着空白聊天窗口猜。
def welcome_text() -> str:
    return ("✅ 绑定成功！你已成为此机器人的所有者，直接发消息即可开始对话。\n"
            "/new 开启新对话 · /help 查看使用指南")


def skills_text(data: dict) -> str:
    names = [str(s.get("name") or "") for s in (data or {}).get("personal") or []]
    for g in (data or {}).get("groups") or []:
        names += [str(s.get("name") or "") for s in g.get("skills") or []]
    names = [n for n in names if n][:_SKILL_MAX]
    if not names:
        return "🧩 还没有安装任何 skill。"
    return "🧩 已安装的 Skill：\n" + "\n".join(f"- {n}" for n in names)


def help_text() -> str:
    return ("🤖 使用指南 —— 直接发消息即可对话。以下是特殊指令：\n"
            "/new 开启新对话\n"
            "/clear 清空当前会话上下文\n"
            "/compact 压缩当前会话上下文\n"
            "/skill 查看已安装的 skill\n"
            "/info 查看当前会话信息（模型 · Token 用量 · 会话 id）\n"
            "/help 查看本指南\n"
            "所有指令的输出不会出现在对话的上下文中。")
