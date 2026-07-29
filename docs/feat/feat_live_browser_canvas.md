# Canvas 真浏览器（Live Browser）设计

> 状态：决策已关闭，可进入实现 · 2026-07-27
> 目标：在 Canvas 中提供真实、可登录、可持久化的 Chromium；agent 与用户观察同一个
> browser context，并通过明确的控制权协议交接操作。
> ADR-01/02/03 与 P0-04~06 已于 2026-07-27 逐项决议，结论见 §0.1 与 §16。
> 注意：ADR-01 选定 sidecar、P0-06 选定 `--no-sandbox`，两项叠加后浏览器与 agent 之间
> 不存在进程级或网络级隔离边界。该残余风险已显式接受，登记于 §12.3，发布说明必须包含。

---

## 0. 决策结论

原方案的价值成立：真实网络会话、持久登录态、agent 操作实时可见，明显优于当前的静态
`srcDoc` HTML 预览。评审提出的六个阻断项已全部决议如下。

| ID | 议题 | 决议（2026-07-27） | 落点 |
|---|---|---|---|
| ADR-01 | 部署与网络隔离 | **sidecar，不做独立 pod**。同 netns 残余风险显式接受 | §2.3 §2.4 §12.3 |
| ADR-02 | 用户/agent 控制权 | **browserd 强制单 controller lease**；补充 user lease 90 秒空闲释放 + 静默重获规则 | §3.1 |
| ADR-03 | 能力开关作用域 | **v1 即做按账户开关**，复用 `UserRecord` 既有的 null=继承 覆盖模式 | §8.3 |
| P0-04 | context/target | browserd 维护唯一 persistent context 与 active target；UI 只显示 active target | §4 |
| P0-05 | 帧协议与 viewport | 固定 canonical viewport；帧带 generation/sequence/CDP metadata | §6.3 §6.4 |
| P0-06 | sandbox / profile | **`--no-sandbox`，风险接受**；profile 改为本地盘 + 关机导出，不直接落 NFS | §5.1 §7.1 §12.3 |

评审外新增的两项 v1 范围（无需决策，不做会导致功能不可用）：

| ID | 议题 | 处理 | 落点 |
|---|---|---|---|
| P0-07 | headless 不渲染原生控件 | **v1 实现 `<select>` 弹层拦截**；右键菜单/打印/自动填充气泡写入已知限制 | §6.5 §11 |
| P0-08 | JS 对话框阻塞渲染器 | `alert/confirm/prompt/beforeunload`、证书 interstitial、HTTP basic auth 全部纳入协议 | §6.2 §6.6 |

### 0.1 决策依据速查

- **为什么不选独立 pod**：独立 netns 会切断"预览 agent 刚起的 dev server"这一最高频用法
  （浏览器的 `localhost` 不再是 runner 的 `localhost`），且需新增一整套 wake 机制。
  详见 §2.2。
- **为什么 `--no-sandbox` 可接受**：容器内启用 Chromium sandbox 需放宽容器 seccomp
  （自定义 profile 下发到每个节点，或设为 Unconfined），是"用一种放松换另一种收紧"；
  且托管集群不一定允许改节点。取舍与残余风险见 §12.3。
- **为什么按账户开关不再是重活**：`UserRecord.cpu_cores / memory_mb`
  （`libs/common/src/priva_common/models/auth.py:79-80`）已经是完整的
  "null 继承 / 有值覆盖"链路，加 `browser_enabled` 是沿用既有模式而非新建。详见 §8.3。

### 0.2 术语

- **browser instance**：一个 Chromium 进程族。
- **browser context**：持久化 profile 对应的默认 context。
- **target**：CDP 页面、popup 或 worker；v1 只向 Canvas 投放一个 active page target。
- **viewer**：已连接帧流的 Canvas 客户端，默认只读。
- **controller**：当前唯一有权向页面发送输入或导航命令的一方，取值 `user | agent | none`。
- **browser lease**：browserd 签发并强制执行的控制权租约。
- **browser generation**：每次 Chromium 重启递增的编号，用于丢弃旧帧和旧控制消息。
- **stream revision**：active target 或 canonical viewport 变化时递增，用于阻止输入落到用户未看到的页面。

## 1. 背景与范围

Canvas 当前的「Browser」不是网络浏览器：`BrowserViewport.jsx` 使用
`sandbox="allow-scripts"` iframe 和 `srcDoc` 渲染 HTML 字符串，配合
`web/user/src/utils/inspectorBridge.js` 做 hover/select。状态在 `browserDebugStore.js`。
它没有 URL 导航、网络会话和 cookie，本质是“HTML 预览 + 元素审查器”。

候选方案：

| 方案 | 结论 |
|---|---|
| iframe 加载真实 URL | 只适合沙箱 dev server；外站常被 `X-Frame-Options`/`frame-ancestors` 阻止，跨域 inspector 不可用 |
| headless Chromium + CDP 投屏 | 本设计；真实浏览器，agent/用户共享 context，流量由租户浏览器运行时发出 |
| Xvfb + 完整 Chrome + noVNC | 资源和带宽成本高、文字模糊、输入协议过重，不采用 |

### 1.1 v1 目标

1. 用户可在 Canvas 导航和登录网站，profile 跨 pod 重启保存。
2. agent 通过平台托管 MCP 操作同一 persistent context。
3. 用户实时旁观，并通过显式操作获得或归还控制权。
4. 浏览器、帧流和输入都有资源上限、生命周期、健康检查和无敏感内容的指标。
5. LIVE 不可用时，现有 HTML 预览/审查器仍可使用。

### 1.2 v1 非目标

- 不提供完整远程桌面、音频或视频采集。
- Canvas 不提供多 tab 条；browserd 内部仍需正确处理 popup/临时 target。
- 不保证 WebAuthn/passkey、摄像头、麦克风、DRM、扩展程序和系统文件选择器。
- 不把 allowed/blocked origins、iframe sandbox 或 LLM prompt 当作安全边界。
- 不承诺规避 CAPTCHA 或网站自动化检测。

## 2. 部署架构与隔离决策

### 2.1 共同的外部数据路径

对浏览器 viewer 的外部路径与部署形态无关（选定 sidecar 后仍然成立，也是未来若切回独立 pod
时不需要改动的部分）：

```text
SPA Canvas Browser/LIVE
  │  wss://<host>/api/browser/ws
  │  Sec-WebSocket-Protocol: priva.ws.v1, priva.token.<jwt>
  ▼
agentgateway
  │  HTTPRoute /api/browser → InferencePool "browsers"
  ▼
control-panel EPP
  │  验 JWT → 查 capability/deployment generation → 唤醒
  │  → 注入 x-priva-browser-authorized: 1 → steer browserd:8093
  ▼
browserd :8093
  │  帧流、viewer 控制协议
  ▼
Chromium → 外部网站
```

control-panel 不进入 WebSocket 字节流；JWT 在边缘校验。`x-priva-browser-authorized` 只有在
NetworkPolicy 确实禁止租户工作负载绕过 edge 时才构成有效的第二层保护。

### 2.2 方案 A：独立 `browse-{account}` pod（ADR-01 已否决 · 存档备查）

