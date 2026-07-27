---
name: remote-agentsandbox
description: "云端 agent sandbox 的 programmatic 调用：通过网关的 SSE 流式契约把任务交给远端 Priva agent 执行，并展示返回结果。当用户希望把工作委派给远端/托管的 Priva agent sandbox、而不是在本地执行时，使用本技能。网关域名在首次调用时由 AGENT_SANDBOX_GATEWAY_URL 环境变量提供，之后自动持久化复用。一旦建立了远端会话，同一上下文中的后续追问应继续使用本技能；多轮远端会话由本技能自动管理。"
metadata:
  icon: Server
  icon_color: "#79c0ff"
---

# 云端 Agent Sandbox — Programmatic 调用

此技能让你以编程方式调用一个远端的云端 agent sandbox：把任务通过网关的
SSE 流式契约交给远端 agent 执行，结果以 `result` 事件返回。

远端 agent 是一个具备完整工具权限的独立 sandbox，你只是它的一个普通 API 客户端——
这里没有新协议，用的就是运行时标准的 `POST /api/sandbox/agent/run/stream`。

## 首次使用配置

调用脚本前需要两样东西，都存放在 **`<当前工作目录>/.agentsandbox-gateway/`** 下。
脚本以**当前工作目录**（`Path.cwd()`）为基准定位该目录——每个工作目录独立维护一份配置，
与本技能的安装目录解耦。

### ① 网关地址（无默认值）

脚本**不内置任何默认网关地址**——猜错地址会把请求静默发到错误的集群。
地址来源按顺序解析：

1. 环境变量 `AGENT_SANDBOX_GATEWAY_URL`
2. `./.agentsandbox-gateway/session.json` 里的 `gateway_url` 字段

两者都没有时脚本以**退出码 1** 失败并打印指引。此时应当**向用户索取网关域名**，
然后带上环境变量重跑一次——脚本会自动把规范化后的地址持久化进 `session.json`，
之后的调用不必再带该环境变量：

```bash
AGENT_SANDBOX_GATEWAY_URL="agent.example.com" \
  python3 <skill-path>/scripts/prod_call.py --prompt "任务描述"
```

环境变量填**网关域名**即可（`agent.example.com`）；也接受带 scheme 的 origin
（`https://agent.example.com`）或已经写全的完整端点 URL——脚本会规范化，
不会重复拼接路径。省略 scheme 时按 `https` 处理。

### ② Bearer token

`./.agentsandbox-gateway/auth` 文件，内容为云端 agent sandbox 的 **Bearer token**
（明文、单行、不要带引号、不要带多余换行）。文件不存在时脚本以退出码 1 失败。

此时向用户索取 token，并用以下命令写入（**必须用 `printf`，不要用 `echo`**，
避免追加换行；也不要把 token 直接写在 shell history 里）：

```bash
mkdir -p ./.agentsandbox-gateway
printf '%s' "$TOKEN" > ./.agentsandbox-gateway/auth
```

脚本启动时自动读取该文件并放入 `Authorization: Bearer ...` 请求头，
token 明文永远不会出现在 stdout/stderr 里。

> 由于配置位于工作目录而非技能安装目录，通过 Skill Hub 重新安装本技能不会影响它们。
> 但切换到新的工作目录时需要重新配置。

## 关键约束 — 同一 session 禁止并发调用

**同一个 `session_id` 在同一时刻只能有一个进行中的调用。**
切勿在一次调用尚未返回时，再发起另一次使用相同 `session_id` 的调用
（包括：并行的 Bash 工具调用、后台任务、循环中未 await 的重复调用等）。

**原因**：远端 sandbox 基于 `session_id` 维护会话上下文与状态文件。
并发调用会造成会话状态覆盖、消息顺序错乱、`session.json` 写入竞争，
最终导致**数据损坏（data corruption）**，后续追问将拿到不一致的上下文。

**如何遵守**：
- 对同一 `session_id` 的调用必须**严格串行**：等待上一次调用返回（脚本退出、
  stdout JSON 解析完成、`session.json` 写入完成）后，再发起下一次调用。
- 如果确实需要并行处理多个独立任务，请为每个任务使用**不同的新会话**
  （即不传 `--session-id`，让远端各自创建新 session），而不是共享同一个。
- 永远不要把同一次 `prod_call.py` 调用放到多个并行的 tool call 里。

