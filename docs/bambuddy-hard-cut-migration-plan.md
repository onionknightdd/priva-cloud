# Bambuddy 一次性品牌迁移实施计划

> 状态：待执行
>
> 审计基线：`main@c95e3e169900e660053a7e0d38d47db0a90053c1`
>
> 适用环境：仅预发，无生产流量
>
> 发布方式：全栈硬切、空环境重建、允许停机
>
> 数据策略：不迁移旧应用数据，本地和集群状态直接写入新位置

## 1. 结论

本次迁移不是兼容性升级，而是一次完整的品牌和技术标识硬切：新版本只识别
`Bambuddy` 命名，预发环境按空环境重新创建，所有前后端组件必须由同一提交构建
并在同一维护窗口发布。

不实现以下能力：

- 不兼容旧环境变量、包名、CLI、entry point 或 import path。
- 不读取、复制、转换或软链接旧本地目录。
- 不同时支持两套 HTTP Header、WebSocket、gRPC、JWT、MCP 或 Hook 契约。
- 不迁移旧 localStorage、Cookie、BroadcastChannel 或浏览器缓存。
- 不迁移 SQLite、Postgres、PVC、租户 workspace、session 或业务记录。
- 不复用旧 JWT、HMAC、Fernet、service identity 等应用密钥。
- 不保留旧 CRD/API group、Kubernetes label 或 Helm release 作为回滚通道。
- 不设置双品牌窗口，也不安排后续“删除兼容层”的第二次发布。

发生问题时采用 fix-forward：修复当前 Bambuddy 分支、重新构建同一套全栈产物并
重建 Pod。回退范围仅限 Bambuddy 的前一个已验证提交，不回退到旧品牌版本。

## 2. 前提与边界

本计划建立在以下前提之上：

- 当前只有预发环境，可以接受维护窗口和重新初始化。
- 预发应用数据可以丢弃；若存在必须保留的业务记录，必须在执行前单独提出，届时
  Postgres、PVC 和密钥策略需要重新设计。
- 管理员、用户、API Key、定时任务、Hook、飞书/企微绑定可以重新创建。
- Anthropic/BYOK、飞书、企微、registry 等外部凭据由安全来源重新录入。
- Gateway API、GIE、`agentgateway-system`、StorageClass、ALB 等共享基础设施不属于
  删除范围。
- 域名、证书、镜像仓库和 Kubernetes API group 使用组织实际拥有或控制的标识。

本次只做品牌和相关技术标识迁移，不顺手重命名中性领域名称：

- `data-spine`、`control-panel`、`scheduler`、`channel-connector`、`agent-runner`
  可以保持不变。
- `AgentTenant` Kind 可以保持不变，只修改 API group、labels 和 annotations。
- 当前中性的 SQL 表名与 Redis key 不需要重命名。
- 第三方名称、`private`/`privacy`、Claude、Anthropic、FileCanvas 等不得误改。
- vendored/generated 文件不得手工替换，必须从源文件重新生成。

## 3. 执行前必须锁定的决定

- [ ] 展示名称：`Priva` → `Bambuddy`。
- [ ] 产品全称：`Priva Cloud` → `Bambuddy Cloud`。
- [ ] 小写标识：`priva` → `bambuddy`。
- [ ] 环境变量前缀：`PRIVA_` → `BAMBUDDY_`。
- [ ] Kubernetes namespace：`priva-cloud` → `bambuddy-cloud`。
- [ ] Helm release：`priva` → `bambuddy`。
- [ ] CLI：`priva-cloud` → `bambuddy-cloud`。
- [ ] 镜像组织/前缀：`priva/*` → `bambuddy/*`。
- [ ] Kubernetes API group 使用组织实际控制的 DNS 域。只有确认拥有
  `bambuddy.io` 后才能采用该域名。
- [ ] 确认继续使用当前机器人图标，还是提供正式 Bambuddy logo。
- [ ] 确认 favicon 是否与侧栏 mark 使用同一图形。
- [ ] 确认 `LICENSE` 中版权主体改成 `Bambuddy contributors` 还是公司法律主体。

`LICENSE` 的版权主体属于法律信息，不允许通过全局搜索替换自动决定。