2026-07-27 决议不采用。否决理由：(1) 独立 netns 后浏览器的 `localhost` 不再是 runner 的
`localhost`，"预览 agent 刚起的 dev server"这一最高频用法会断，需额外做地址改写 +
runner 端口白名单；(2) 需新增 `spec.browserWake`、pod IP 回写、operator reconcile 与独立
Deployment，首版可见效时间后延约 3~4 天。本节保留原分析，供隔离要求升级时重启该方案。

```text
ar-{account} pod                         browse-{account} pod
┌────────────────────┐                  ┌────────────────────────────┐
│ runner / MCP       │ -- scoped auth →│ browserd                   │
│                    │   internal CDP   │  ├─ controller/target mgr  │
└────────────────────┘                  │  └─ Chromium via debug pipe│
                                        └────────────────────────────┘
SPA ───────── gateway/EPP ──────────────────────┘ :8093
```

优点：

- 浏览器拥有独立 netns、ServiceAccount、cgroup、镜像和 egress policy。
- 可以阻止浏览器访问 runner localhost、cluster service 和云 metadata，同时保留 runner 的
  合法内部访问。
- Chromium OOM/崩溃不会直接重启 runner 容器；镜像增量也不会施加给未启用浏览器的账户。

代价：

- 新增 `spec.browserWake`、pod IP/status 回写、operator reconcile、独立 Deployment 和冷启动。
- runner 到 browserd 的内部 CDP/MCP 路径必须鉴权，不能暴露裸 CDP。

内部连接要求：

- browserd 是唯一 CDP broker，Chromium 使用 `--remote-debugging-pipe`，不监听裸 TCP CDP。
- runner 使用短期、账户绑定、run/session 绑定的 token；通过托管 wrapper 或
  Playwright MCP `--cdp-header` 发送。
- discovery response 中的 `webSocketDebuggerUrl` 必须继续指向 browserd，不能泄露或绕过
  broker。
- NetworkPolicy 只允许对应 runner、agentgateway 和 operator 访问 browserd 所需端口。
- browser pod 挂载同账户的 RWX workspace PVC，使 `/workspace/.browser` 和
  `/workspace/Downloads` 对 runner 可见；browser 容器不得挂载其他平台 secret/volume。

### 2.3 方案 B：runner pod sidecar（ADR-01 选定 · 2026-07-27）

优点是复用 runner wake、共享 `/workspace`、连接简单，且 dev server 预览天然可用
（与 runner 同 netns，`localhost:PORT` 直接可达，无需任何打通工作）。

以下限制为**已接受的残余风险**，必须如实写入发布说明：

1. runner 与 browserd 共享 netns，Kubernetes NetworkPolicy 无法给二者配置不同 egress。
2. 浏览器可访问 runner localhost，以及 runner 被允许访问的内部服务。
3. sidecar 独立 memory limit 能降低直接资源争抢，但不能保证 node pressure 或 pod eviction
   不影响 runner。
4. browserd 的 env 白名单只隔离环境变量；同 UID、共享卷和同 pod 仍不是强安全边界。

强制要求（不因选定 sidecar 而放宽）：

- **Chromium 必须使用 `--remote-debugging-pipe`，不得监听 `127.0.0.1:9223`**。这是 sidecar
  模式下唯一阻止 runner 绕过 browserd 的机制——一旦存在可连接的 TCP CDP 端口，租户 agent
  可直接驱动浏览器，lease、限速与审计全部失效。
- MCP 只连接 browserd 管理端点 `127.0.0.1:9222`。
- 文档和产品不得把 loopback 描述为浏览器与 runner 之间的隔离。
- egress policy 只能按整个 pod 收紧；无法满足浏览器独立 egress 的客户不得启用此模式。
- browserd 仍需执行 controller lease、target 管理、限速和审计，不得仅做透明反向代理。

### 2.4 ADR-01 决议与验收

**决议（2026-07-27）：采用 sidecar，不做独立 pod。**

实现期验收项：

- [x] 部署模型已定：sidecar，浏览器容器与 runner 同 pod。
- [ ] 安全负责人书面接受"同 netns + 未沙箱化 Chromium"的组合残余风险（§12.3）。
- [ ] Chromium 不存在可绕过 browserd 的 TCP CDP endpoint（debug pipe 实测验证）。
- [ ] runner→browserd 身份、token TTL、作用域和撤销方式已定义。
- [ ] 浏览器无法访问云 metadata、其他租户和未授权集群服务的集成测试通过。
- [ ] egress 收紧策略按整 pod 生效，且不破坏 runner 既有的合法内部访问。

## 3. 控制权和并发模型

“用户随时接管”必须是 browserd 强制执行的状态机，不是根据最近一条 CDP 命令推断的指示灯。

### 3.1 单 controller lease

browserd 对每个账户最多维护一个有效 lease：

```text
                  agent request（无人控制）
        ┌────────────────────────────────────┐
        ▼                                    │
      NONE ── user Take Control ──► USER ────┘ Return/timeout
        │                           ▲
        │ agent request             │ user Take Control（显式抢占）
        ▼                           │
      AGENT ─── tool end/timeout ───┘
```

规则：

- viewer 建连后始终是只读，只有获得 lease 才能发 `navigate/input/set_viewport`。
- 用户点击 **Take Control** 可抢占 agent。browserd 撤销 agent lease；已发出的单条 CDP
  command 可能完成，但之后的命令返回 `CONTROL_REVOKED`。
- 用户点击 **Return Control** 只释放为 `NONE`，不自动把控制权授予任意后台 agent。
- **user lease 空闲释放（90 秒）**：用户 90 秒无输入（鼠标、键盘、导航）后 lease 自动降为
  `NONE`。viewer 保持连接并继续收帧，只是变成只读。避免用户走开后 agent 被永久阻塞。
- **静默重获**：用户在释放后再次输入时，若当前 controller 为 `NONE`，browserd 直接把 lease
  重新授予该 viewer，**无需再次点击 Take Control**；若此时 controller 已是 `agent`，用户输入
  一律丢弃并在 UI 显示 Take Control 按钮，必须显式抢占。这条组合规则同时避免了两种坏情况：
  用户走开阻塞 agent、以及用户随手一点无意中打断 agent 正在执行的多步任务。
- agent 每次工具批次申请 lease；用户控制中默认 fail-fast，返回
  `BROWSER_CONTROLLED_BY_USER`，不无限排队。
- 多 agent session 同时申请时默认只有第一个成功；其他返回 `BROWSER_BUSY`。如以后增加队列，
  等待上限不得超过 30 秒，且必须绑定原 run。
- lease 含 `lease_id/account_id/session_id/run_id/owner/expires_at/generation`；agent heartbeat
  默认 30 秒，60 秒无 heartbeat 过期。
- 用户连接断开后保留 10 秒 grace；仍未恢复则释放 lease。
- Chromium 重启、管理员禁用、profile reset 会撤销所有 lease。

browserd 必须在输入入口和 MCP/CDP broker 两边校验 lease。仅在 WebSocket UI 侧禁用按钮不能
阻止 agent 绕过控制权。

标准 Playwright MCP 通常维护长连接，不能假设每条 CDP message 都会携带新的 lease。v1 必须
实现以下一种方式：

- 平台托管 adapter 按 lease 建立 CDP connection，lease 撤销时立即关闭对应 connection；或
- browserd 把鉴权后的 CDP connection 映射到 lease，并在抢占、过期、generation 变化时强制断开。