**脚本会强制 fail-fast**：传入 `--session-id` 时，脚本会用 `fcntl.flock`
在 `./.agentsandbox-gateway/<session_id>.lock` 上取**非阻塞**独占锁。
如果同一 session 已有进行中的调用，第二次调用会立即以 **退出码 4** 失败、
不发送 HTTP 请求。看到 exit 4 后应该等当前调用返回再串行重试，
而不是立刻重发。新会话（不传 `--session-id`）不加锁——它们彼此独立。

## 工作原理

1. 用户下达一条希望交给远端 sandbox 执行的任务。
2. 通过 python3 运行 `scripts/prod_call.py`，传入 prompt。
3. 脚本解析网关地址、读取 `./.agentsandbox-gateway/auth`、构建 priva 风格 payload、
   以 SSE 形式调用网关、流式读取响应。
4. 中间事件（`assistant` / `tool_use` / `tool_result` / `task_*` 等）全部丢弃，
   只留下终态 `result` 事件的 `data` JSON 输出到 stdout。
5. 向用户展示结果。

## 详细流程

### 第一步 — 判断是新会话还是继续上一轮

> ⚠️ **默认行为是新建会话，不是继续。** 只有当本轮请求与上一轮存在**明确的、文本可验证的延续关系**时，才传 `--session-id`。
> 仅仅因为 `session.json` 存在就复用 `session_id` 是**错误**的——会把无关话题塞进同一会话，污染远端 agent 的上下文。

脚本本身不维护会话状态——会话状态由你在
`<当前工作目录>/.agentsandbox-gateway/session.json` 中管理，结构如下：

```json
{
  "gateway_url": "https://agent.example.com/api/sandbox/agent/run/stream",
  "session_id": "6776417b-8339-403d-820c-6a1b0a7e453a",
  "last_used": "2026-03-03T10:30:00Z",
  "topic": "分析 payment 模块的测试覆盖"
}
```

> `gateway_url` 由脚本写入并读取。你在更新会话状态时**必须原样保留它**，
> 否则下次调用会退回"网关地址未配置"并要求用户重新提供。

#### 决策流程（按顺序，任一条不满足就**新建**）

1. **用户是否明确指令？**
   - "新会话" / "new session" / "重开" / "重新开始" → **强制新建**（不传 `--session-id`）
   - "继续上次" / "接着刚才" / "用之前那个会话" → **强制继续**（前提：`session.json` 里有 `session_id`）
   - 没有明确指令 → 进入下一步

2. **`session.json` 里是否有 `session_id`？**
   - 没有 → **新建**
   - 有 → 进入下一步

3. **`last_used` 是否在 10 分钟以内？**
   - 超过 10 分钟 → **新建**（视为会话过期）
   - 10 分钟内 → 进入下一步

4. **本轮请求与 `topic` 是否同主题？**
   - 主题不同（关键词、对象、领域均无重叠）→ **新建**
   - 同主题 → 进入下一步

5. **请求中是否包含可指认的延续信号？必须命中以下之一：**
   - 指代词回指上轮输出："它"、"这个"、"刚才那个"、"上面的"、"上一条"
   - 顺承/递进词："继续"、"再"、"接着"、"然后"、"那现在"
   - 隐含引用上轮结果（动作作用于上轮发现的对象）：上轮"分析测试覆盖" + 本轮"把缺口补上" ✅
   - 明确状态追问："怎么样了"、"好了吗"、"还在跑吗"
   - 命中 → **继续**（传 `--session-id`）
   - 未命中 → **新建**

> **拿不准时一律新建**——污染上下文比多建一个会话代价大得多。
> 不要用"省 token / 省资源 / 保持上下文"作为复用 session 的理由。

#### 反例（不要这么做）

| 上轮 topic | 本轮请求 | 错误判断 | 正确判断 | 原因 |
|------------|----------|----------|----------|------|
| 分析测试覆盖 | 查一下昨天的 API 报表 | 复用 session_id | 新建 | 主题完全不同 |
| 重构 auth 模块 | 帮我生成一份周报 | 复用 session_id | 新建 | 主题不同 |
| 任何 | （`last_used` 是昨天） | 复用 session_id | 新建 | 已过期 |
| 分析 payment 模块 | 帮我分析 billing 模块 | 复用 session_id | 新建 | 对象不同（payment ≠ billing） |
| 跑数据清洗任务 | 现在怎么样了？ | 新建 | 复用 | "怎么样了"是状态追问，明确回指 |
| 分析测试覆盖 | 把缺口补上 | 新建 | 复用 | "缺口"隐含上轮发现的对象 |