## 4. 分支与工作区策略

当前主工作树可能包含未提交的用户修改。迁移必须在独立 worktree 中进行，不能
混入已有改动：

```bash
git fetch origin main

REBRAND_BASE_SHA="$(git rev-parse origin/main)"
git worktree add ../bambuddy-rebrand \
  -b chore/bambuddy-hard-cut \
  "$REBRAND_BASE_SHA"

cd ../bambuddy-rebrand
git status --short
```

执行期间如果 `origin/main` 前进，先重新审计变更范围，再 rebase；不要机械解决
涉及品牌或生成物的冲突。

## 5. 目录、包和文件映射

以下目录使用 `git mv` 原子重命名，并同步修改所有 import、动态模块字符串、测试、
打包配置和文档引用：

| 当前路径 | 目标路径 |
|---|---|
| `deploy/helm/priva-cloud` | `deploy/helm/bambuddy-cloud` |
| `libs/common/src/priva_common` | `libs/common/src/bambuddy_common` |
| `packaging/priva-cloud` | `packaging/bambuddy-cloud` |
| `priva` | `bambuddy` |
| `protos/priva_common` | `protos/bambuddy_common` |
| `services/agent-runner/src/priva_agent_runner` | `services/agent-runner/src/bambuddy_agent_runner` |
| `services/agent-runner/src/priva_agent_runner/bundled/skills/priva-user-manual` | `services/agent-runner/src/bambuddy_agent_runner/bundled/skills/bambuddy-user-manual` |
| `services/agent-runner/src/priva_agent_runner/services/priva_plugin` | `services/agent-runner/src/bambuddy_agent_runner/services/bambuddy_plugin` |
| `services/channel-connector/src/priva_channel_connector` | `services/channel-connector/src/bambuddy_channel_connector` |
| `services/control-panel/src/priva_control_panel` | `services/control-panel/src/bambuddy_control_panel` |
| `services/data-spine/src/priva_data_spine` | `services/data-spine/src/bambuddy_data_spine` |
| `services/operator/src/priva_operator` | `services/operator/src/bambuddy_operator` |
| `services/scheduler/src/priva_scheduler` | `services/scheduler/src/bambuddy_scheduler` |
| `tools/cli/src/priva_cli` | `tools/cli/src/bambuddy_cli` |

单独重命名：

- `docs/priva-cloud-architecture-report.html` →
  `docs/bambuddy-cloud-architecture-report.html`。
- 所有 wheel、distribution、package 和 workspace 名称。
- `web/package.json` 中的 workspace/package 名称，并通过 npm 更新 lockfile。

代码级清单：

- [ ] 所有 `priva_*` Python import 和测试 patch path。
- [ ] 所有 `Priva*` 类名、类型名和公开符号，例如 `PrivaPlugin`。
- [ ] 所有 `priva_*` 函数、变量、fixture 和参数，例如 `priva_home`。
- [ ] `pyproject.toml` 中 package include、distribution、dependency 和 script。
- [ ] entry point group `priva_cloud.services` → `bambuddy_cloud.services`。
- [ ] CLI command `priva-cloud` → `bambuddy-cloud`。
- [ ] `__main__`、动态 import 字符串、module discovery 和 plugin loader。
- [ ] 根目录构建脚本、Dockerfile、`.gitignore`、`.dockerignore`。
- [ ] 单元测试、集成测试和 fixture 中的路径、模块名及样例。

## 6. 协议与运行时契约硬切

协议变更必须与调用方在同一个提交系列中完成，不提供旧值 fallback。

### 6.1 Proto 与 gRPC

- [ ] 源 proto 目录和 import 改为 `bambuddy_common`。
- [ ] proto package `priva.dataplane.v1` 改为最终 Bambuddy package。
- [ ] 修改 service descriptor、客户端和服务端引用。
- [ ] 从 proto 源重新生成 Python stubs，不手改生成文件。
- [ ] 只注册新 gRPC service/package。

### 6.2 HTTP、WebSocket、JWT 与 MCP

