# 飞书 DM 功能设计（channel-connector）

> 状态：Phase 0 / 1 / 2 已落地 · §9.1 指令引导卡片已落地（2026-07-27 部署）
> 关联文档：`docs/im-channel-permission-zh.md`（权限/AUQ 交互协议）
> 更新：2026-07-27

## 1. 背景与架构

飞书 DM 是 channel-connector 承载的常驻字节通道：每个 effective 账号一条
lark_oapi WS 长连接（Model B：**连接 == 账号**，用户自建应用），reconcile 引擎
对着 data-spine 的 `feishu_channel_config` 做 poll-diff（diff 键 = `desired_digest`），
入站 DM 经 access gate → SessionRouter 决策 → wake + dial `ar-{account}/run/stream`
→ SSE 折叠进流式卡片回复。

```
飞书 ⇄ WS(lark thread) → _dispatch → AppWorker._handle
                                        ├─ access gate (router.access_allowed)
                                        ├─ decide (/new | /link | run)
                                        ├─ fetch images (REST)
                                        └─ dial /run/stream → 流式卡片 ⇄ 权限/AUQ 卡片
```

## 2. 已落地（Phase 0）

### 2.1 WS 生命周期修复（2026-07-23）
- 原 bug：`stop()` 用 `to_thread` 调 async `_disconnect` —— 协程从未执行，
  连接成僵尸；同 app 多连接时飞书事件单播，消息被路由到已"关闭"的环境。
- 修复：`_ThreadLocalLoopProxy` 让每条连接持有独立事件循环（解除单进程单连接
  限制 + teardown 后可 re-arm）；`stop()` 为有界有序序列（关自动重连 →
  `_disconnect` 在连接自己的 loop 上执行 → `loop.stop` → join）；线程退出时
  cancel 遗留任务、强拆漏关 socket、`loop.close()`。资源泄漏由
  `tests/api/test_lark_ws_lifecycle.py` 回归钉死（server 观测到关闭、双连接
  并发、10 轮 arm/teardown 后线程/fd 回基线）。
- 配套：`_dispatch`/卡片回调/status 上报的 `_stopping` gate；REST client 停止后
  禁止懒重建；worker 在途 turn 追踪 + teardown 取消；pending 注册表 TTL 兜底。

### 2.2 图片 / 图文消息（2026-07-23）
- `_dispatch` 放行 `text` / `image` / `post`；post 展平（标题+段落、链接
  `文字 (url)`、`img` run 收集 image_key 保序）。
- 字节经 `GET /im/v1/messages/{mid}/resources/{key}?type=image` 拉取
  （**需应用开通 `im:resource` 权限**），魔数嗅探 PNG/JPEG/GIF/WEBP（恰好是
  runner 白名单）。
- worker 编排：≤5 张、单张解码后 ≤3MB（与 runner 校验一致），超限/失败以中文
  附注折进 prompt，不阻断 run；纯图片消息兜底文案 **"请描述图片内容。"**
- 透传 `AgentRunRequest.images` —— 与 web SPA 完全同一条 lane，runner 侧
  校验/vision-model/content blocks 零改动。

### 2.3 诊断日志（2026-07-23）
- 每条入站事件（过滤前）打 meta 日志：`chat_type` `msg_type` `sender_type`
  `tenant_key` `open_id` `user_id` `union_id`。
- 群聊消息补群名称日志（`GET /im/v1/chats/{chat_id}`，**需 `im:chat:readonly`**；
  按 chat_id 缓存）。用于上线观察真实 id 形态，验证 owner 绑定的身份假设。

## 3. 身份模型（关键裁定）

- 平台 `account.feishu_user_id` 是 **SSO 应用命名空间**的 union_id；bot 为用户
  自建应用、不同开发者主体，**两个命名空间不互通**（spec §12-2）→ 不能拿平台
  身份直接比对 DM 发送者。
