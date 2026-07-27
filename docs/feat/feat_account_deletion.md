# Feat: 账户停用与删除（两阶段生命周期）

> 设计定稿 2026-07-27，同日实施完成（未提交，未部署）。决策：两阶段（停用→清除）；清除即销毁数据；
> 活跃账户可强制删除（对话框展示状态）；操作入口收进 UserEditDrawer 危险区。
> 实施后的偏差与运维注意事项见文末「已实施状态」。

## 背景：现状盘点

Admin 已有 `DELETE /api/admin/users/{username}`（`routers/admin.py:468`）和带输入用户名确认的删除按钮，但**只删数据库行**，运行时资源全部泄漏：

| 环节 | 现状 |
|---|---|
| DB（account + 7 张级联表） | ✅ FK CASCADE 全清（channel_binding / quota / scheduled_job / job_run_record / job_fire / account_resource_spec / feishu_channel_config） |
| JWT / API key | ✅ 每请求回查 DB（`services/auth.py:63`），行删即 401 |
| Feishu 连接、scheduler、网关路由 | ✅ 级联 + re-list / label-selector 自愈 |
| AgentTenant CR | ❌ 孤儿化，operator 持续 reconcile 并重新供给存储 |
| `ar-{acct}` / `term-{acct}` Deployment+Service | ❌ 永久留存 |
| 每账户存储（dev NFS loop 镜像 / prod CephFS PVC） | ❌ 永久泄漏；quota-manager 无 DELETE 接口；`StorageBackend` 无 deprovision |

另有一套**已建好但从未接通**的软删除基础设施：`account.status` CHECK 含 `disabled/offboarding/purged`；CR `desiredState` 含 `offboarding/purge`；operator `_quiesce_if_inactive`（`reconcile.py:106`）见非 active 即缩容为 0；extproc 对非 active 账户 403（`extproc.py:181`）；`require_active_account` 已存在。但没有任何 API 写入非 active 状态。

## 状态机

```
           POST /users/{u}/disable              DELETE /users/{u}
  ACTIVE ─────────────────────────▶ DISABLED ──────────────────▶ PURGING ──▶ 行删除
    ▲                                   │                        (202)      (用户名释放)
    │      POST /users/{u}/enable       │
    └───────────────────────────────────┘
                     DELETE 可从 ACTIVE 直接发起（强制路径，对话框展示活跃状态）
```

- **停用（可逆）**：DB `status=disabled` → 登录拒绝、网关 403、scheduler 不触发；patch CR `desiredState=offboarding` → operator 缩容为 0。数据全保留。
- **清除（不可逆）**：DB 先标 `status=purged`（墓碑，防 teardown 中途失败后复活）→ control-panel 删 AgentTenant CR → operator finalizer 完成 teardown → `sync_all_tenants` 周期循环发现 CR 已消失后补刀删 DB 行。接口立即返回 202，UI 行显示 PURGING 直至消失。

## 各层改动

### control-panel
- `routers/admin.py`：新增 `POST /users/{u}/disable`、`POST /users/{u}/enable`（专用端点，不走 UserUpdate DTO）；改造 `DELETE /users/{u}`：标记 purged + 删 CR + 返回 202。保留现有守卫（不能删自己、最后一个 admin）。
- `provisioner.py`：新增 `delete_tenant()`（delete CR）；`sync_all_tenants` ①跳过 purged（禁止重建 CR）②发现 purged 且 CR 已消失 → 删 DB 行（幂等收尾，兼作崩溃恢复）；disabled 账户 CR 收敛 `desiredState=offboarding`。
- RBAC `deploy/rbac/control-panel-rbac.yaml`：agenttenants 加 `delete` verb（一行）。
- 登录（`routers/auth.py`）：拒绝 `status != active` 的账户，给明确报错。