- [ ] `X-Priva-*` → `X-Bambuddy-*`。
- [ ] `priva.ws.v1` → `bambuddy.ws.v1`。
- [ ] `priva.token.*` → `bambuddy.token.*`。
- [ ] `priva.target.*` → `bambuddy.target.*`。
- [ ] JWT issuer/audience 改为 Bambuddy，新旧登录状态不兼容。
- [ ] MCP server、tool 完整名称和 UI renderer 一起修改。
- [ ] OpenClaw 等包含旧品牌的完整 tool id 一次性修改。
- [ ] Hook marker `__priva_enforced` → `__bambuddy_enforced`。
- [ ] OpenAPI title、User-Agent、日志字段和 CLI help 中的品牌标识。

### 6.3 Metrics 与运行时文件

- [ ] `priva_*` metrics → `bambuddy_*`，不双写旧指标。
- [ ] 仪表盘和告警同步改查询表达式。
- [ ] 本地状态只写入新路径，例如：
  - `/workspace/.bambuddy`
  - `~/.config/bambuddy`
  - `/data/bambuddy.dataspine.db`
  - `bambuddy_workspace`
- [ ] 不探测、读取、复制或清理用户机器上的旧路径。
- [ ] 测试断言新运行过程不会创建旧路径。

## 7. Kubernetes、Helm 和镜像

### 7.1 统一命名

- [ ] Chart 目录、`Chart.yaml` name、helper prefix、release 和 namespace。
- [ ] `app.kubernetes.io/name`、`part-of`、selector、annotation 和 owner reference。
- [ ] ConfigMap、Secret、ServiceAccount、Role/Binding、ClusterRole/Binding。
- [ ] Gateway、HTTPRoute、Ingress、InferencePool 和证书 SAN。
- [ ] CRD group、plural、RBAC resource、代码中的 `GROUP` 常量。
- [ ] `PRIVA_*` Docker ENV、Pod env 和 Secret key → `BAMBUDDY_*`。
- [ ] raw manifests、Helm、minikube、UAT 脚本和部署文档保持一致。

建议的新应用对象名称包括：

- namespace：`bambuddy-cloud`
- Helm release：`bambuddy`
- `bambuddy-shared-secret`
- `bambuddy-control-panel-secret`
- `bambuddy-data-spine-secret`
- `bambuddy-regcred`

`postgres-secret` 是中性名称，可以保留。

### 7.2 镜像

至少构建并推送以下镜像，全部使用不可变 tag：

- `bambuddy/control-panel`
- `bambuddy/agent-runner`
- `bambuddy/data-spine`
- `bambuddy/operator`
- `bambuddy/scheduler`
- `bambuddy/channel-connector`
- 按启用情况构建 `bambuddy/nfs-xfs`

当前已知缺口：

1. `deploy/uat/build-push.sh` 未构建/推送 `scheduler`，必须补齐。
2. `deploy/minikube/build.sh` 默认遗漏 `scheduler`，`up.sh` 也未应用其
   manifest/RBAC，必须补齐。

### 7.3 数据库、存储和密钥

- [ ] 创建新 Postgres database/role/password，建议 database 和 role 均使用
  `bambuddy`。
- [ ] DSN 指向新 namespace 内的 Postgres Service。
- [ ] 创建全新的 PVC，不执行 `pg_dump`、SQLite copy、VolumeSnapshot 或 PV rebind。
- [ ] 重新生成 JWT、HMAC、Fernet、service identity keypair。
- [ ] 重新录入 Anthropic/BYOK、飞书、企微和 registry 凭据。
- [ ] 不将 Secret 明文写入迁移文档、issue 或提交记录。
- [ ] UAT 使用 Postgres 时，不创建 legacy SQLite source/rollback PVC。
- [ ] 如果产品仍支持 SQLite，仅在 `backend=sqlite` 时创建其 PVC 和 mount。

## 8. Web 用户端和管理端

本阶段只迁移品牌和技术标识，不改变现有 GitHub Dark 设计语言。

### 8.1 可见界面

