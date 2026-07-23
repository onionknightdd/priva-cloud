# 飞书 DM 功能设计（channel-connector）

> 状态：Phase 0 已落地 · Phase 1 代码完成 · Phase 2 代码完成（均待镜像部署验证）
> 关联文档：`docs/im-channel-permission-zh.md`（权限/AUQ 交互协议）
> 更新：2026-07-23

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

## 7. 飞书应用权限清单（用户侧配置指引）

| 权限 | 用途 | 必需性 |
|---|---|---|
| `im:message.p2p_msg:readonly` | 接收单聊消息 | 必需 |
| `im:message:send_as_bot` | 发送消息/卡片 | 必需 |
| `im:resource` | 拉取图片/文件资源 | 图片消息必需 |
| `im:chat:readonly` | 群信息（群名日志） | 诊断可选 |
| `im:message.group_at_msg:readonly` | 群 @ 消息 | Phase 2 |