### 第二步 — 调用脚本

```bash
python3 <skill-path>/scripts/prod_call.py \
  --prompt "任务描述" \
  [--session-id "之前的session_id"] \
  [--verbose]
```

脚本会：
- 解析网关地址（环境变量 → `session.json` 的 `gateway_url`）
- 从 `./.agentsandbox-gateway/auth` 读取 Bearer token
- 构建 priva `AgentRunRequest` 形态的 JSON body：
  - 始终包含 `message`（即 prompt）
  - 传入了 `--session-id` 时才包含 `session_id` 字段（否则字段省略 = 新会话）
- 用 `Authorization: Bearer <token>` 调用 SSE 网关
- 流式读取 SSE 事件，捕获终态 `result` 事件
- 将 `result` 事件的 `data` JSON **原样**输出到 stdout
- 成功返回 0，失败返回非 0

#### `--verbose` 中间过程日志

加上 `--verbose` 后，脚本会把所有 SSE 事件实时（每个事件一行、`flush` 落盘）
写入到 `./.agentsandbox-gateway/<session_id>.jsonl`：

- **继续会话**（传了 `--session-id`）：直接 append 到 `<session_id>.jsonl`，
  多轮调用形成同一文件里递增的事件流。
- **新会话**（未传 `--session-id`）：先写到 `_pending.<pid>.jsonl`，
  收到 `result` 事件后自动重命名为 `<返回的 session_id>.jsonl`。

每行格式：

```json
{"ts": "2026-05-11T03:42:17.123Z", "event": "assistant", "data": { ... 原始 SSE payload ... }}
```

事件类型涵盖 `stream_init / assistant / tool_use / tool_result / user_message / system /
hook_event / task_started / task_progress / task_notification / rate_limit_status /
queue_flush / session_reset / retry_attempt / retry_exhausted / stream_error / result`。
（网关的保活心跳是 SSE 注释而非事件，不会出现在日志里。）

**何时启用 `--verbose`**：
- 复杂任务、用户希望看到远端 agent 的中间步骤（"它具体做了什么？"、"展示一下过程"）
- 调试远端 agent 的失败或异常行为
- 需要回过头查看历史会话每一步执行细节时

**如何把中间过程展示给用户**：
完成后，从 stdout 拿到的 `result` JSON 里取出 `session_id`，
然后读取 `./.agentsandbox-gateway/<session_id>.jsonl`（JSON Lines 格式，每行独立解析）。
对用户呈现时建议按时间顺序概述关键 `tool_use` / `tool_result` / `task_*` 事件。

### 第三步 — 解析并展示响应

stdout 上的 JSON 直接来自 priva 的 `result` SSE 事件，结构形如：

```json
{
  "type": "result",
  "subtype": "success",
  "session_id": "6776417b-…",
  "is_error": false,
  "num_turns": 3,
  "duration_ms": 4821,
  "duration_api_ms": 3120,
  "stop_reason": "end_turn",
  "total_cost_usd": 0.0125,
  "usage": { "input_tokens": …, "output_tokens": … },
  "result": "…远端 agent 的 markdown 回答…"
}
```

关键字段：

| 字段 | 含义 |
|------|------|
| `result` | 远端 agent 的回答，markdown 字符串，可直接渲染 |
| `session_id` | 后续追问需要传回的 Session ID |
| `is_error` | 远端执行过程中是否遇到错误 |
| `subtype` | `success` / `error_max_turns` 等终止原因 |
| `num_turns` | 远端 agent 执行的轮数 |
| `duration_ms` | 总耗时（毫秒） |
| `usage` | token 使用量 |

**向用户展示结果的方式：**

1. 将 `result` 字段作为主要内容展示（markdown，直接渲染）。
2. 附加简短的元信息：
   - 耗时：`duration_ms` 毫秒转换为秒
   - 轮次：`num_turns`
3. 如果 `is_error` 为 `true`，提醒用户并展示错误详情（通常 `result` 中已包含）。
4. **务必**将新的 `session_id` 和简短的话题摘要写入会话状态文件，
   以便后续判断是否继续会话。