- [ ] `web/user/index.html` 和 `web/admin/index.html` title。
- [ ] 用户侧栏、管理侧栏和收起状态的品牌展示。
- [ ] 登录、注册、初始化和 onboarding/intro 场景。
- [ ] Ask、Skill Sync、Scheduler、Terminal、WeCom 等说明文案。
- [ ] System Topology 的 aria-label 和 cluster label。
- [ ] 中英文 locale 的品牌 value 与语义 key。
- [ ] favicon 和品牌 mark。
- [ ] 所有 hover、响应式和无水平滚动规则继续满足设计规范。

### 8.2 浏览器内部契约

以下值不能仅修改显示文案，必须修改定义和所有读写方：

- [ ] `__PRIVA_TOKEN_KEY__`。
- [ ] user/admin/fallback token key。
- [ ] theme、developer mode、debug mode、intro 和 chunk reload key。
- [ ] checkpoint、rewind、session status、transport key。
- [ ] BroadcastChannel 名称。
- [ ] split-pane `postMessage` event。
- [ ] session drag MIME。
- [ ] tab id、refresh marker 和 active navigation id。
- [ ] Skill Sync target mode。
- [ ] `privaTheme` 等状态字段。
- [ ] WebSocket subprotocol、sentinel、token 和 target prefix。

旧浏览器状态不迁移。发布说明要求清除站点 cookies、localStorage 和缓存，然后
分别重新登录 user/admin。

### 8.3 代码标识

- [ ] `handleAskPriva*`。
- [ ] `RemotePrivaForm`。
- [ ] `getPrivaBaseUrl`。
- [ ] `PRIVA_BASE_URL_PLACEHOLDER`。
- [ ] `remotePriva*`、`askPriva*`、`tabs.priva` 等 i18n key。
- [ ] comments、fixture 和示例路径。

### 8.4 UI 确认门

根据仓库规则，修改组件前需要用 ASCII 图确认布局。默认提案如下：

```text
用户侧栏（180–480px）
┌──────────────────────┐
│ [Mark] Bambuddy  [<] │
├──────────────────────┤
│ 会话 / Skills / ...  │
└──────────────────────┘

收起
┌──────┐
│ Mark │
└──────┘

管理侧栏
┌──────────────────────┐
│ [Mark] Bambuddy  [<] │
│        ADMIN    [中] │
├──────────────────────┤
│ Dashboard / ...      │
└──────────────────────┘
```

`Bambuddy` 比旧名称长，管理侧栏应采用两行布局，重点验证 180px、240px、480px、
collapsed、中英文和窄视窗，不能依赖文本截断掩盖布局问题。

## 9. 文档、手册和元数据

- [ ] `README.md`、`AGENTS.md`、`CLAUDE.md`。
- [ ] `docs/` 下所有架构、ADR、功能、迁移历史和操作文档。
- [ ] 产品规格、架构报告、live/shared preview 等 standalone HTML。
- [ ] deploy、common、services、protos 目录中的 README。
- [ ] bundled user manual 的目录、frontmatter、`SKILL.md` 和 references。
- [ ] package metadata、Chart metadata、CLI help、OpenAPI 和日志示例。
- [ ] 外部 Git remote、registry、域名、Dashboard、Bot 名称和 Secret Manager key。

历史描述不能盲目替换。例如“旧品牌最初是单机应用”应改写为“项目早期的单机
版本”，避免把历史事实错误归到 Bambuddy 名下。

`web/design-spec.md` 中的 `Terminal Codex`、`Agent Ops Console`、`ops-console`
不包含旧品牌，自动扫描不会发现。需要人工确认它们是正式设计术语还是应一并
废弃的早期工作名。

## 10. 生成物策略

所有生成物必须从迁移后的源文件重新生成：

- [ ] protobuf stubs。
- [ ] `uv.lock`。
- [ ] `package-lock.json`。
- [ ] user/admin SPA。
- [ ] control-panel staged `_web`。
- [ ] wheel。
- [ ] Docker images。

禁止直接编辑 minified JavaScript、protobuf 生成代码或 wheel。`scripts/build-wheel.sh`
不得使用 `--skip-web`，避免把本机旧 SPA 打入新 wheel。

需要扫描 ignored/untracked 生成物，因为 `git grep` 不会覆盖它们。

## 11. 预发发布与清理顺序

### 11.1 停机前