- 因此 owner 身份必须在 **bot 应用自己的命名空间内自举**：link-code 绑定，
  身份由平台登录态背书。绑定标识存 **union_id**（同开发者主体下稳定），
  open_id 存快照（卡片操作者校验、日志）。
- 现状：`access_allowed` 三种模式全放行（Model B MVP 取舍，防线 = 飞书应用
  可用范围配置）；全链路唯一身份校验点是交互卡片只允许原 DM 发送者点击
  （open_id 比对，`card_actions.py`）。

## 4. Phase 1 — link-code owner 绑定 + 单聊 gate

### 4.1 绑定流程

```
web 控制台                    connector                     data-spine
────────────                 ─────────                     ──────────
[生成绑定码] ──────────────────────────────────────────▶ 存 code hash + TTL(10min)
显示 /link A7K2MQ
      │ 用户私聊发给机器人
      ▼
                    收到 "/link A7K2MQ"
                    (router 拦截,不进 agent) ──────────▶ 原子校验: hash 匹配+未过期
                                                          → 写 owner_union_id/open_id
                                                          → 清除 code(一次性)
                    回复 "✅ 绑定成功" ◀──────────────────┘
```

- 触发格式：`/link <code>`（别名 `/绑定`），6 位 Crockford base32（去易混淆
  字符），输入大小写不敏感；沿用 `/new` 的 slash-command 拦截模式，绑定消息
  不透传给 agent。
- 码只存 SHA-256 hash、常数时间比较、一次性（成功即清）、TTL 10 分钟；每账号
  同时一个有效码，重新生成覆盖；校验失败统一回"绑定码无效或已过期"（不暴露
  存在性）。
- 解绑仅 UI 侧提供（平台登录态），bot 侧不做 `/unlink`。

### 4.2 Access gate 语义

| 模式 | 未绑定 | 已绑定 |
|---|---|---|
| `owner_only`（默认） | 放行（兼容现状，UI 明示"未绑定"） | 仅 owner union_id；其他人回 `reject_message` |
| `allowlist` | 放行 | owner + `allowed_union_ids` |
| `all` | 放行 | 放行 |

同时上线**单聊 gate**：`chat_type != "p2p"` 一律跳过（meta 日志保留）。
Phase 1 期间群聊 = 未开放。

### 4.3 数据与一致性

- `feishu_channel_config` 加列：`owner_union_id` `owner_open_id`
  `owner_bound_at` `link_code_hash` `link_code_expires_at`。
- **owner 列纳入 `desired_digest`**：绑定/解绑通过现有 digest 机制触发
  teardown+re-arm（约 1-2 秒重连，稀有事件），worker 的 cfg 快照自动刷新，
  无需每消息回查。link_code 列**不入 digest**（生成码不应重连）。
- 新 RPC（沿用角色分离模式）：
  - `CreateLinkCode(AccountRef)`（control-panel）→ 生成+存 hash，返回明文码+过期时间
  - `BindOwnerWithCode(account_id, code, union_id, open_id)`（connector）→ 原子校验+绑定+清码
  - `UnbindOwner(AccountRef)`（control-panel）
- InboundMessage 增加 `sender_union_id` `chat_type`。

### 4.4 UI（user SPA 飞书设置页）

```
┌─ 飞书身份绑定 ────────────────────────────┐   绑定后:
│ 状态: ● 未绑定                            │   ┌─────────────────────────────────┐
│ 当前可用范围内所有用户均可使用            │   │ 状态: ● 已绑定 2026-07-23 14:02 │
│                                           │   │ 身份: on_9f3a…c2(脱敏)          │
│ [ 生成绑定码 ]                            │   │ 访问模式: [仅本人 ▾]            │
│ ┌───────────────────────────────────────┐ │   │ [ 解除绑定 ]                    │
│ │ /link A7K2MQ          有效期 09:43 ⏱ │ │   └─────────────────────────────────┘
│ │ 复制后私聊发送给机器人完成绑定        │ │
│ └───────────────────────────────────────┘ │
└───────────────────────────────────────────┘
```