仅预置一个永久连接的 `@playwright/mcp --cdp-endpoint ...` 不满足控制权要求。

### 3.2 viewer 与 session

- 默认 `max_viewers=2`；只有一个 viewer 可以成为 user controller。
- 浏览器是**账户级资源**，不是聊天 session 级资源。连接状态和 controller 状态不能只存进
  session-local UI snapshot。
- 前台 session 的 LIVE 面板可自动切换；后台 session 的 browser tool call 只增加 badge/notice，
  不抢焦点。
- 历史 SSE replay 不触发 LIVE auto-show；只有当前 live run 的受管 browser MCP 事件可以触发。
- 多窗口同时打开同一账户时，第二个窗口默认只读，并显示 controller 所在窗口/agent 的脱敏标识。

## 4. browser context 与 target 模型

### 4.1 persistent context

- 每个账户只有一个平台管理的 persistent context。
- v1 禁止 MCP 创建额外 incognito context；相关工具应从托管工具集中移除或在 wrapper 中拒绝。
- browserd 启动后确保至少存在一个 `about:blank` page target。
- Canvas、MCP 和 screencast 必须共享同一个 `active_target_id`。

### 4.2 target 管理

虽然 v1 不展示 tab 条，browserd 仍需维护 target registry：

- 普通 `window.open`/OAuth popup 可以创建临时 page target，不能简单重定向回主页面，否则会破坏
  OAuth、支付跳转和 opener 语义。
- 新可见 popup 默认成为 active target；popup 关闭后回到 opener。
- agent 通过受管 tab 工具切换页面时，browserd 同步更新 active target 并重启 screencast。
- service worker、shared worker 和 devtools target 不得成为 active target。
- active target 关闭或崩溃时，优先回到最近可用 page；没有 page 时新建 `about:blank`。
- active target 变化递增 `stream_revision` 并通知所有 viewer；旧 revision 的帧和输入一律丢弃。

v1 UI 可以只显示 `active target + target_count`；多 tab 可视化管理放到 v1.5。

## 5. browserd

新服务建议放在 `services/browserd/`，使用 Go 实现。它不是透明 CDP proxy，而是以下职责的
唯一所有者：

- Chromium 生命周期和 profile 锁；
- 受鉴权的 CDP/MCP broker；
- controller lease；
- context/target registry；
- screencast、输入、帧限速和 viewer 广播；
- health、metrics、下载事件和安全日志。

### 5.1 Chromium 启动

生产选择完整 Chromium/Chrome for Testing 的 new headless，不同时使用
`chromium-headless-shell` 与 `--headless=new`。前者是旧 headless shell，行为和完整 Chrome
存在差异。

启动基线（P0-06 决议后）：

```text
--headless=new
--remote-debugging-pipe          # 不开 TCP CDP，见 §2.3 强制要求
--no-sandbox                     # P0-06 决议：风险接受，见 §12.3
--user-data-dir=/var/browser-profile   # pod 本地 emptyDir，见 §7.1
--disable-crash-reporter
```

约束：

- 固定 Chromium 和 Playwright MCP 版本及 browser revision；禁止运行时 `npx` 下载。
- `--disable-gpu` 和 `--disable-dev-shm-usage` 先通过 WebGL、视频、字体和压力测试再决定；
  不把它们当默认优化。
- **Chromium sandbox 决议为不启用（`--no-sandbox`）**。理由：容器内启用需放宽容器自身的
  seccomp（自定义 profile 下发到每个节点，或该容器设为 Unconfined），属于"用一种放松换另一种
  收紧"，且托管集群不保证允许改节点配置。代价是渲染进程未被隔离——在 sidecar 模式下，恶意
  网页利用 Chromium 漏洞后可直接取得 agent pod 的网络位置与 `/workspace` 读写权。该风险已
  显式接受，登记于 §12.3，必须进入发布风险说明。
- 上述决议不改变 debug pipe 的强制性：`--no-sandbox` 放弃的是渲染进程隔离，不是 CDP 管控。
- browserd 作为 PID 1 时负责 reaping；SIGTERM 先停止接收 lease，等待 profile flush，再终止
  Chromium 进程组。
- 启动使用单飞锁；定义 30 秒 readiness timeout、有限退避和 crash-loop 熔断。
- 每次进程启动递增 `browser_generation`。

### 5.2 活跃度和回收

分别维护：

- `viewer_activity_ts`：viewer ping、用户输入或显式导航。
- `controller_activity_ts`：有效 lease heartbeat、输入和受管 CDP 命令。
- `page_activity_ts`：导航、下载和对话框等有意义状态变化；页面动画/帧产生不算活动。

默认策略：

- 无 viewer、无 lease、无 CDP client 且无下载时，900 秒后关闭 Chromium。
- viewer 存在但 15 分钟无用户活动时关闭 viewer WS；被动动画不能永久阻止回收。
- WS 最大 lifetime 为 4 小时，前端可在符合重连条件时重新建立连接。
- 下载进行中可延迟回收，但有独立最大时长和大小限制。

operator 合并 runner/browser idle 时必须 fail-safe：

- 无法读取 browserd health 时，不立即缩容活跃 pod；记录 degraded 并在有限 grace 后按策略处理。
- sidecar readiness 失败不应让 runner API 永久不可用；browser capability 单独报告 degraded。
- sidecar 模式下 browser 无法独立缩容：浏览器活跃会保活整个 runner pod。因此只读 viewer
  **不计入**保活条件（只有 lease、用户输入、下载才算），避免用户开着面板不动就让 runner
  无期限不缩容。

### 5.3 Health

`GET /health` 只返回非敏感状态：

```json
{
  "status": "ok|starting|degraded",
  "chromium_running": true,
  "browser_generation": 7,
  "stream_revision": 12,
  "active_target": true,
  "target_count": 2,
  "viewers": 1,
  "cdp_clients": 1,
  "controller": "agent|user|none",
  "last_activity_ts": 1785123456,
  "crash_count": 0
}
```

不得返回当前 URL、title、cookie、页面截图或账户凭据。

## 6. Viewer WebSocket 协议

端点：`:8093 GET /api/browser/ws`。JWT 和 `Sec-WebSocket-Protocol` 复用现有 `wsAuth.js`，
但 browser protocol 自身必须版本化。

### 6.1 client → browserd