### operator
- `reconcile.py`：新增 `@kopf.on.delete` handler（kopf 自动挂 finalizer）：缩容 Runner/Terminal → `StorageBackend.deprovision(account_id)` → 显式删 CephFS `ar-{acct}-export` PVC（该 PVC 无 owner-ref，`storage_backend.py:171` 创建时未挂）→ 摘 finalizer，owner-ref GC 清 Deployment/Service。RBAC 已够（已有 deployments/services/PVC 的 delete）。
- 共享的 `claude-managed-policy` ConfigMap 是全局对象，**严禁**随账户删除。

### 存储
- `storage_backend.py`：`StorageBackend` 新增 `deprovision(account_id)`，两后端实现，均幂等（目标不存在视为成功）。
- dev：`deploy/dev-storage/quota-manager/app.py` 新增 `DELETE /accounts/{id}`（umount loop → rm `/data/images/{id}.img` → rmdir `/export/{id}`）。
- prod：`delete_namespaced_persistent_volume_claim(ar-{acct}-export)`。

### data-spine
- 基本零改动：`AccountService.update` 已支持 status（`service.py:114`），只是没有路由传；`user_store.py` façade 透传 status 即可。

### 需顺手验证/修补的细节
- channel-connector `list_effective()` 需过滤非 active 账户，否则停用后 Feishu WS worker 仍挂着（目前只在账户行消失时 teardown）。
- scheduler `fire()` 已守卫 inactive（`engine.py:233`），无需改。

## UI（web/admin）

### 用户表（UserManagement.jsx）
- 新增**状态列**：ALL CAPS chip — ACTIVE `var(--green)` / DISABLED `var(--yellow)` / PURGING `var(--red)`；disabled 行文字降为 `var(--text-secondary)`；PURGING 行只读。
- 行内操作**只保留 ✎ 编辑**（现有行内 🗑 删除移除，迁入抽屉危险区）。

### UserEditDrawer 危险区（新增）

```
┌─ UserEditDrawer ────────────────────────┐
│  bob                                    │
│  角色 / 密码 / Runner / 资源 / API Key    │
│  Feishu 配置                             │
│  ─────────────────────────────────────  │
│  危险区（1px var(--red) 边框，4px 圆角）   │
│  ┌───────────────────────────────────┐  │
│  │ 停用账户                           │  │
│  │ 冻结登录并停止 runner，数据保留       │  │
│  │                        [ 停用 ]    │  │
│  ├───────────────────────────────────┤  │
│  │ 永久删除账户                        │  │
│  │ 销毁全部数据与运行时资源，不可恢复     │  │
│  │                        [ 删除… ]   │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
```

- 停用：`showConfirmDialog({danger: true})`，展示当前状态，无需输入用户名（可逆）。已停用时按钮变「启用」。
- 删除：复用 `ConfirmDialog` 的 `requireText: username`，消息区新增当前状态块（fleet 数据现成）：

```
┌───────────────────────────────────────────┐
│  删除账户 bob                              │
│───────────────────────────────────────────│
│  当前状态                                  │
│    Runner:  ● awake · 2 个活跃 run ←红色    │
│    Feishu:  已绑定                         │
│  以下内容将被永久销毁：                      │
│    · 运行中的任务将被强制终止                │
│    · 全部对话历史 / 工作区文件 / BYOK 凭据    │
│    · runner / terminal 实例与专属存储        │
│  输入用户名 bob 以确认：                     │
│  [_______________________]                │
│             [ 取消 ]   [ 永久删除 ]         │
└───────────────────────────────────────────┘
```

- API 层：`web/shared/api/admin.js` 新增 `disableUser/enableUser`；删除后 `fetchUsers()` 轮询至 PURGING 行消失（沿用手动 re-fetch 模式）。

## 实施顺序

1. 存储层：`StorageBackend.deprovision` + quota-manager DELETE（可独立测试）。
2. operator finalizer + teardown（配合 1，用手删 CR 验证完整回收）。
3. control-panel：disable/enable 端点、DELETE 改造、`sync_all_tenants` 收尾逻辑、RBAC、登录守卫、connector 过滤。
4. admin UI：状态列 + 抽屉危险区 + 对话框（前端可热载验证）。

## 验证清单