CP API：`POST /feishu/link-code`（生成）、`DELETE /feishu/owner`（解绑）；
绑定状态并入现有 config 响应（`owner_bound_at` + 脱敏 union_id）。

## 5. Phase 2 — 群聊参与（受控开放）（2026-07-23 已实现）

### 5.1 控制面（用户可控 + admin 全局闸）

```
effective_group_enabled = 用户 group_chat_enabled(默认关)
                        AND NOT 平台全局 group_chat_disabled(admin)
```

| 控制点 | 谁操作 | 粒度 | 存放 |
|---|---|---|---|
| `group_chat_enabled` | 用户（飞书设置页 toggle） | 单账号 | `feishu_channel_config` 加列，默认 0 |
| `group_chat_disabled` | admin（Configurations 全局开关） | **全平台一刀** | 新单例表 `channel_platform_config(id=1)`，沿用 `runner_defaults` 模式 |
| （既有）`admin_disabled` | admin | 单账号 | 不变——管整个 DM 功能；**不新增群聊维度的单用户 admin 控制** |

- data-spine 合成 `effective_group_enabled` 并**纳入 digest**——折进 digest 的是
  **合成后的 effective 位**（不是原始两位）：全局关着时用户翻自己的 toggle 不改变
  行为、也就不弹连接；admin 全局翻转由 `recompute_digests()` 批量重算，只重写
  effective 位真正翻转的行（= 精准 re-arm 受影响账号），control-panel 再对
  effective 账号逐个 nudge 降低 poll 延迟。
- admin 全局关闭时用户 toggle 置灰 + "管理员已全局停用群聊"。
- admin 全局闸 RPC 挂在 FeishuChannelConfigService 下（GetPlatformConfig /
  SetPlatformConfig），HTTP 为 `GET/PUT /api/admin/channel-platform`，UI 在
  admin Configurations ▸ 渠道（新 section，全局停用需内联二次确认）。

### 5.2 群聊行为

- **触发**：只响应 @机器人 消息——应用只需 `im:message.group_at_msg:readonly`
  （权限即过滤器，避免敏感的全量群消息权限）。实现上 transport 以
  `mentions 非空` 判定 @（该权限契约下推到长连接的群消息必然 @ 了 bot），
  worker 侧 `mentioned=False` 的群消息跳过。
- **占位符剥离**（`_strip_mention_placeholders`）：**行首**提及（即 @bot 触发词）
  整体剥离——所以 "@bot /new" 正常命中命令；**句中**提及替换为 `@名字`
  保留人类可读语义（无名字则移除）。
- **会话**：每群独立 session——`channel_binding` 唯一索引
  `(account_id)` → `(account_id, feishu_chat_id)`（sqlite/PG 各带幂等迁移，
  旧单行索引退役）；群里 `/new` 只重置该群。附带修正：**单聊也切 per-chat**——
  `all` 模式下多个访客各有独立会话（此前全账号共享一个 session）。
- **访问**：拉 bot 进群即授权，群内任何成员 @ 均可触发
  （`single_chat_access_mode`/owner gate 只管单聊）；权限/AUQ 卡片仍只允许
  发起人点击。`/link` 在群内**不做绑定处理**（照常作为 prompt 进 run）——
  绑定只在私聊完成，避免绑定码在群里被动公开的歧义。
- **可见性**：流式卡片发群、全群可见——用户开启群参与即接受此语义。

### 5.3 入站 gate 最终形态

```
chat_type == "p2p"   → 单聊链路: single_chat_access_mode + owner gate
chat_type == "group" → effective_group_enabled?
                        ├─ 否 → 跳过(保留 meta 日志)
                        └─ 是 → 仅 @机器人 消息进 pipeline (per-group session)
```

## 6. 裁定记录