```jsonc
// 首帧，10 秒 deadline。display 尺寸仅用于本地呈现，不直接改变远端 viewport。
{"type":"init","protocol":1,"displayWidth":380,"displayHeight":720}

{"type":"request_control"}
{"type":"release_control","leaseId":"..."}
{"type":"lease_heartbeat","leaseId":"..."}

{"type":"navigate","leaseId":"...","streamRevision":12,"url":"https://example.com"}
{"type":"back","leaseId":"...","streamRevision":12}
{"type":"forward","leaseId":"...","streamRevision":12}
{"type":"reload","leaseId":"...","streamRevision":12}
{"type":"stop","leaseId":"...","streamRevision":12}

{"type":"mouse","leaseId":"...","streamRevision":12,"event":"down|up|move","x":10,"y":20,
 "button":"left","buttons":1,"clickCount":1,"modifiers":[]}
{"type":"wheel","leaseId":"...","streamRevision":12,"x":10,"y":20,"dx":0,"dy":120}
{"type":"key","leaseId":"...","streamRevision":12,
 "event":"down|up","key":"Enter","code":"Enter","modifiers":[]}
{"type":"text","leaseId":"...","streamRevision":12,"data":"中文输入"}

// v1 仅允许 controller 选择服务端预设；普通 viewer resize 只影响本地缩放。
{"type":"set_viewport","leaseId":"...","streamRevision":12,"preset":"desktop-1280x720"}
{"type":"ping"}

// 原生控件替代：SPA 侧渲染的 <select> 弹层选定后回写（见 §6.5）
{"type":"select_response","leaseId":"...","streamRevision":12,
 "popupId":"opaque","values":["CN"],"cancelled":false}

// JS 对话框应答（见 §6.6）。未应答前渲染器阻塞，必须有超时兜底。
{"type":"dialog_response","leaseId":"...","dialogId":"opaque",
 "accept":true,"promptText":"optional"}

// HTTP basic auth / 证书 interstitial 应答
{"type":"auth_response","leaseId":"...","challengeId":"opaque",
 "action":"provide|cancel","username":"...","password":"..."}
```

输入约束：

- 坐标永远使用 canonical viewport 的 CSS pixel；前端负责从 letterbox 后的 canvas 坐标转换。
- browserd 校验 `generation/stream_revision/active_target/lease` 后再调用 CDP Input domain；
  revision 过旧返回 `STALE_VIEW`，不把输入猜测性地发送到新页面。
- mouse 传递 `buttons` bitmask、`clickCount` 和 modifiers；move/wheel 分别节流并合并。
- 普通文本和中文 IME 通过 `beforeinput/composition` → `Input.insertText`；特殊键走
  `Input.dispatchKeyEvent`。
- URL 先标准化裸域名，再校验 scheme 和解析后的目标地址。

### 6.2 browserd → client JSON

```jsonc
{"type":"ready","protocol":1,"generation":7,"streamRevision":12,"targetId":"opaque",
 "url":"https://example.com","title":"Example","viewport":{"width":1280,"height":720,"dpr":1}}
{"type":"nav_state","generation":7,"url":"...","title":"...","loading":false,
 "canGoBack":true,"canGoForward":false,"securityState":"secure|insecure|warning|unknown"}
{"type":"controller","owner":"agent|user|none","leaseId":"...","expiresAt":1785123456}
{"type":"control_granted","leaseId":"...","expiresAt":1785123456}
{"type":"control_denied","reason":"BROWSER_BUSY|BROWSER_CONTROLLED_BY_USER"}
{"type":"target_changed","generation":7,"streamRevision":13,
 "targetId":"opaque","targetCount":2}
{"type":"download","id":"opaque","state":"started|completed|failed","name":"report.pdf"}

// 原生 <select> 被点击：headless 不会渲染弹层，改由 SPA 渲染（§6.5）
{"type":"select_popup","generation":7,"streamRevision":12,"popupId":"opaque",
 "multiple":false,"anchor":{"x":120,"y":340,"width":180,"height":32},
 "options":[{"value":"CN","label":"中国","selected":true,"disabled":false}]}

// JS 对话框打开：在收到 dialog_response 之前该页面渲染器是阻塞的（§6.6）
{"type":"dialog_opening","generation":7,"dialogId":"opaque",
 "kind":"alert|confirm|prompt|beforeunload","message":"...","defaultPrompt":""}
{"type":"dialog_closed","dialogId":"opaque","result":"accepted|dismissed|timeout"}

// HTTP basic auth 与证书错误 interstitial
{"type":"auth_required","challengeId":"opaque","kind":"basic|proxy",
 "origin":"https://example.com","realm":"..."}
{"type":"cert_error","challengeId":"opaque","origin":"https://example.com",
 "errorType":"expired|self-signed|name-mismatch|other"}

{"type":"closed","reason":"..."}
{"type":"error","code":"...","message":"localized-safe-message"}
{"type":"pong"}
```

`securityState` 来自 CDP Security domain、证书错误和 mixed content，不得只根据 URL 是否以
`https:` 开头判断。

### 6.3 二进制帧

不能只发送 `[tag][JPEG]`，因为 `Page.screencastFrame` 的 metadata 是准确坐标映射的一部分。
v1 帧格式：

```text
4B magic "PRVB"
1B protocol_version
1B tag = 0x01
2B header_length
4B browser_generation
4B stream_revision
8B frame_sequence
2B width
2B height
4B metadata_length
metadata_length B UTF-8 JSON
remaining B JPEG payload
```

所有整数使用 network byte order。`header_length` 允许后续协议增加字段并保持旧客户端可跳过。
metadata 至少保留 CDP 的 `offsetTop`、`pageScaleFactor`、`deviceWidth`、`deviceHeight`、
`scrollOffsetX/Y` 和 timestamp。客户端发现 generation、revision 或 sequence 过旧时直接丢弃。

### 6.4 viewport 与 backpressure

v1 默认 canonical viewport：

```text
width=1280, height=720, deviceScaleFactor=1, max_fps=12, jpeg_quality=60
```

- viewer 只做 scale-to-fit/letterbox；viewer 的 `ResizeObserver` 不调用远端
  `Emulation.setDeviceMetricsOverride`。
- controller 可从有限预设切换 viewport；切换递增 `stream_revision` 并清空旧帧队列。
- browserd 收到 CDP frame 后及时 Ack，但 Ack 不代表已有下游 backpressure。
- 每个 viewer 只有一个 pending frame slot，新帧覆盖旧帧。
- 默认限制：单帧不超过 2 MiB、每 viewer 不超过 8 MiB/s、write deadline 5 秒；连续超限关闭慢
  viewer。
- 输入单独使用令牌桶；mouse move/wheel 可合并，key/text 不静默丢弃。

### 6.5 原生控件替代（P0-07）

`Page.startScreencast` 只捕获页面合成器输出，**不包含浏览器自身的原生控件**；headless 模式下
这些控件根本不存在。受影响的有：`<select>` 下拉弹层、右键菜单、自动填充/密码管理器气泡、
打印对话框、系统文件选择器。

用户视角的表现是"点了下拉框什么都没发生"。其中 `<select>` 影响面最大（注册表单、国家/地区、
日期选择大量使用），且**只影响用户手动操作**——agent 走 Playwright 的 `selectOption()` 是
编程式赋值，不受影响。

v1 决议：**实现 `<select>` 弹层拦截**，其余写入已知限制。

流程：

1. browserd 在页面注入的 isolated-world 脚本捕获对 `<select>` 的 `mousedown`，
   `preventDefault()` 阻止原生弹层，读取 option 列表与锚点矩形。
2. browserd 发 `select_popup` 给 controller viewer（只读 viewer 不发）。
3. SPA 用现有 `Dropdown` 组件在对应坐标渲染弹层——视觉上反而比原生更统一。
4. 用户选定后回 `select_response`，browserd 写回 value 并派发 `input` + `change` 事件，
   保证站点的框架监听器（React/Vue）能感知。

约束：`stream_revision` 变化或 active target 切换时，未应答的 popup 立即作废；注入脚本必须在
isolated world，不污染页面 JS 环境；`<select multiple>` 用同一通道，`multiple:true`。

已知限制（写入用户可见文档）：右键菜单、打印对话框、自动填充气泡、系统文件选择器在 v1 不可用。