- [ ] 停用 → 网关 403 / 登录拒绝 / pod 缩容为 0 / Feishu worker 停止 / scheduler 不触发
- [ ] 启用 → 全部恢复，数据完好
- [ ] 删除（active、有活跃 run）→ 强杀、CR 消失、Deployment/Service 被 GC、存储镜像/PVC 消失、DB 行消失、用户名可复用
- [ ] teardown 中途 kill operator → 重启后 finalizer 继续收尾（幂等）
- [ ] 删除后 `sync_all_tenants` 不复活 CR
- [ ] 现有守卫仍生效：不能删自己、不能删最后一个 admin

---

## 已实施状态（2026-07-27）

代码已全部落地在工作区，**未提交、未部署**。本地门禁：452 passed / 16 skipped，`build:admin`
与 `build:user` 均通过。改动 19 个文件 + 2 个新测试文件
（`tests/control_panel/test_account_lifecycle.py`、`tests/operator/test_purge_finalizer.py`）。

### 设计外的关键修正（审查阶段发现）

- **管理员锁死**：清除改为打墓碑后 `count_admins()` 仍是状态盲 SQL（`WHERE role='admin'`），
  purged 的 admin 行继续计数，可把管理员逐个删光。三处减少管理员的路径（删除、停用、角色降级）
  统一改用 `_active_admin_count`，只计 `role=admin AND status=active`。
- **Helm chart 漏改**：`deploy/rbac/control-panel-rbac.yaml` 加了 `delete` 动词，
  但 `deploy/helm/.../rbac-control-panel.yaml` 没有 → helm 部署环境清除必然 403。已同步。
- **回收循环整体卡死**：`sync_all_tenants` 里 `delete_tenant` / `set_tenant_desired_state`
  的非 404 异常会中断整趟，饿死列表后面所有账户（含等待收割的墓碑）。已按账户容错。
- **operator teardown 守卫**：`_purging` 原按 account_id 记录且永不清理，带外
  `kubectl delete agenttenant` 会让 sync 重建的 CR 被永久早退。改为记录 CR uid，只拦同一对象。
- **403 降级 401**：生命周期闸门移到 token 解析后抛 `HTTPException(403)`，被 `extproc.py`
  和 `app.py` 的裸 `except` 吞成 401。两处都已保留 403 语义。

### 运维注意事项（部署前必读）

1. **kopf 会给每个 AgentTenant CR 挂 finalizer**（已存在的 CR 在 resume 时补挂）。此后
   `kubectl delete agenttenant` 会卡在 Terminating 直到 operator 处理；operator 不在线时
   CR 会一直悬着，需手工摘 `metadata.finalizers`。这是本设计的固有代价。
2. **quota-manager 必须重建镜像**：`deploy/dev-storage/quota-manager/app.py` 是 COPY 进
   `priva/nfs-xfs:dev` 的（不是 ConfigMap 挂载）。镜像未重建前运行中的 quota-manager 没有
   DELETE 路由，返回 405 → operator finalizer 重试 10 次后放弃并打 LEAKED 日志，loop 镜像残留。
3. **本功能不可热载**：改动跨 control-panel / operator / quota-manager 三个镜像，前端单独热载
   会调用到不存在的接口。端到端验证需要完整重建 + 重新部署。
4. **quota-manager 全程无鉴权**（既有状况，POST/PUT 同样裸奔），新增的 DELETE 把影响面从
   "改配额"抬到"销毁他人工作区"。已加账户 id 格式校验堵死路径穿越，但**鉴权与 NetworkPolicy
   隔离仍是未决的安全决策**，与既有的 web-terminal 越权风险同源。

### 有意的行为变更（需知悉）

- 生命周期闸门在 token 解析处生效，被停用的用户现在对**所有**控制面接口 403（含 `/api/auth/me`），
  不再只是运行时发现被拦。
- 降级/清除一个**已处于 disabled 状态**的管理员，即便它是唯一的 admin 行也会被放行
  （旧的状态盲计数会拦）。UI 上不可达，因为停用本身会拒绝最后一个活跃管理员。
- UI 的 PURGING 轮询上限 60s 与后端 60s 收割周期同量级，时序不巧时行会残留到手动刷新。