| # | 裁定 | 日期 |
|---|---|---|
| 1 | 未绑定时 `owner_only` 放行（兼容现有用户） | 2026-07-23 |
| 2 | 触发词 `/link` + `/绑定`；码存 hash、10min TTL、一次性 | 2026-07-23 |
| 3 | owner/群开关变更走 digest re-arm（接受短暂重连）；link_code 列不入 digest | 2026-07-23 |
| 4 | 群聊：用户 opt-in（默认关）+ admin 仅全局闸，不做单用户 admin 控制 | 2026-07-23 |
| 5 | 群内任何成员 @ 可触发（不做群内 owner-only）；只响应 @ 消息 | 2026-07-23 |
| 6 | 绑定标识存 union_id（open_id 仅快照）；兜底文案中文 | 2026-07-23 |
| 7 | digest 折合成后的 effective_group 位；全局翻转走 recompute_digests 精准 re-arm | 2026-07-23 |
| 8 | 单聊会话同步切 per-chat（多访客各自独立 session）；群内 `/link` 不做绑定 | 2026-07-23 |
| 9 | slash command 走方案①引导卡片（飞书无注册 API）：绑定成功 + `/help` 触发；欢迎卡仅 🆕 按钮（不含 clear/compact），`/help` 卡三按钮，clear/compact 按 `open_chat_id` 触发完整 run | 2026-07-24 |
| 10 | DM 卡片里 AskUserQuestion 显示名改为 `Question`；已回答的选择用 markdown 列表（`- **问**：答`）渲染 | 2026-07-24 |

## 7. 飞书应用权限清单（用户侧配置指引）

| 权限 | 用途 | 必需性 |
|---|---|---|
| `im:message.p2p_msg:readonly` | 接收单聊消息 | 必需 |
| `im:message:send_as_bot` | 发送消息/卡片 | 必需 |
| `im:resource` | 拉取图片/文件资源 | 图片消息必需 |
| `im:chat:readonly` | 群信息（群名日志 + 会话列表群名） | 诊断/会话列表可选 |
| `im:message.group_at_msg:readonly` | 群 @ 消息 | Phase 2 |
| `contact:user.base:readonly` | 私聊对方人名（会话列表） | 会话列表可选，缺失降级为 chat_id |

## 8. 已激活会话列表（2026-07-23）

用户飞书设置页底部列出该账号的全部 `channel_binding` 行：类型 chip（私聊/群聊）·
对象名 · session_id（mono 缩写 + 复制）；已重置（`/new` 后 session 置空）的行标灰显示
「已重置」。展示元数据（`chat_type`/`chat_name`）由 connector 在收到该 chat 消息、
commit/detach 后打点入库（`SetBindingDisplay` RPC）——群→群名（复用诊断缓存）、
私聊→contact API 人名；**每次 arm 每 chat 只解析一次**，群改名/权限补开后需等
re-arm 刷新；名字取不到时存空串，UI 降级显示 chat_id 缩写。
CP 只读接口：`GET /api/auth/me/feishu-sessions`（激活在前、按时间倒序）。

## 9. 调研：slash command 能否接入后自动注册（2026-07-24）

**结论：不能。** 飞书开放平台没有程序化注册/更新机器人指令的 API（无 Telegram
`setMyCommands` / Slack slash command 配置的等价物），客户端也没有输入 `/`
唤起指令列表的原生能力。现有 `/new` `/link` 等指令纯靠 connector 本地文本前缀
匹配（`router.py`），飞书侧对指令一无所知，用户必须手打全文。

飞书最接近的能力是**机器人自定义菜单**（bot 单聊输入框旁的菜单按钮）：

- 只能在**开发者后台手工配置**（机器人能力配置页 → 自定义菜单编辑），无 open API；
- 点击推送 `application.bot.menu_v6` 事件（携带配置时填的 `event_key`），走现有
  WS 长连接可收（`lark_oapi` 有 `register_p2_application_bot_menu_v6`，需在
  「事件与回调」额外勾选）；connector 目前未订阅该事件；
- Model B 下每账号自带应用 → 菜单需**每个租户在自己的后台配一次**，平台无法代劳。