### 6.6 JS 对话框与认证挑战（P0-08）

`alert / confirm / prompt / beforeunload` 会触发 `Page.javascriptDialogOpening`，并且
**在调用 `Page.handleJavaScriptDialog` 之前该页面的渲染器是阻塞的**——不处理的话，站点第一个
`confirm()` 就会冻死整个会话，表现为"画面卡住、点什么都没反应"。因此这不是可选项。

- browserd 收到 dialog 事件 → 广播 `dialog_opening` → SPA 弹出符合设计规范的模态 →
  用户应答 → `dialog_response` → `Page.handleJavaScriptDialog`。
- **超时兜底**：无 controller、或 controller 30 秒未应答时，browserd 自动 dismiss
  （`beforeunload` 自动 accept），并广播 `dialog_closed{result:"timeout"}`。绝不能让页面
  无限期挂着。
- agent 持有 lease 时对话框由 agent 侧工具策略应答；用户仅收到通知，不抢焦点。
- HTTP basic auth（`Fetch.authRequired`）与证书错误 interstitial 同样处理：SPA 收集凭据后
  回传。凭据只在内存中转发给 CDP，**不落日志、不落 metrics、不写 profile 导出以外的任何地方**。

### 6.7 关闭码和重连

沿用 terminal 已有语义并补充：

| code | 含义 | 自动重连 |
|---|---|---|
| 4001 | auth failed | 否 |
| 4002 | feature disabled | 否 |
| 4003 | protocol/version invalid | 否 |
| 4010 | idle timeout | 用户操作后重连 |
| 4011 | max lifetime | 可立即新建连接 |
| 4012 | browser restarting | 是，有限重试 |
| 1012/异常断开 | 服务重启/网络异常 | 最多 3 次指数退避 + jitter |

不得对 auth、disabled 或协议错误无限重连。前端隐藏、切到 HTML 模式或 Canvas 关闭时主动释放
viewer，避免持续消耗帧流。

## 7. Profile、下载和数据生命周期

### 7.1 Profile：本地盘运行 + 关机导出（决议 2026-07-27）

**不把 Chromium profile 直接放在 `/workspace`（NFS）上。** 原因：profile 由 SQLite
（cookies、历史）与 LevelDB（localStorage、IndexedDB）构成，二者重度依赖文件锁；已确认
账户 workspace 卷是 NFS 支撑的 RWX（`services/operator/src/priva_operator/storage_backend.py:195`
创建 `ReadWriteMany` PVC），LevelDB over NFS 是已知的锁失败与数据损坏来源。而"登录一次、
之后 agent 带登录态干活"是本功能的头号卖点，不能押在这个组合上。

采用的方案：

```text
运行时： /var/browser-profile        pod 本地 emptyDir（磁盘介质，非 Memory），性能正常、锁正常
持久化： /workspace/.browser/profile-export.enc   关机时导出，启动时导入
```

- **导出内容**：cookies、localStorage、IndexedDB、origin 权限授予。不导出 cache、GPU
  shader cache、Code Cache 等可重建数据——这也顺便把导出文件控制在小体积。
- **导出时机**：browserd 收到 SIGTERM 时，先停止签发 lease → 关闭 viewer → 等 profile 数据库
  flush → 导出 → 再终止 Chromium 进程组。另外每 5 分钟做一次增量导出，缩小强杀丢失窗口。
- **已知损失**：pod 被强杀（OOMKill、节点驱逐、`SIGKILL`）时，最后一次增量导出之后新增的
  登录态会丢失。UI 在这种情况下应提示"上次会话未正常保存，可能需要重新登录"，不静默失败。
- **不做双写**：不要一边写本地一边同步 NFS——那会把 NFS 的锁问题重新引入。

profile 是新的高价值凭据面，而不是普通缓存：

- CDP 可读取 HttpOnly cookie、storage 和已认证页面内容；日志和错误不得包含这些数据。
- 导出文件必须加密静置（账户绑定密钥），且不随普通 workspace 备份/导出流程外流。
- 初次启用 LIVE 时告知用户登录态可被其 agent 使用，并提供明确确认。
- 提供 **Reset Browser Data** 危险操作：要求输入账户名确认，关闭 viewer/lease/Chromium 后
  同时清理本地 profile 与导出文件；记录不含内容的审计事件。
- Chromium 启动前处理 `SingletonLock`：本地 emptyDir 每次都是全新的，stale lock 主要来自
  导入流程，导入时必须剔除 lock 类文件。
- 启动后执行完整性检查；导出文件损坏时隔离并以空 profile 启动，不阻塞浏览器可用性。
- 导出文件大小计入账户 storage quota；本地 emptyDir 需设 `sizeLimit` 并计入 pod 临时存储预算。
- profile 不应自动暴露给其他账户、管理员预览、日志采集或备份导出。
- **P0 spike 必测**：导出/导入往返后登录态确实存活（含 pod 重启）、导出耗时在 SIGTERM
  grace period 内可完成、Chromium 版本升级后旧导出仍可导入。

### 7.2 下载

v1 即使不提供下载列表，也必须定义行为：

- 下载目录固定为 `/workspace/Downloads`，agent 可见。
- 文件名规范化，禁止目录穿越；重名使用可预测后缀，不覆盖已有文件。
- 单文件、总并发、总字节和最长持续时间有限制；写入前检查 quota。
- browserd 广播 started/completed/failed；Canvas 至少显示完成或失败状态。
- 下载进行中禁止静默销毁 Chromium；超时后明确标记失败。

上传、剪贴板和文件选择器默认需要用户确认；无 UI 确认能力时在 v1 中返回明确的 unsupported，
不得挂起工具调用。

## 8. 边缘、capability 与 operator

### 8.1 路由

| 位置 | 改动 |
|---|---|
| `deploy/gateway/inferencepool.yaml` | 新 pool `browsers`，selector `app=agent-runner`（sidecar 模式），targetPort 8093，同一个 control-panel EPP |
| `deploy/gateway/httproute.yaml` | `/api/browser/capability` exact → control-panel；`/api/browser` prefix → browsers pool |
| `services/control-panel/.../extproc.py` | JWT、账户 capability、部署 generation、wake、steer 和授权头 |
| `routers/browser.py` | 无唤醒 capability；返回 enabled/available/phase/reason/restart 状态 |
| NetworkPolicy + Helm | :8093 只允许 agentgateway/operator 入站（沿用 `terminal-deny-tenant-peers` 的写法）；sidecar 模式下 runner→browserd 走 pod 内 loopback，不经 NetworkPolicy |

`wake_and_wait()` 当前以 runner `:8091 /health` 为成功条件，不能直接把返回地址改成 `:8093`。
browser 路径必须等待目标 browserd generation 的 readiness，并处理：

- runner 已热但 browserd sidecar 尚未 ready；
- 配置刚开启、旧 runner pod 还没有 browserd；
- 独立 browser pod 正在创建或 crash-loop；
- 管理员关闭期间已有 viewer/lease 仍在 drain。

### 8.2 Capability

建议响应：

```json
{
  "enabled": true,
  "available": false,
  "phase": "PendingRunnerRestart|Waking|Starting|Ready|Degraded|Disabled",
  "reason": "safe-machine-code",
  "deploymentGeneration": "opaque",
  "requiresRestart": true,
  "profileExists": true
}
```