### 第四步 — 更新会话状态

调用成功后，更新会话状态。**先读出现有内容再写回**，
不要用整体覆盖的方式丢掉脚本写入的 `gateway_url`：

```bash
python3 - <<'EOF'
import json, pathlib, datetime
p = pathlib.Path("./.agentsandbox-gateway/session.json")
state = json.loads(p.read_text()) if p.exists() else {}
state.update({
    "session_id": "<从响应中提取>",
    "last_used": datetime.datetime.now(datetime.timezone.utc)
                  .isoformat(timespec="seconds").replace("+00:00", "Z"),
    "topic": "<用户请求的简短摘要>",
})
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
EOF
```

## 错误处理

- **网关地址未配置**：脚本以退出码 1 失败，stderr 打印配置指引。按"首次使用配置 ①"
  向用户索取网关域名，带 `AGENT_SANDBOX_GATEWAY_URL` 重跑一次即可（自动持久化）。
- **`auth` 文件缺失或为空**：退出码 1。按"首次使用配置 ②"写入 token 再重试。
- **网络错误 / SSE 读取中断**：退出码 2。告知用户网关无法访问；
  地址填错（域名不存在、TLS 握手失败）也会落到这里——先核对 `gateway_url`。
- **API 返回非 2xx**：退出码 3。展示 HTTP 状态码和响应体（stderr 内容）。
  `401` 通常意味着 `auth` 里的 token 失效或不属于该网关。
- **SSE 流结束但未收到 `result` 事件**：退出码 3。可能远端异常。
- **收到 `stream_error` 或 `retry_exhausted` 事件**：退出码 3。
  脚本会把该事件的 payload 写到 stderr。
- **`is_error` 为 true**：远端 agent 执行了但遇到问题。展示 `result` 内容
  （通常包含错误详情），建议用户换个方式描述。
- **退出码 4（并发冲突）**：同一 `session_id` 已有其他进行中的调用。脚本不会
  发送 HTTP 请求，stderr 会指出冲突的 lock 文件路径。等当前调用结束后**串行重试**，
  不要立刻重发——并发会损坏远端 agent 状态。
- **远端需要人工确认的工具**：本通道是非交互的，远端如果命中需要审批的工具会
  **fail closed**（直接拒绝并在结果里说明原因），不会挂起等待。遇到这种拒绝时，
  把原因转述给用户，必要时改用其他方式执行该操作。
- **超时**：复杂任务可能需要 2 分钟以上。脚本设置了 **5 分钟整体 wall-clock 超时**，
  按每一行 SSE 数据（含网关的保活心跳）检查累计耗时，因此远端静默卡死时同样会中止，
  退出码 2。如果超时，告知用户并建议拆分为更小的步骤。

## 安全说明

- Bearer token 是敏感凭据：**不要**在响应中回显其内容、**不要**提交到
  代码仓库、**不要**写入日志。脚本只把它放在 `Authorization` 请求头里发往
  网关，不会打印到 stdout/stderr。
- 远端 sandbox 里的 agent 拥有真实的工具权限，其操作会作用于该 sandbox 的
  工作区与它可访问的资源。执行破坏性操作（删除、覆盖、对外发送）前，
  必须先向用户确认："即将在远端 sandbox 执行[操作]，是否继续？"，
  只有在用户明确确认后才调用。
- 只读操作（状态检查、查看文件、数据查询）无需确认，直接执行。

## 示例

**用户**："让云端 agent 分析一下 payment 模块的测试覆盖情况"

→ 新话题，不传 session_id：
```bash
python3 <skill-path>/scripts/prod_call.py \
  --prompt "分析 payment 模块的测试覆盖情况，列出未覆盖的关键分支"
```

**用户**（追问）："把缺口补上"

→ 继续上一轮会话。读取会话状态，先向用户确认（会改动文件），然后：
```bash
python3 <skill-path>/scripts/prod_call.py \
  --prompt "为上一步列出的未覆盖分支补充单元测试" \
  --session-id "6776417b-..."
```

**用户**："查一下昨天的 API 调用量报表"

→ 新话题，只读操作，无需确认：
```bash
python3 <skill-path>/scripts/prod_call.py \
  --prompt "查询昨天的 API 调用量统计报表"
```