| 方案 | 自动化 | 说明 |
|---|---|---|
| ① 指令引导卡片（选定） | 完全自动 | 见 §9.1 定稿设计，2026-07-27 落地 |
| ② 自定义菜单（已接入） | 半自动 | 租户在开发者后台配一次菜单 + 勾选 `application.bot.menu_v6` 事件；connector 侧已实现，见 §9.3。注意 `menu_v6` 事件体只有 operator/event_key/timestamp，**无 chat_id/message_id**，需按 open_id 发送并从响应读回 p2p chat_id |

参考：[机器人菜单使用指南](https://open.feishu.cn/document/client-docs/bot-v3/bot-customized-menu)。

### 9.1 方案① 定稿设计（2026-07-24 裁定）

**触发时机**：① `/link` 绑定成功——回执由纯文本换成引导卡片；② 新本地指令
`/help`（别名 `/帮助`）随时唤起（与 `/new` 同为 connector 拦截，不进 agent）。
不做首次消息自动发（访客/群场景噪音）。

**卡片内容**——两个版本（裁定 2026-07-24：欢迎卡不含 clear/compact——刚绑定
还没开始用，上下文指令无意义；`/help` 是使用中唤起的，保留完整版）：

欢迎卡（`/link` 绑定成功，绿标题）：

```
┌────────────────────────────────────────────┐
│ ✅ 绑定成功                                 │
├────────────────────────────────────────────┤
│ 你已成为该机器人的所有者，直接发消息          │
│ 即可开始对话。每个聊天窗口是独立会话。         │
│                                            │
│ `/new`   开启新对话（别名 /新 /reset）       │
│ `/help`  查看使用指南                        │
│                                            │
│ 📷 支持直接发送图片：最多 5 张，单张 ≤ 3MB    │
│ 访问模式与会话管理请前往网页控制台 · 飞书设置  │
├────────────────────────────────────────────┤
│ [🆕 开始新对话]                             │
└────────────────────────────────────────────┘
```

使用指南卡（`/help` 唤起，蓝标题「🤖 使用指南」，去掉所有者行）：完整指令列表
（`/new` `/clear` `/compact` `/help`）+ 三按钮
`[🆕 开始新对话] [🧹 /clear] [📦 /compact]`，其余内容与欢迎卡一致。

**按钮语义**：

- 「🆕 开始新对话」→ `router.detach()`，回执「🆕 已开始新对话」；重复点击幂等。
- 「/clear」「/compact」→ 等价于用户手打该文本：按卡片回调上下文的
  `open_chat_id` 定位 chat 绑定，以 `/clear`、`/compact` 为 prompt 走完整
  run（dial + 流式卡片，SDK 解释指令）。实现上需把 worker 的 run pipeline
  抽出为消息入口与卡片入口共用。
- 点击校验沿用现有模式：仅卡片接收者本人可点（open_id 比对，`card_actions.py`
  新增 `kind`，如 `menu`，value 携带 cmd + 允许的 open_id）。
- 卡片留在聊天记录中即常驻菜单；文案（图片限制/别名）对齐 §2.2 已实现行为。

### 9.2 落地实现（2026-07-27）

| 文件 | 改动 |
|---|---|
| `menu_cards.py`（新） | 纯渲染：`welcome_card` / `help_card`（schema 2.0 + `header.template` green/blue）、`MENU_COMMANDS` 白名单、卡片失败用的纯文本兜底 |
| `router.py` | `HELP_COMMANDS = {/help, /帮助}` → `Decision(kind="help")`（与 `/new` 同为本地拦截，不进 agent） |
| `worker.py` | `/link` 成功回执换成欢迎卡（失败仍纯文本）；`kind=="help"` 回使用指南卡；`handle_menu()` 把按钮点击**合成一条入站消息**走同一条管道；模块级 `_ACTIVE` 注册表（arm 注册 / teardown 先注销） |
| `card_actions.py` | `value.act == "menu"` 分支前置于 pending 查找（引导卡片不登记 pending，重启后照样可点） |
| `lark_ws.py` | card action 解析补 `operator.union_id`（合成消息要过 owner gate） |

**关键取舍**：按钮不是绕过 access gate 的后门——合成消息照走群聊闸 / owner gate；
`message_id` 留空使表情反应链路自动 no-op。union_id 优先取本次点击的 operator，
回落到卡片 value 里记的那个（open_id 已比对，同一人）。

### 9.3 机器人自定义菜单（方案②，2026-07-27）

租户在开发者后台配置菜单项 + 在「事件与回调」订阅 `application.bot.menu_v6` 后，
点击推送 `event_key`，connector 映射到与卡片按钮**完全相同**的合成消息管道。

平台约定的 event_key（`menu_cards.BOT_MENU_KEYS`）：

| event_key | 菜单文案 | 注入指令 | 回执后的内容 |
|---|---|---|---|
| `new_session` | /new 重置会话 | `/new` | detach **完成后**发「✅ 会话已成功重置」卡 |
| `compact` | /compact 压缩会话 | `/compact` | 「📦 正在压缩会话…」卡（点点 1→2→3 循环）→ 就地 patch 成「✅ 会话已压缩」 |
| `help` | /help 查看帮助 | `/help` | 使用指南卡（§9.1） |
| `list_skill` | /skill 查看 skill | `/skill` | skill 清单卡（原生表格） |
| `session_info` | /info 会话信息 | `/info` | 当前会话信息卡（见下） |

**指令回执**：菜单/按钮点击先回一条「🔔 收到菜单指令：`<cmd>`」——飞书菜单点击本身
没有任何视觉反馈（不像普通消息有表情生命周期），必须给即时确认；随后才是指令内容。

**chat_id 解析**：菜单事件没有 chat_id。worker 维护模块级 `_P2P_CHATS`
（`(account, open_id) → chat_id`，任何入站私聊消息都会写一笔，跨 re-arm 存活）；
未命中时上面那条回执改按 open_id 投递，从 `im.v1.message.create` 的响应体读回 p2p
`chat_id` 并回填缓存——一次调用同时完成"确认"和"解出会话"。解不出会话则记 warning
丢弃，不炸线程。**access gate 先于回执**：非授权用户点菜单一条消息都发不出去。

**`/skill`（`/skills` `/技能`）**：新增 `skills.py`，走 dial.py 同一接入面
（wake + `X-Priva-Runner-Token`）只读 GET ar pod 的
`/api/sandbox/resource/skills/`，渲染成清单卡——**飞书原生 `table`**（复用
`cards.answer_elements` / `native_table`，与终态卡同一实现），每个分组一张两列表，
`enabled=false` 整行标灰「已停用」，超过 30 条报「另有 N 个未列出」。拉取失败回一句
「⚠️ 暂时读不到 skill 列表」，绝不静默。

**`/info`（`/信息`）**：唯一被**改写**而非拦截的指令——router 把它换成 CLI 原生
`/context` 在当前会话真跑一次，输出渲染成「📊 当前会话信息」卡：`## Context Usage`
上提成标题、会话 id 进副标题（**裸 id**，飞书标题组件拒绝任何字号字段——实测
`ErrCode 11311: header text_size invalid`，减轻视觉重量只能靠减字）、
`**Model:**`→`**模型：**`、`Estimated usage by category`→`Token 消耗大致分布`，
其余原样跟着 CLI 走，三张表全部是原生表格。跑挂/无输出时退回通用终态卡并把
`**Session id:**` 补进正文——错误原样可见，会话 id 绝不丢。

**上下文洁净性（实测 2026-07-27）**：同一会话连跑三次 `/context`，session id 不变、
`**Tokens:**` 恒为 274、`Messages` 行始终不存在 → CLI 的本地指令输出不进会话上下文。
使用指南卡据此明示「所有指令的输出不会出现在对话的上下文中」。

**卡片形态实测**：无 header 的流式卡 `patch` 成带 header 的卡（`/info` 走这条）飞书
接受；`header.template` green/blue/wathet/indigo 均正常。