- [ ] 完成源代码迁移、自动测试和旧品牌扫描。
- [ ] 构建并推送全部镜像，确认 `scheduler` 没有遗漏。
- [ ] 记录 kube context、集群 API 地址、目标 namespace UID，防止删错集群。
- [ ] 记录 registry、StorageClass、Gateway/ALB、证书、域名、DNS 和 TTL。
- [ ] 不导出旧业务数据库，不备份旧 Secret 明文。
- [ ] 创建 `bambuddy-cloud` namespace 和 registry pull secret。
- [ ] 先以 `ingress.enabled=false`、`channelConnector.enabled=false` 安装 Bambuddy。
- [ ] 通过 port-forward 完成空库初始化、管理员创建和基础 smoke test。

### 11.2 切换窗口

1. 暂停外部 GitOps/自动部署，避免旧 release 被重新创建。
2. 停止旧入口，或删除旧 HTTPRoute/Ingress。
3. 将旧 control-panel、scheduler、channel-connector scale 到 0。
4. 暂时保留旧 operator。
5. 删除旧 `AgentTenant`，让旧 operator 执行 finalizer 并清理租户资源/PVC。
6. 等待旧 CR、runner、terminal 和租户 PVC 消失。
7. 启用 Bambuddy ingress/channel connector，切换 DNS/ALB route。
8. 验证新入口后，卸载旧 Helm release。
9. 删除旧 namespace、旧 CRD 和旧 ClusterRole/Binding。
10. 检查并清理 claimRef 指向旧 namespace 的 Released/Retain PV。
11. 清除浏览器站点数据，重新登录并录入外部凭据。
12. 最后再清理旧 registry images。

必须先停止旧 control-panel，因为它会周期性按数据库账号重新创建 `AgentTenant`。
不要先卸载旧 operator，否则带 finalizer 的 CR 或 namespace 可能卡在
`Terminating`。若 finalizer 失败，应先定位明确对象再处理，不把强制移除 finalizer
作为默认步骤。

旧 chart 的 CRD/Secret 可能带 `helm.sh/resource-policy: keep`；`helm uninstall`
后必须检查 cluster-scoped 残留。不得删除共享的 Gateway API、GIE 或
`agentgateway-system`。

## 12. 验证门禁

### 12.1 源文件与路径扫描

使用避免误报 `private`/`privacy` 的品牌边界规则：

```bash
LEGACY_BRAND_RE='PRIVA(?:$|[_./:-])|Priva(?:$|[^a-z])|priva(?:$|[_./:-]|[A-Z])'

git grep -I -n -P "$LEGACY_BRAND_RE"
git ls-files | rg '(^|/)(priva($|/)|priva[-_])|Priva|PRIVA'
```

在迁移执行期间，本计划是唯一允许包含旧、新映射的文档。完成迁移后有两种处理
方式，必须二选一：

1. 默认方案：将本计划移出产品仓库或删除，再执行零残留门禁。
2. 若必须永久保留审计记录，仅对本文件设置精确 allowlist；产品代码、配置、产物
   和其他文档仍必须零输出。

不得扩大 allowlist。

### 12.2 后端与构建

```bash
./protos/gen.sh
pytest
uv lock --check

(cd services/terminald && go test ./... && go build ./...)

npm --prefix web ci
npm --prefix web run build

find web -type f -name '*.test.js' -print0 | xargs -0 node --test

./scripts/build-wheel.sh
```

在干净临时虚拟环境安装 `dist/bambuddy_cloud-*.whl`，然后验证：

```bash
bambuddy-cloud --help
pip check
unzip -Z1 dist/bambuddy_cloud-*.whl | rg -i 'priva'
unzip -p dist/bambuddy_cloud-*.whl | strings | rg -i 'priva'
```

最后两条命令必须零输出，迁移计划文档不应进入 wheel。

### 12.3 前端产物

```bash
rg --hidden --pcre2 '(?i)(?<![a-z])priva(?![a-z])' \
  web/user/dist \
  web/admin/dist \
  services/control-panel/src/bambuddy_control_panel/_web \
  -g '!file-icons/**'
```

预期零输出。并验证：

