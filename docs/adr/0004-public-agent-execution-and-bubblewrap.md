# ADR-0004：Public Agent 的执行归属与 bubblewrap 沙箱

> **状态：Accepted（设计已确认，尚未实现）**
>
> **日期：2026-08-07**
>
> **适用范围：**“A 发布 Agent Definition，B 在自己的租户环境中运行”的 shared/public Agent。
>
> **关联设计：**[Agent Runner 组件](../architecture/components/agent-runner.md)、[多租户平台蓝图](../architecture/multi-tenant-platform.md)、[产品规格 §11.3](../agent-runtime-product-spec-zh.html#publishable)。

## 1. 决策摘要

1. **v1 采用“发布配置、调用者实例化”模型。** A 发布不可变、版本化的 Agent
   Definition；B 调用时，由 control-panel 解析该版本并在 **B 自己的 Agent Runner Pod**
   中执行。共享的是定义，不是 A 的 Pod、工作区、凭据、会话或权限。
2. **不为每个 Agent 或每次运行创建新 Pod。** 现有“一账号一个 Pod”继续承担租户边界；
   gVisor、Kata 和独立 AgentRun Pod 不进入本方案的 v1 基线。
3. **bubblewrap 不是租户隔离层，也不是所有 Agent 的默认必选项。** 它只承担
   “B 的 Pod 内，本次不受信 public Agent 只能访问 B 明确授权目录”的第二层边界。
4. **采用两种运行画像：**
   - `trusted`：B 自己创建、管理员审核或 B 明确信任的定义；保持现有执行模型，
     bubblewrap 可选。
   - `public-restricted`：来自第三方且允许 Bash/文件工具的定义；强制使用受平台控制的
     bubblewrap launcher，任何沙箱初始化失败都拒绝运行。
5. **`public-restricted` 使用 outer bubblewrap 包裹整个 Claude CLI。** Claude Code/SDK
   自带的 Linux sandbox 只保证 Bash 及其子进程，不能单独满足“整个 CLI 只能看到指定
   业务目录”的字面要求；它可作为后续内层防御，但不是本决策的唯一边界。
6. **网络保持调用者 Pod 的现有网络 namespace。** outer bubblewrap 使用
   `--share-net`，不增加域名白名单或 HTTP 代理。其明确后果是：Agent 可以把它被授权读取
   的内容发往互联网；本方案不是 DLP。
7. **bubblewrap 单独不构成完整方案。** restricted 画像还必须同时具备：服务端目录授权、
   Claude 工具路径校验、子进程环境清洗、私有运行状态、不可扩权的 settings、专用 seccomp
   以及 public definition 的能力约束。

## 2. 当前事实与边界

### 2.1 已有的租户边界

平台已经是一账号一个、可 scale-to-zero 的 Agent Runner Pod：

- Pod 只挂载该账号自己的 `/workspace`；
- `runAsNonRoot`、`drop ALL`、`allowPrivilegeEscalation:false`、只读根文件系统；
- 不挂载 ServiceAccount token；
- 默认拒绝访问其他租户 Pod；
- control-panel 在平台边缘验证用户 API Key/JWT，然后换成短期
  `X-Priva-Runner-Token`，runner 只持有验签公钥。

因此 A、B 两个租户之间的隔离不依赖 bubblewrap。若 B 实例化 A 的公开定义，执行位置仍是
B 的 Pod，计费、BYOK、workspace、会话和输出均属于 B。

### 2.2 新增的租户内风险

Agent Definition 是供应链输入。即使它只有 prompt、skills 和工具声明，恶意定义或 prompt
injection 仍可能诱导 Claude：

- 扫描 B Pod 内与本次任务无关的项目；
- 读取 `.claude`、历史会话、用户配置或凭据文件；
- 通过 Bash、Hook、stdio MCP 或文件工具绕过应用层路径约定；
- 读取 runner 继承给 CLI 的环境变量或 `/proc/<pid>/environ`；
- 利用开放网络外传已读取的数据。

当前实现对 private/single-tenant 使用是合理的，但不满足 public-restricted：

- `build_agent_options()` 默认 `permission_mode="bypassPermissions"`；
- `setting_sources=["project", "user"]`，可加载 B 工作区中可修改的配置；
- `cwd`/`add_dirs` 只做存在性检查，可指向该 Pod 的任意目录；
- 文件浏览器按设计允许浏览该租户 Pod 的整个文件系统；
- runner 容器环境含账号范围的 data-spine service token，SDK 子进程默认继承父环境；
- 当前镜像没有 `bubblewrap`/`socat`，Pod 的 `RuntimeDefault` seccomp 会拒绝创建
  bubblewrap 所需的 user namespace。

### 2.3 本决策不解决什么

- 不把普通 runc Pod 提升为 VM 级隔离；
- 不阻止 B 自己信任的 Agent 访问 B 主动授予的内容；
- 不限制公网域名、HTTP 内容或数据外传；
- 不让一个原始平台 API Key 在沙箱里“变得安全”——原始 Key 根本不得进入 CLI；
- 不允许模型 (b)“外部调用者直接驱动 A 的常驻个人 Pod”。模型 (b) 继续要求独立服务
  身份/租户或易逝 AgentRun Pod，不复用本 ADR 的模型 (a) 风险结论。

## 3. Public Agent Definition 契约

### 3.1 所有权

一次运行必须保留四个不同字段，不能把发布者当成执行者：

```json
{
  "definition_owner_account_id": "account-a",
  "definition_id": "agent-123",
  "definition_version": "5",
  "caller_account_id": "account-b",
  "execution_account_id": "account-b",
  "session_owner_account_id": "account-b"
}
```

- A 只能管理 Definition 及其发布版本；
- B 拥有运行、会话、输出和费用；
- B 的原始 API Key 只到 control-panel；A 的 Key 从不参与；
- control-panel 只能向 B 的 Pod 签发 B 范围的短期 runner token；
- runner token 只验证入站 HTTP/WS，不得写入 prompt、workspace、settings 或 CLI 环境。

### 3.2 发布物允许的内容

public v1 默认允许声明：

- system prompt；
- 平台注册、版本固定的只读 skills；
- 平台注册的工具标识；
- 所需能力清单，例如 `workspace.read`、`workspace.write`、`bash`、`network`；
- 建议模型和最大 turn 数，但最终由 B/平台策略裁剪。

public v1 默认禁止 Definition 直接携带：

- 原始环境变量和值；
- 绝对宿主/Pod 路径；
- `cli_path`、bubblewrap 参数或任意 settings JSON；
- 自定义 shell Hook；
- 任意 stdio MCP `command`/`args`；
- `excludedCommands`、`allowUnsandboxedCommands` 或任何沙箱逃逸配置；
- A 的凭据、MCP token、会话文件、软链接或私有资源路径。

需要上述能力的发布物只能经过管理员审核成为 `trusted`，或者把能力改造成平台托管、按调用者
授权的远程 MCP/API。

### 3.3 B 授予资源，不授予路径

客户端提交逻辑资源，不能提交任意绝对路径：

```yaml
sandbox_profile: public-restricted-v1
grants:
  - resource_id: project-b
    mount_path: /work
    access: rw
  - resource_id: shared-reference
    mount_path: /reference
    access: ro
```

服务端根据 `caller_account_id` 的 ACL 解析真实路径，执行 `realpath`，并拒绝：

- 不属于 B 管理根目录的路径；
- 软链接逃逸；
- RO/RW 重叠或可写父目录覆盖只读子目录；
- 重复或冲突的 sandbox mount path；
- 不位于某个 RW grant 内的 `cwd`。

解析后的 grants、Definition digest、运行画像和调用者身份必须随 session 保存；resume 时重新
授权，已撤销的 grant 必须 fail closed。

## 4. bubblewrap 运行设计

### 4.1 为什么选择 outer wrapper

Claude Code 官方 Linux sandbox 本身使用 bubblewrap，但其安全边界是 **Bash 及其子进程**；
Read/Edit/Grep/Glob、SDK in-process MCP、Hooks 等仍需 Claude permissions 或应用代码治理。
本需求使用“Claude CLI 只能看到授权业务目录”的强表述，因此 restricted 画像把
`options.cli_path` 固定为平台 launcher，由 launcher 再 exec 真正的 Claude CLI。

用户、Definition 和 B 的可写 project settings 都不能覆盖 `cli_path`。

### 4.2 文件系统视图

“只看到指定目录”指 **只看到指定业务数据目录**，不表示一个 Linux 动态链接程序可以完全
看不到系统运行文件。restricted CLI 最小视图包含：

- `/usr`、必要的 `/bin`/`lib*`：只读；
- CA、DNS、最小 passwd/nss 配置：只读；
- 私有 `/proc`、`/dev` 和 tmpfs `/tmp`；
- 本次 Definition 的只读物化目录；
- B 授权的 RO/RW grants；
- 该 public-agent 实例专属的 `HOME`、`CLAUDE_CONFIG_DIR` 和 session state；
- 不包含 B 的其他项目、普通 `.claude` home、其他 session、runner 配置或 Secret mount。

概念启动形态：

```bash
bwrap \
  --unshare-user \
  --unshare-pid \
  --unshare-ipc \
  --unshare-uts \
  --share-net \
  --new-session \
  --die-with-parent \
  --ro-bind /usr /usr \
  --ro-bind /etc/ssl/certs /etc/ssl/certs \
  --proc /proc \
  --dev /dev \
  --tmpfs /tmp \
  --ro-bind <definition-materialization> /agent \
  --ro-bind <resolved-ro> /reference \
  --bind <resolved-rw> /work \
  --bind <private-agent-state> /state \
  --chdir /work \
  /usr/local/bin/claude "$@"
```

实际实现不得用 shell 拼接路径。launcher 读取 runner 生成的结构化 policy，逐项校验后用 argv
调用 `bwrap`。

### 4.3 Claude/SDK 设置

`public-restricted` 至少强制：

- `permission_mode != "bypassPermissions"`；
- `setting_sources=[]`，不加载 B 的 project/user/local 可写设置；系统 managed settings 继续生效；
- `cli_path=<trusted launcher>`，且运行请求不能覆盖；
- `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1`；当前镜像内 Claude Code `2.1.207` 已包含该能力；
- `max_turns`、运行超时和每租户并发上限；
- 沙箱不可用、环境清洗不可用或 policy 校验失败时直接拒绝运行，绝不回落到非沙箱执行。

SDK `0.2.116` 已暴露 `ClaudeAgentOptions.sandbox`，可用于额外的 Bash 内层沙箱；但 v1 不以
它替代 outer wrapper。若启用嵌套 sandbox，必须先验证正常模式，而不是直接打开
`enableWeakerNestedSandbox`。后者会降低 Linux 隔离强度，不作为生产默认值。

**当前 session 路径实现是 restricted 模式的显式 blocker。** Agent Runner 的 fork、rewind、
heal/strip 辅助路径仍依赖父进程全局的 `os.environ["CLAUDE_CONFIG_DIR"]`；同一个 B Pod 并发
运行多个私有 public-agent config home 时，不能靠临时修改进程环境解决，否则会串 session。
实现必须选择并验证一种方式：

1. 将这些路径帮助函数改为显式接收 per-run config home；或
2. 把 restricted CLI/session 操作放入独立 worker 进程，以进程环境隔离；或
3. restricted v1 明确禁用 resume/fork/rewind/heal，并使用易逝 session state。

在此项完成前，不能把“private `CLAUDE_CONFIG_DIR`”当成已具备的性质，也不能开放并发的
restricted 持久会话。

### 4.4 工具路径和扩展点仍需治理

即使 outer bwrap 已隐藏其他业务目录，平台仍需在 `can_use_tool`/内置工具层校验路径：

- Read/Edit/Write/Grep/Glob 的目标 `realpath` 必须落入 grant；
- FileCanvas、附件、文件 API 和 session mutation 使用同一 grant resolver；
- `/proc`、`/state`、`CLAUDE_CONFIG_DIR` 和平台策略文件不允许被模型文件工具读取；
- restricted Definition 不得启用任意 Hooks、plugins 或 stdio MCP；
- 平台托管 MCP 必须按 B 的身份与资源 ACL 重新授权，不能继承 A 的凭据。

这是防止“非 Bash 工具绕过 bubblewrap 设计意图”的必要条件。

## 5. 凭据与环境

### 5.1 Priva API Key / runner token

- B 的长期 API Key/JWT 在 control-panel 边缘验证后即终止；
- 转发到 runner 前必须删除原始 `Authorization`、`X-User-Name` 和客户端自带的全部
  `X-Priva-*` Header，再添加 control-panel 签发的短期 runner token；
- runner token 只存在于父服务处理入站请求的内存中，不进入 CLI；
- Definition、workspace 和 session transcript 中不得出现上述 token。

### 5.2 runner 服务环境

当前 runner 父进程持有账号范围的 `PRIVA_DATASPINE__SERVICE_TOKEN`。在 private Agent 的
单租户威胁模型里，账号读取自己的 token 风险受限；在第三方 Definition 模型里，这不再是
合理假设。

trusted launcher 必须先构造 allowlist 环境再启动 bubblewrap/Claude，至少删除：

- `PRIVA_DATASPINE__SERVICE_TOKEN`；
- 所有入站 `Authorization`/runner token；
- control-plane/service-identity 私钥（原则上本就不应挂入 runner）；
- 与本次 Agent 无关的用户 env、MCP token 和 Hook secrets。

不能只靠 `options.env` 增量覆盖，因为 SDK 会把它合并到继承的父进程环境；launcher 应在
进程内清空环境并只恢复白名单键，且不得把 secret 放进命令行 argv。

### 5.3 LLM BYOK

Claude CLI 必须持有 B 的 LLM 凭据才能调用模型，因此任何“CLI 永远看不到自身凭据”的承诺
都不真实。restricted 画像采取以下缩减：

- 不挂载 B 的普通 `.claude/settings.json`；为 public-agent 实例建立私有、无其他凭据的
  `CLAUDE_CONFIG_DIR`；
- runner 将本次需要的 provider credential 作为 CLI 环境传入；
- 强制 `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1`，从 Bash、Hooks 和 stdio MCP 子进程环境移除
  Anthropic/cloud provider 凭据，并在 Linux 为 Bash 使用隔离 PID namespace；
- restricted Definition 禁止提供任意 executable Hook/plugin/stdio MCP；
- 集成测试必须证明 Bash 无法从 `env`、`/proc`、settings 或调试日志恢复凭据。

如果当前 CLI/SDK 版本无法通过这组测试，restricted 画像不得上线；长期替代是经纪的短期
provider token/LLM gateway，而不是把原始 BYOK 暴露给不受信子进程。

## 6. 网络语义

`public-restricted-v1` 的网络合同是：

- 使用 Pod 当前 network namespace（`--share-net`）；
- 不增加 bubblewrap 网络 namespace、域名代理或域名 allowlist；
- 保留现有 Kubernetes NetworkPolicy：仍禁止横向访问其他租户 Pod和未授权控制面服务；
- 未挂载的 Unix socket 因文件系统不可见而不可达；不得挂载 Docker/containerd/Kubernetes
  等高权限 socket；
- DNS、TCP、UDP 和调用者 Pod 原有的公网出口必须进入验收测试。

这保留了用户要求的网络行为，同时明确接受：Definition 能把 **grants 内**的数据发到公网。
若未来需要防外传，必须新增 egress proxy/DLP；这不是 bubblewrap 的职责。

## 7. Kubernetes 与镜像改动

### 7.1 镜像

修改现有 agent-runner 镜像并发布新 tag，不增加 sidecar 或嵌套容器：

- 安装并固定非 setuid `bubblewrap`；
- 仅在启用 Claude native network sandbox 时才需要 `socat`；outer `--share-net` 本身不需要
  socat，但镜像可为后续内层 sandbox 一并提供；
- 安装 trusted launcher；
- 固定、记录并测试 Claude Agent SDK 与 bundled Claude Code 版本。

### 7.2 seccomp

当前 minikube/容器现场验证：`RuntimeDefault` 下
`unshare(CLONE_NEWUSER)` 返回 `EPERM`；在保持 non-root、drop ALL、no-new-privileges、只读根
文件系统的情况下，仅放开 seccomp 后可以创建 user namespace。

- PoC 可短期使用 `seccompProfile: Unconfined` 证明功能；
- 生产必须使用 Localhost seccomp（建议由 Security Profiles Operator 分发），从
  `RuntimeDefault` 基线只增加 bwrap 初始化实际需要的 syscall；
- 不添加 `SYS_ADMIN`，不使用 privileged Pod；
- 在生产节点验证 unprivileged user namespace、AppArmor userns 规则和
  `user.max_user_namespaces`；
- profile 缺失或 bwrap probe 失败时 Pod/运行 fail closed。

Kubernetes seccomp 作用于整个容器，因此新增 syscall 也会对 runner 父进程开放；这是一项已知
代价。外层 tenant Pod 边界和 non-root/cap-drop 继续保留。

## 8. 隔离组件横向对比与结论

### 8.1 先区分安全边界

这些组件不在同一层，不能只按“隔离强弱”排成一条线：

| 层级 | 本方案要防的事情 | 代表组件 | 不能单独解决的事情 |
|---|---|---|---|
| 运行内资源授权边界 | public Definition 在 B Pod 中读取未授权目录、其他 session 或 runner 状态 | bubblewrap、Landlock、nsjail、Firejail | Pod/容器逃逸、互联网外传 |
| Pod 到节点的内核边界 | Agent 利用 Linux 内核漏洞从租户 Pod 逃到节点或相邻 Pod | hardened runc、gVisor、Kata | 同一 B Pod 内不同运行之间的最小目录授权 |
| 执行单元与生命周期边界 | 每次运行独立挂载、凭据、网络策略、资源和销毁周期 | 每 Run Pod、Firecracker/microVM | Definition、工具和 API 自身的授权正确性 |

因此，gVisor/Kata 是现有 tenant Pod 边界的升级，bubblewrap/nsjail 是 **tenant Pod 内**的
per-run 边界；二者可以叠加，但彼此不是替代品。无论采用哪一层，LLM/API 授权、环境清洗、
工具路径校验和 egress 策略仍由平台负责。

### 8.2 针对当前需求的比较矩阵

评估使用以下硬条件：

- **G1：**整个 Claude CLI 只能看到服务端授予的业务目录，而不只是限制 Bash；
- **G2：**继续在 B 的既有 Agent Runner Pod 中运行，不新增每 Agent/每 Run Pod；
- **G3：**保留 B Pod 当前 DNS、TCP、UDP 和公网出口行为；
- **G4：**不使用 privileged、`SYS_ADMIN` 或 setuid helper；
- **G5：**不要求替换集群 container runtime，且可以按单次 public run 启用。

“满足 G1”只描述目录视图，不代表组件同时解决凭据泄露、工具授权或数据外传。

| 方案 | G1：整个 CLI 最小目录 | G2：复用 B Pod | G3：网络不变 | G4/G5：权限与接入 | 隔离边界及主要代价 | 本项目结论 |
|---|---|---|---|---|---|---|
| 只依赖 hardened runc tenant Pod | 否；CLI 仍能看到 Pod 已挂载的全部 B workspace | 是 | 是 | 是 | 已经是 A/B 租户边界，但没有本次运行的子目录边界 | 只适用于 `trusted` |
| Claude native `ClaudeAgentOptions.sandbox` | 否；官方强边界是 Bash 及其子进程 | 是 | 不保证完全等同；网络限制可经过 Claude proxy | 需要 bwrap/socat 和 SDK 设置，但不换 runtime | 与 Claude permissions 配合方便，不能覆盖 Read/Edit、in-process MCP、Hook 等所有入口 | 可做内层防御，不作为 G1 的唯一边界 |
| **outer bubblewrap** | **是；从空 mount namespace 只 bind 系统运行文件、Definition 和 grants** | **是** | **是；`--share-net`** | 非 setuid、无 capability；需镜像和允许 userns 初始化的 Localhost seccomp | 同宿主内核；不自带策略、资源调度或网络 ACL，安全性完全取决于 launcher 参数 | **`public-restricted-v1` 选定方案** |
| Landlock | 部分；能拒绝未授权文件操作，但不构造新的 root/mount/PID 视图 | 是 | 是；不设置网络 ruleset 即不改变网络 | 无需 user namespace/capability；依赖节点内核启用及 ABI 能力 | 可叠加且限制不可撤销；ABI 兼容和 denied-by-default rights 必须正确处理 | 可作 bwrap 内的第二层；不得作为 bwrap 失败时的降级路径 |
| nsjail | 是；可用 mount namespace、pivot_root/chroot、RO/RW bind、私有 proc/tmpfs | 是 | 是；关闭新的 network namespace，或另配 userland network | rootless 仍依赖 userns/相关 syscall；镜像需额外二进制、Kafel/protobuf policy | 同时提供 seccomp、rlimit、cgroup 和 supervisor，能力强但与 K8s/runner 现有资源治理重叠，策略面更大 | 可行替代，但当前没有超过 bwrap 的决定性收益；仅在需要统一 per-run seccomp/rlimit supervisor 时重评 |
| Firejail | 原理上可做目录/namespace 隔离 | 是 | 可配置 | 官方模型是 SUID sandbox，与 `allowPrivilegeEscalation:false`、非 setuid 基线冲突；功能/profile 面偏桌面应用 | namespaces、seccomp、capabilities、桌面 profile 集于一个高权限工具，审计与镜像面更大 | 不采用 |
| 只用 seccomp/AppArmor | 否；seccomp 过滤 syscall 而非路径，静态 LSM profile 也不等于动态 mount 视图 | 是 | 是 | 需要容器/节点 profile 管理；不能按任意 grants 自动得到安全策略 | 适合减少内核攻击面和阻止危险操作，不负责组装本次运行的可见文件树 | 仅作为 Pod/bwrap 配套防御 |
| gVisor (`runsc`) RuntimeClass | 否；它只看 OCI/Pod 已配置的 mounts，仍会看到 B Pod 内全部已挂载目录 | 是，但必须以该 RuntimeClass 重建整个 B Pod，不能只切换其中一次进程 | 目标可达性可保持，但使用 gVisor 网络/系统调用实现，需兼容测试 | 必须在节点安装 runtime/containerd shim 并配置 RuntimeClass | 用户态 application kernel 显著收窄 workload→宿主 Linux 内核攻击面；有 syscall、文件系统和性能兼容成本 | 推荐作为高风险租户 Pod 的后续加固试点，**仍需 bwrap** 做 G1 |
| Kata Containers RuntimeClass | 否；VM 内仍能看到该 Pod 被挂载的全部 B 数据 | 是，但同样只能按 Pod 选择 runtime | 通过 VM/virtio/CNI，需端到端兼容测试 | 需要 KVM/硬件虚拟化、Kata runtime、guest kernel/rootfs 和节点池 | 每 Pod 一个轻量 VM/独立 guest kernel，边界最强；启动、内存、存储和运维成本最高 | 仅用于合规或强对抗等级；**仍需 bwrap** 做 G1 |
| 直接使用 Firecracker | 只有把 grants 作为 microVM 唯一数据源时满足 | 否；本质上引入新的 microVM 执行单元 | 需要重新构建 tap/CNI/DNS/出口路径 | 不是可直接放进 PodSpec 的完整 K8s runtime；需 firecracker-containerd、Kata 或自建控制面，并要求 KVM | 极小 VMM/设备模型、启动快，但 guest 镜像、存储共享、快照、调度和生命周期都要平台化 | v1 不采用；若以后做 serverless/匿名 AgentRun，优先经 Kata 等成熟 CRI 集成评估，而非自建 VMM 控制面 |
| 每 Run Pod（runc/gVisor/Kata） | 是；Pod 只挂载本次 grants 时可形成清晰目录边界 | 否 | 可用 NetworkPolicy 接近现状，但连接、预热和回收语义变化 | runc 无需换 runtime；gVisor/Kata 需要 RuntimeClass；均增加 K8s 对象与调度 | 最清晰的进程、挂载、Secret、资源和销毁边界；代价是冷启动、PVC/会话映射、并发对象数与运维复杂度 | 模型 (a) v1 不采用；模型 (b)、匿名调用或允许任意 executable 扩展时升级 |

### 8.3 关键取舍

#### bubblewrap 与 nsjail

两者都能在现有 B Pod 内为整个 CLI 构造 mount/PID namespace，因此都能满足 G1。nsjail
额外集成 seccomp、rlimit、cgroup、网络和进程 supervisor，适合 CTF、fuzzing 或大量短命的
任意二进制；但 Priva 已由 Kubernetes 管理 cgroup/Pod 资源，由 runner 管理超时、取消和审计。
本场景真正缺失的是一个小而可审计的 **filesystem-view constructor**，bubblewrap 的职责更窄，
launcher policy 也更容易固定和测试。若未来需要在同一 Pod 内为每个 run 分配硬 cgroup 或完全
不同的 seccomp policy，再以同一组 §10 验收用例比较 nsjail，不能只替换二进制名称。

#### bubblewrap 与 Landlock

Landlock 的优势是非特权、可叠加且不依赖 user namespace；限制应用后只能继续收紧。它很适合
在 bwrap 已构造的 mount 视图内再次限制文件访问。但 Landlock 是访问控制，不会隐藏整个原始
文件树、创建私有 `/proc`，也不替代 PID/IPC namespace；不同内核 ABI 支持的文件和网络 rights
还不同。因此它是 defense-in-depth，不是满足“CLI 只看到指定目录”强合同的等价 fallback。

#### bubblewrap 与 gVisor/Kata

`runtimeClassName` 在 Pod 创建时选择，不能给同一个常驻 B Pod 中的某一次 Claude 子进程临时
切换。把整个 B Pod 改为 gVisor 或 Kata，可以降低 public Agent 从 Pod 逃到节点的风险，却不会
撤销 Kubernetes 已挂进该 Pod 的 sibling projects、普通 session 或 Secret。若启用这类 runtime，
正确组合仍是：

```text
Kata 或 gVisor tenant Pod（Pod→node 边界）
└── outer bubblewrap public run（run→B workspace/state 边界）
    └── Claude native sandbox（Bash 子进程的额外边界，可选）
```

#### 何时才值得每 Run Pod/microVM

模型 (a) 中，A 的 Definition 已在 B 的独立 tenant Pod 内运行；再创建 Pod 不会新增 A/B
租户边界，所以 v1 不支付调度和状态迁移成本。出现以下任一条件时，应升级执行单元，而不是继续
堆叠 bwrap 参数：

- 服务由匿名或外部多租户调用，运行不再自然归属于一个既有 B Pod（模型 (b)）；
- public Definition 可携带任意二进制、Hook、plugin 或 stdio MCP，而非平台注册能力；
- 每次运行必须有独立 NetworkPolicy、ServiceAccount、Secret 注入、硬资源配额或销毁证明；
- runner 无法可靠解决并发 session/config home、后代清理或父进程凭据继承。

### 8.4 最终结论

最终建议不是“给所有 Claude 套 bubblewrap”，也不是“用更强的 VM runtime 替换 bubblewrap”，
而是按威胁边界分层：

> **保留每租户 Pod；A 的 Definition 在 B Pod 中实例化；可信运行不强制沙箱；任何未经审核、
> 可执行 Bash/文件工具的 public Definition 必须进入 fail-closed 的 outer-bubblewrap restricted
> 画像，并同时满足授权、工具、凭据和 settings 四条配套边界。**

若实现团队暂时无法完成私有 `CLAUDE_CONFIG_DIR`、父环境白名单和工具路径 gate，则 v1 应先只
发布 `trusted`/声明式 Definition，不应以“已安装 bubblewrap”为由宣称 restricted 模式安全。

## 9. 落地顺序

1. **Definition 合同：**版本不可变、digest、能力声明、禁止字段、发布者/调用者/执行者分离。
2. **授权解析器：**`resource_id → canonical path → ro/rw mount`；resume 重新授权。
3. **凭据边界：**edge 删除原始 Authorization；launcher 环境白名单；public-agent 私有 config
   home；启用 subprocess credential scrub。
4. **PoC：**镜像安装 bwrap；临时 Unconfined；实现 trusted launcher；验证 `--share-net`。
5. **生产 K8s：**录制并分发 Localhost seccomp；恢复 fail-closed profile。
6. **SDK 接入：**public profile 禁止 bypass、固定 `cli_path`、禁用可写 settings sources、
   统一 `can_use_tool` grant gate。
7. **Registry/UI：**B 在首次运行和权限变化时确认 Agent 请求的目录、RW/RO、Bash 和网络能力。
8. **Canary：**仅开放给内部测试租户；通过 §10 全部验收项后再允许公开发布。

## 10. 上线验收门槛

restricted 画像必须自动化验证：

- RW grant 可创建、修改、删除；RO grant 不能写、rename、link 或覆盖；
- sibling 项目、普通 B `.claude`、其他 session、runner 配置和 Secret mount 不可
  `stat/list/read`；
- 软链接、`..`、bind-overlap、hardlink 和 resume grant-revocation 不能逃逸；
- Read/Edit/Grep/Glob、FileCanvas、Hooks、MCP 与 Bash 的结果一致；
- `env`、`/proc`、settings、debug log 中取不到 Priva token、data-spine token 和 provider key；
- B 的原始 API Key 不进入 runner upstream header、CLI、日志或 transcript；
- `cli_path`、settings、Definition 和运行请求均不能扩大 sandbox；
- bwrap、seccomp、userns 或 credential scrub 任一不可用时运行失败，不发生 unsandboxed fallback；
- DNS、HTTP(S)、普通 TCP、UDP 以及产品实际需要的网络工具与非沙箱 B Pod 行为一致；
- cancel/timeout/SIGTERM 能终止所有后代，无后台进程遗留；
- 并发 public runs 的 policy、目录、HOME、session state 和输出互不串扰；
- 授权目录中的数据可以经公网外传这一已接受风险，在产品确认界面和审计中明确可见。

## 11. 参考

- [Bubblewrap README](https://github.com/containers/bubblewrap/blob/main/README.md)
- [Bubblewrap manual](https://github.com/containers/bubblewrap/blob/main/bwrap.xml)
- [Linux Landlock userspace API](https://www.kernel.org/doc/html/latest/userspace-api/landlock.html)
- [nsjail README](https://github.com/google/nsjail/blob/master/README.md)
- [Firejail README](https://github.com/netblue30/firejail/blob/master/README.md)
- [gVisor security architecture](https://gvisor.dev/docs/architecture_guide/intro/)
- [gVisor Kubernetes integration](https://gvisor.dev/docs/user_guide/quick_start/kubernetes/)
- [Kata Containers architecture](https://github.com/kata-containers/kata-containers/blob/main/docs/design/architecture/README.md)
- [Kata Containers virtualization and VMM comparison](https://github.com/kata-containers/kata-containers/blob/main/docs/design/virtualization.md)
- [Firecracker architecture and integration overview](https://firecracker-microvm.github.io/)
- [Claude Code sandboxing](https://code.claude.com/docs/en/sandboxing)
- [Claude Code settings / sandbox keys](https://code.claude.com/docs/en/settings)
- [Claude Code environment variables (`CLAUDE_CODE_SUBPROCESS_ENV_SCRUB`)](https://code.claude.com/docs/en/env-vars)
- [Kubernetes seccomp](https://kubernetes.io/docs/reference/node/seccomp/)
- [Kubernetes RuntimeClass](https://kubernetes.io/docs/concepts/containers/runtime-class/)