- Browser Canvas tab 始终保留，因为 HTML 预览不依赖 LIVE capability。
- `enabled=false` 只禁用 LIVE mode。
- `available=true` 必须代表当前部署 generation 中存在健康 browserd，而不是仅配置百分比大于 0。
- capability 不返回 pod IP、内部端口、URL 或 profile 内容。

### 8.3 开关作用域

**决议（2026-07-27）：v1 即做按账户开关。**

现状澄清：web 终端的开关（`terminal_resource_percent`，`libs/common/src/priva_common/config.py:241`）
确实是平台级全局的，但**账户级覆盖的链路已经存在**——`UserRecord.cpu_cores / memory_mb`
（`libs/common/src/priva_common/models/auth.py:79-80`）就是"null 继承平台默认 / 有值覆盖"的
完整实现，且已贯通 data-spine、proto、admin API 与 CRD `spec.resources`。所以本项是沿用既有
模式加一个字段，而非新建一条链路，估时约 2~3 天。

```text
platform browser default（默认 disabled）
        ↓ UserRecord.browser_enabled == null 时继承
per-account override（browser_enabled = true/false）
        ↓
AgentTenant spec + deployment generation → operator 决定是否注入 browserd 容器
```

选择按账户而非全局的理由：浏览器一旦启用即占 768Mi~1Gi 内存，全局开启意味着集群内存预算要按
全员计算；按账户可先给试点账户开。与 §9 的独立镜像决议配合后，**未启用的账户完全无成本**
（不注入容器 → 不拉镜像 → 不占内存）。

需要同步修改：

- common config/admin models（加 `browser_enabled: bool | None`）；
- data-spine schema 迁移、repository 和 service；
- protobuf/generated client；
- CRD `spec` 每账户字段（沿用 `spec.resources` 的位置约定）；
- control-panel provisioner、capability 和管理 API；
- operator：按该字段决定是否注入 browserd 容器，并计入 allocation hash；
- Helm values/config；
- admin UserEditDrawer/Fleet UI、locales 和测试。

启用/修改资源后进入 `PendingRunnerRestart` 或 `PendingBrowserRestart`。存在 active run、viewer、
lease 或下载时先 drain；禁止直接杀死正在操作的浏览器。

### 8.4 资源分账

现有 `_split_resources` 只处理 runner + terminal，需扩展为显式三方分账：

```text
runner + terminal + browser = committed account allocation
```

约束：

- 百分比之和不得超过 100%，并保证 runner 最低 CPU/内存。
- browser 默认 0%；建议最小 250m CPU/512Mi，常规单 active page 使用 768Mi～1Gi limit，
  最终以压测为准。
- requests 与 limits 策略、`/tmp`/`dev-shm` 是内存还是 ephemeral storage 必须计入预算。
- sidecar、terminal 同时开启的组合要进入 allocation hash 和滚动更新测试。
- Chromium OOM 只应使 browser capability degraded；不能造成无限 pod 重启。

## 9. 镜像

**决议（2026-07-27）：browserd 使用独立镜像**（`deploy/docker/browserd.Dockerfile`），
即使它以 sidecar 形式与 runner 同 pod——同一个 pod 内的容器可以使用不同镜像。

理由：与 §8.3 的按账户开关配合，未启用浏览器的账户不会被注入 browserd 容器，因而**完全不承担
Chromium 的镜像体积、拉取时间与节点磁盘占用**。若并进 `agent-runner` 镜像，按账户开关就只省了
内存、没省镜像成本，等于让全体账户为少数启用者付费。

代价（已接受）：多一套构建/推送/版本对齐流程；minikube 本地开发需多 load 一个镜像。

镜像要求：

- 不包含 runner 平台 secrets、Claude runtime 或无关工具链。
- 固定 browserd、Chromium、Playwright MCP 兼容矩阵，禁止运行时下载。
- 包含 CJK 字体、CA bundle、可选企业 CA、locale/timezone 和代理设置。
- non-root（uid 10001，与 runner 一致）、read-only root filesystem、
  `automountServiceAccountToken: false`、drop all capabilities。
- browserd 容器使用显式 env allowlist，**禁止 `envFrom`** —— 这是 sidecar 模式下唯一还能把
  平台 secrets 挡在 Chromium 环境之外的手段（同 pod 共享卷与 netns 已无法隔离）。
- 需要的可写挂载：`/var/browser-profile`（emptyDir，磁盘介质，profile 运行目录，见 §7.1）、
  `/tmp`（emptyDir，含 dev-shm 预算）、`/workspace`（与 runner 共享，用于导出文件与下载）。

## 10. Agent/MCP 接入

使用平台保留 server 名 `priva_browser`，工具名稳定为：

```text
mcp__priva_browser__browser_*
```

这样前端 auto-show 可以精确匹配托管工具，避免普通用户 MCP 误触发。

要求：

- Playwright MCP 固定精确版本并在构建时安装；禁止运行时执行未固定版本的
  `npx @playwright/mcp`。
- MCP 配置由平台注入，用户普通 MCP 配置不能覆盖保留名称或修改 CDP endpoint/header。
- wrapper 在每个工具批次获取 agent lease，并把 `session_id/run_id/generation` 绑定到请求。
- 禁止或包裹创建新 context、任意 CDP、任意 JavaScript evaluate、cookie/storage 导出、文件
  上传等高风险工具。
- CDP over HTTP discovery 和 WebSocket upgrade 都必须携带相同授权，且 endpoint 不能在
  discovery response 中绕过 browserd。

默认权限范围：

- JWT WebUI 当前会话：允许。
- IM/Channel connector、scheduled run、后台 automation、subagent：默认禁用，后续按独立产品
  权限逐项开放。
- 用户处于 controller 状态时 agent 工具 fail-fast，而不是在后台等待后突然执行。

浏览器内容属于不可信输入。页面文字中的指令不得自动提升权限。提交表单、上传文件、发送消息、
付款、删除数据、修改权限等不可逆/外部副作用操作，需要沿 agent 工具策略触发用户确认。

## 11. 前端

现有 Canvas 已有全局 `CanvasHeader` 和关闭按钮，因此 LIVE/HTML 切换放在 Browser 内容工具栏，
不重复创建第二层 Canvas header。现有 `browserDebugStore.mode` 已表示 Inspect/Interact，为避免
命名冲突，顶层模式建议叫 `LIVE | HTML`，HTML 内继续保留 `INSPECT | INTERACT`。

按照项目规则，修改组件前必须再次向用户展示并确认最终 ASCII 布局。当前建议稿：

```text
┌─ Canvas global header: BROWSER ──────────────────────────┐
├───────────────────────────────────────────────────────────┤
│ [ LIVE | HTML ]                           [EXPAND]         │
├───────────────────────────────────────────────────────────┤
│ [Back] [Forward] [Reload] │ https://example.com     [Go]  │
├───────────────────────────────────────────────────────────┤
│                                                           │
│              canonical viewport → scale-to-fit            │
│              click-to-focus / letterboxed canvas          │
│                                                           │
├───────────────────────────────────────────────────────────┤
│ AGENT DRIVING · 1280×720 · 12 FPS      [TAKE CONTROL]     │
└───────────────────────────────────────────────────────────┘
  状态通过 2px 左边框表达，不使用圆点或彩色状态背景。
```