- [ ] user/admin title、favicon、登录、侧栏和设置页。
- [ ] onboarding、Ask、Skill Sync、Scheduler、Terminal、Topology。
- [ ] 180/240/480px 侧栏、collapsed、中英文和小视窗无水平滚动。
- [ ] 多 tab SSE、split-pane、拖放、主题同步使用新 channel/event/MIME。
- [ ] 新 WebSocket 协议握手成功，旧值明确失败。
- [ ] 新 MCP tool 和 Hook marker 正确显示。

### 12.4 Helm 与 Kubernetes

```bash
helm lint deploy/helm/bambuddy-cloud \
  -f deploy/helm/bambuddy-cloud/values-uat.yaml

helm template bambuddy deploy/helm/bambuddy-cloud \
  -n bambuddy-cloud \
  -f deploy/helm/bambuddy-cloud/values-uat.yaml \
  > /tmp/bambuddy-uat.yaml

rg -n 'Priva|priva|PRIVA' /tmp/bambuddy-uat.yaml
kubectl apply --dry-run=server -f /tmp/bambuddy-uat.yaml
```

rendered manifest 的品牌扫描必须零输出。还必须确认：

- [ ] 全部服务镜像存在、架构匹配且 tag 不可变。
- [ ] registry pull test 通过。
- [ ] Postgres、data-spine、operator、control-panel、scheduler、connector Available。
- [ ] control-panel `/health` 中 data-spine dependency 的 `ok=true`，不能只看 HTTP 200。
- [ ] Gateway `Programmed=True`。
- [ ] HTTPRoute `Accepted=True`、`ResolvedRefs=True`。
- [ ] 新建账号后出现新 API group 下的 `AgentTenant`、PVC、Deployment、Service。
- [ ] runner 从 0 唤醒到 Ready。
- [ ] HTTP/SSE/WS agent 请求通过。
- [ ] terminal、scheduler、飞书/企微按启用范围分别通过 smoke test。
- [ ] Postgres database/role、DSN、证书 SAN 和外部 HTTPS 正确。
- [ ] 只创建新的本地路径和 PVC。

清理完成后检查：

```bash
kubectl get ns,crd,clusterrole,clusterrolebinding,pv -o name | rg -i 'priva'
helm list -A | rg -i 'priva'
```

还要扫描所有 namespace 中对象的名称、labels、annotations、env 和镜像引用。

## 13. 推荐提交顺序

所有提交位于同一迁移分支，并最终作为一次全栈发布：

1. `test(rebrand): add hard-cut inventory gate`
2. `refactor(rebrand): rename packages modules and imports`
3. `refactor(rebrand): rename runtime protocols and state paths`
4. `chore(rebrand): rename kubernetes helm and images`
5. `feat(rebrand): update user and admin brand surfaces`
6. `docs(rebrand): rename docs manuals and metadata`
7. `build(rebrand): regenerate locks stubs spas and wheel`
8. `test(rebrand): verify fresh staging install`

不存在兼容层清理提交或后续兼容版本。

## 14. Definition of Done

只有以下条件全部满足，迁移才算完成：

- [ ] 所有命名决策、API group、视觉资产和版权主体已确认。
- [ ] 所有源代码目录、包、import、方法、变量和测试使用 Bambuddy 命名。
- [ ] 新前后端由同一 commit 构建，不存在混跑。
- [ ] 所有运行时协议、浏览器契约和 metrics 只接受新值。
- [ ] 本地状态只写新路径，不读取旧路径。
- [ ] 新 namespace、数据库、PVC、Secret、CRD 和 RBAC 从空环境创建。
- [ ] 全部自动化测试、构建、wheel、Helm dry-run 和 E2E 通过。
- [ ] user/admin、文档、手册、HTML 和生成物完成品牌更新。
- [ ] 产品代码、配置、构建产物和集群资源的旧品牌扫描为零。
- [ ] 旧 namespace、CRD、ClusterRole/Binding、PV 和 Helm release 已清理。
- [ ] 共享集群基础设施未被误删。
- [ ] 浏览器清站点数据后，user/admin 能重新登录并完成核心流程。
- [ ] 本计划按第 12.1 节处理，不留下无边界的扫描例外。