前端要求：

- LIVE capability 不影响 HTML tab；不可用时给出 disabled/restart/degraded 原因。
- 状态完整覆盖 `unsupported/waking/starting/connected-readonly/user-control/agent-control/
  reconnecting/restarting/degraded/disabled`。
- 初次加载使用与真实布局同形的 skeleton shimmer；不得用 spinner。
- 所有 Lucide icon 使用 `strokeWidth={1.5}`；颜色使用 CSS variables；无横向滚动。
- 280～380px Canvas 下默认 scale-to-fit；提供 expand/fullscreen，否则 1280px 页面不可操作。
- panel 和 expanded modal 复用一个 WS/viewer，不占用两个 viewer 名额。
- canvas 可聚焦，点击获得本地键盘焦点；Escape 释放本地键盘捕获。明确处理
  Ctrl/Cmd+L、R、W、Tab 等浏览器快捷键，避免用户无法退出。
- **`<select>` 弹层**（§6.5）：收到 `select_popup` 后用现有 `Dropdown` 组件在锚点坐标渲染，
  坐标需经与帧同一套 letterbox 变换换算；`stream_revision` 变化时立即关闭未应答弹层。
- **JS 对话框**（§6.6）：`dialog_opening` → 居中 scale-in 模态（200ms spring），
  `prompt` 带输入框，`beforeunload` 用"离开/留在此页"措辞；显示 30 秒自动关闭倒计时，
  与 browserd 的超时兜底一致。basic auth / 证书错误用同一模态样式，凭据输入框不做自动填充、
  不进 zustand 持久化。
- 使用 hidden textarea + `beforeinput/compositionstart/update/end` 支持中文 IME。
- pointer capture、buttons bitmask、double click、context menu 和 wheel normalization 必须测试。
- controller 为 agent 时，用户输入不发送；点击 Take Control 后才开始转发。
- URL/title 截断并避免写入日志；地址栏只允许批准的 scheme。

Auto-show：

- 仅当前前台 runtime 的 live `mcp__priva_browser__browser_*` `tool_use` 事件自动打开 LIVE。
- 历史 replay、后台 session 和普通 MCP 不抢焦点；显示 badge/notice。
- Canvas 隐藏、切到 HTML 或页面不可见一段时间后断开 viewer；再次打开按重连策略连接。

## 12. 安全模型

### 12.1 需要防御

- 恶意网页利用 Chromium 漏洞；
- 网页/agent 对集群内部、其他租户、云 metadata 的 SSRF；
- 原始 CDP 绕过 browserd；
- 登录 profile、cookie、storage 和下载泄露；
- 页面 prompt injection 引导 agent 执行高风险操作；
- 恶意或失控 client 制造高帧率、超大 viewport、输入洪泛和连接耗尽。

### 12.2 控制项

1. **网络**：sidecar 模式下无法给浏览器单独配 egress，只能按整 pod 收紧——因此 SSRF 防护的
   主力落在 **browserd 的连接前/后地址校验**上，而不是 NetworkPolicy。需覆盖 RFC1918、
   loopback（dev server 端口除外，见下）、link-local、云 metadata、CGNAT、集群 CIDR、
   IPv6 ULA/link-local 和 DNS rebinding；不能只检查 URL 字符串，DNS 解析后、重定向后都要
   重新校验目标地址。
   **例外**：`localhost:<dev-server 端口>` 是被显式允许的高频用法（预览 agent 起的服务），
   需按端口白名单放行，且只放行 runner 声明过的端口，不是整个 loopback。
2. **scheme**：默认只允许 `http`、`https`、`about:blank`；阻止 `file`、`data`、
   `javascript`、`chrome`、`devtools`。localhost/dev server 需要单独显式策略。
3. **Chromium sandbox**：**已决议不启用**（`--no-sandbox`，§5.1）。剩余的进程级约束只有
   non-root（uid 10001）、drop all capabilities、read-only rootfs、seccomp RuntimeDefault。
   渲染进程未被隔离这一点计入 §12.3 残余风险。
4. **CDP**：Chromium debug pipe；browserd broker 鉴权；不暴露 discovery/raw WebSocket；
   lease 绑定 run/session/generation。
5. **凭据**：browserd/Chromium env allowlist、无 `envFrom`、无 ServiceAccount token、日志与 metrics
   无 URL/cookie/header/frame。
6. **profile**：用户确认、配额、reset、锁恢复、升级测试；将其视作敏感凭据数据。
7. **内容安全**：浏览器页面是 LLM 不可信输入；高风险工具走用户确认和工具 allowlist。
8. **资源**：固定 viewport/FPS、viewer/CDP client 上限、帧/带宽/下载/输入限额和 lifetime。

### 12.3 已接受的残余风险（ADR-01 + P0-06 组合）

ADR-01 选定 sidecar、P0-06 选定 `--no-sandbox`，两项叠加的结果是：**恶意网页与 agent 之间
不存在进程级或网络级隔离边界**。下列风险无法通过普通 NetworkPolicy 或容器配置消除，必须逐条
进入发布风险说明，并由安全负责人签字确认（§2.4 验收项）：

1. **渲染进程未沙箱化**：网页利用 Chromium 漏洞逃逸后，直接获得 browserd 容器的执行上下文。
2. **同 netns**：逃逸后即取得 runner 的网络位置，继承 runner 全部合法 egress 与内部可达性；
   无法只对浏览器收紧 egress。
3. **可访问 pod localhost**：包括 runner 自身监听的端口。
4. **共享卷**：runner 与 browserd 共享 `/workspace`，租户进程可读写 profile 导出文件
   （该文件加密静置可降低但不消除风险）。
5. **故障域合一**：Chromium OOM、pod 驱逐或节点故障会同时影响 agent 与浏览器。

缓解措施（不消除风险，只降低暴露）：browserd 显式 env allowlist 挡住平台 secrets；
debug pipe 阻止 CDP 被直接驱动；browserd 侧地址校验承担 SSRF 防护；按账户开关把暴露面限制在
显式启用的账户。

**若未来出现"浏览器必须独立网络隔离"的客户要求，需重启 §2.2 的独立 pod 方案**——browserd
代码本身按独立 pod 的接口约定编写（debug pipe + 鉴权 broker），切换成本主要在部署层。

## 13. 可观测性与 SLO

### 13.1 Metrics

- Chromium startup duration、startup failure、crash/OOM/restart count；
- first-frame latency、input-to-next-frame latency；
- frames generated/sent/dropped、JPEG bytes、viewer write timeout；
- active viewers、controllers、CDP clients、targets、downloads；
- lease granted/denied/revoked/expired；
- capability phase 和 wake duration；
- browser container CPU/memory/ephemeral storage/profile size。

metrics/logs 只使用账户的不可逆 hash 或平台内部 opaque ID，不记录 URL、title、DOM、截图、输入、
cookie、Authorization header 和下载内容。

### 13.2 初始目标（压测后调整）

| 指标 | 目标 |
|---|---|
| warm connect → ready | p95 ≤ 2s |
| cold wake → first frame | p95 ≤ 20s |
| 用户输入 → 下一相关帧 | 同地域 p95 ≤ 250ms |
| browserd 非预期断开率 | < 1% sessions |
| 默认帧率/viewport | ≤ 12fps，1280×720，DPR=1 |
| 资源边界 | 单账户 browser OOM 不影响其他账户；sidecar 模式下 browserd 容器 OOM 只重启该容器，不应连带重启 runner 容器（需验证 restartPolicy 行为） |

## 14. 验收测试

### 14.1 功能

- 用户先连接、agent 先连接、agent/用户往返交接；
- 用户抢占正在操作的 agent，后续命令被明确拒绝；
- 两个 viewer、两个 agent session、后台 session 和历史 replay；
- 导航、后退/前进、reload、页面关闭、popup/OAuth、target crash；
- 中文 IME、组合键、双击、右键、拖动、滚轮、高 DPI 本地屏幕；
- Canvas 280px、380px、全屏和 split pane，无水平滚动；
- `<select>` 单选/多选弹层、选定后站点框架能收到 `change`、切页时弹层作废；
- `alert/confirm/prompt/beforeunload` 全部可应答，30 秒超时兜底生效，页面不会挂死；
- HTTP basic auth 与证书错误可应答；
- user lease 90 秒空闲释放、静默重获、agent 持有时用户输入被丢弃且需显式抢占；
- profile 导出/导入往返后登录态存活（含 pod 正常重启与 SIGKILL 两种路径）、浏览器版本升级后
  旧导出可导入、导出文件损坏时以空 profile 启动而非阻塞；
- 下载成功、重名、quota、超时和回收期间下载。

### 14.2 安全

- runner 不能直连或发现裸 Chromium CDP；
- 未授权 pod/账户不能连接 browserd；
- 浏览器不能访问云 metadata、Kubernetes API、其他租户、未授权 service、IPv6 内网；
- DNS rebinding 和重定向到私网被阻止；
- `file:`/`data:`/`javascript:` 等 scheme 被拒绝；
- browserd/Chromium env 无平台 secrets，ServiceAccount token 未挂载；
- 页面/URL/cookie/frame 不进入日志、metrics 或异常消息；
- 用户控制期间 agent 无法通过 MCP/raw CDP 绕过 lease。

### 14.3 生命周期和故障

- 冷启动、warm start、readiness timeout、Chromium crash-loop、browserd restart；
- viewer 慢消费、断网、重连、超过 max viewers/lifetime；
- browser OOM、磁盘满、profile 损坏、下载中缩容；
- 管理员启用/禁用/改资源时 active run、viewer 和 lease 正确 drain；
- operator 无法读取 health 时不会误杀活跃浏览器，也不会永久保活故障资源。

## 15. 分期与工作量（单人）

ADR 已关闭，可直接进入 P0。下表已按 2026-07-27 的决议调整（sidecar 省下 wake 机制，
新增 `<select>`/dialog 与 profile 导出两块工作）：

| 阶段 | 内容 | 估时 |
|---|---|---|
| P0 | spike：debug pipe 可行性、screencast 坐标映射、`--headless=new` 下 `<select>` 实测、**profile 导出/导入往返**、威胁模型 | 2～3 天 |
| P1 | browserd 生命周期、鉴权 broker、target registry、lease 状态机、帧/输入协议 | 5～7 天 |
| P2 | 独立 browserd 镜像、operator sidecar 注入、per-account 开关全链路、资源分账、edge、NetworkPolicy | 5～7 天 |
| P3 | LIVE 前端、输入/IME、控制权、`<select>` 弹层、dialog 模态、responsive、auto-show | 5～6 天 |
| P4 | 托管 MCP 与工具策略、profile 导出/导入、download 生命周期 | 3～5 天 |
| P5 | 故障、安全、并发、性能和升级测试 | 4～6 天 |

生产 v1 约 **5～7 人周**。相对评审版的变化：sidecar 省掉了独立 pod 的 wake 机制（约 -3 天），
但 per-account 开关（+2~3 天）、`<select>` 拦截（+1~2 天）、dialog 协议（+1 天）、
profile 导出/导入（+2 天）是净增。

P0 的三个 spike 结果会回写本文档；其中 **profile 导出往返若在 SIGTERM grace 内无法完成**，
需重启 §7.1 的存储决策（回到独立块存储卷方案）。

v1.5 候选：多 tab UI、元素审查、下载列表、viewport presets、剪贴板/上传审批、审计查看器、
右键菜单与打印支持。

v1.5 候选：多 tab UI、元素审查、下载列表、viewport presets、剪贴板/上传审批、审计查看器。

## 16. 决策记录

全部决议于 2026-07-27 关闭。

| 决策 | 状态 | 结论 |
|---|---|---|
| ADR-01 部署模型 | **已定** | sidecar，不做独立 pod。残余风险接受并登记 §12.3 |
| ADR-02 控制权 | **已定** | 单 controller lease；用户显式抢占；agent 冲突 fail-fast；user lease 90 秒空闲释放 + 静默重获（仅 controller 为 NONE 时） |
| ADR-03 capability | **已定** | v1 即做 per-account 开关，复用 `UserRecord` null=继承 模式；平台默认关闭 |
| P0-06 Chromium sandbox | **已定** | 不启用，`--no-sandbox` + 风险接受（§5.1 §12.3） |
| P0-07 原生控件 | **已定** | v1 实现 `<select>` 弹层拦截；右键/打印/自动填充写入已知限制 |
| P0-08 JS 对话框 | **已定** | 纳入协议，含 30 秒超时兜底；不做会导致页面挂死 |
| profile 存储 | **已定** | 本地 emptyDir 运行 + 关机/5 分钟增量导出到 `/workspace`，不直接落 NFS |
| 镜像 | **已定** | 独立 `browserd` 镜像，即使以 sidecar 形式部署 |
| Chromium 发行形态 | 建议默认 | 完整 Chromium/Chrome for Testing new headless，不混用 headless-shell |
| viewport | 建议默认 | 1280×720、DPR=1、12fps，viewer 本地 scale-to-fit |
| viewer | 建议默认 | 最多 2 个，一个 user controller |
| browser idle | 建议默认 | 无 viewer/lease/CDP/download 后 900 秒 |
| MCP 权限 | 建议默认 | 仅当前 JWT WebUI 会话；IM/scheduled/subagent 默认关闭 |

### 16.1 待 P0 spike 回写的项

- `--headless=new` 下 `<select>` 弹层的确切行为（决定 §6.5 拦截范围）。
- profile 导出能否在 SIGTERM grace 内完成（失败则回退到独立块存储卷，§7.1）。
- screencast 帧率与坐标映射在 1280×720 下的实测表现（决定 §6.4 默认值）。

## 17. 参考资料

- Chrome DevTools Protocol — Page：
  <https://chromedevtools.github.io/devtools-protocol/tot/Page/>
- Chrome DevTools Protocol — Target：
  <https://chromedevtools.github.io/devtools-protocol/tot/Target/>
- Playwright `connectOverCDP`：
  <https://playwright.dev/docs/api/class-browsertype>
- Playwright Docker / Chromium sandbox：
  <https://playwright.dev/docs/docker>
- Playwright MCP：
  <https://github.com/microsoft/playwright-mcp/blob/main/README.md>
- Chrome Headless：
  <https://developer.chrome.com/docs/chromium/headless>
- Chrome Remote Debugging security：
  <https://developer.chrome.com/blog/remote-debugging-port>
- 项目现有同 pod 风险分析：
  `docs/webterminal-blind-spot.md`
