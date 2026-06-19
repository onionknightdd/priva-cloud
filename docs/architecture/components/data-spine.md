---
Status: Draft · Date: 2026-06-17 · Branch: multi-tenant-platform
Parent: ../multi-tenant-platform.md · Component: Data Spine (SQLite-service + Redis)
Modifications from blueprint: M1 = SQLite+Redis (not Postgres); M2 = no CLI session-id remap
---

# Priva Data Spine — Component Specification

**Scope:** the central, authoritative state layer for the multi-tenant fork (branch `multi-tenant-platform`), built under two binding modifications to `docs/architecture/multi-tenant-platform.md`: **M1** (SQLite + Redis instead of Postgres, with SQLite fronted by exactly one owning data-plane service) and **M2** (the platform-minted `session_uuid` *is* the CLI `--session-id`, immutable, with no remap and no separate `cli_session_id` column). This document is the executable contract for §1 the data-plane service & its thin client, §2 the full SQLite DDL, §3 the no-remap session lifecycle, §4 the two-tier Redis key catalog, §5 migration off today's fcntl-locked files, §6 the resolved-risk register, and §7 the open questions that must be answered before cutover. Every load-bearing SDK claim was verified against the installed `claude-agent-sdk==0.2.93` (cited inline); facts that cannot be settled by static read (the bare-`--resume`-of-missing-file behavior) are surfaced as a hard pre-lock open question, in keeping with M2's "verified, not assumed" mandate.

---

## Revision note — 2026-06-17 (post-review corrections)

Two binding corrections from the user, superseding the drilled draft wherever they conflict:

- **C1 — Audit log is NOT in SQLite.** It stays as JSONL files (as in single-machine Priva), but **per-user on a dedicated audit volume** (`/audit/<account_id>/YYYY-MM-DD.jsonl`), separate from the session PVC so it honors decision 24's "audit retained separately" (the session PVC purges at 30 d; audit lives on its own volume + retention). Each pod is the **sole writer** of its own audit subdir (same single-writer-by-construction property as the session JSONL — no contention), and a read-only **`audit-reader`** service serves the control panel's cross-tenant queries by scanning that tree. Consequence: cross-tenant audit is a **file scan, not a SQL query** (fine for rare admin use). The `audit_log` / `chain_checkpoint` tables (§2.5), the `AppendAudit` / `QueryAudit` RPCs (§1.7), and the audit→SQLite migration row (§5) are **withdrawn**. Combined with C3, **no table is on `synchronous=FULL`** (SQLite holds zero money/audit data).
- **C2 — No object storage (OSS).** Litestream→object-store (original §1.5) is **withdrawn**. DR is, in order: (1) the RWO block PVC + scheduled CSI `VolumeSnapshot`s; (2) optional Litestream with a **`file` replica** to a backup volume, or a periodic `VACUUM INTO` to a backup PVC; (3) **rqlite / LiteFS** as the no-OSS write-HA upgrade. No object store anywhere.
- **C3 — `budget_ledger` is NOT in SQLite.** *(Spend itself is now **deferred — superseded by M6 below**: there is no metering proxy and no `spend:reserve`; the rest of this bullet is the pre-M6 record, kept for history.)* Spend accounting was to live entirely in the **LLM metering proxy** (decision 11): the proxy event log as durable spend system-of-record, the **Redis `spend:reserve`** counter as the live race-closer, orphaned reservations reconciling **against the proxy log** — no SQLite ledger. The `budget_ledger` table (§2.8) and its FULL-sync requirement are **withdrawn**. The `quota` table stays (caps/tiers/idle-grace are *config*, not money). **Combined effect of C1 + C3 (+ M6):** the central SQLite holds **zero crash-sensitive money/audit data** — only config/metadata/index that is re-derivable or re-enterable — so **no table needs `synchronous=FULL`** and the no-OSS DR story (C2) is comfortable with PVC + periodic snapshot. (`usage_rollup` also leaves the DB — locked residual default #1, §2.6: under M6 usage is **pod-self-reported**, never cached in SQLite.)
- **C4 — Session fork is SUPPORTED (reverses the earlier "forbidden in MVP" stance).** `fork_session`/`--fork-session` (already wired: `routers/agent.py:546-569` → `sdk_fork_session`) creates a CHILD session with a new immutable `session_uuid`; the parent's id never changes, so **fork is not a remap** and M2's zero-remap invariant is intact. A fork is a first-class create — capture the SDK-minted child id and lowercase it; the child is a new JSONL with a `forkedFrom` header. **(Superseded by M5, below: there is no session table, so lineage rides the child JSONL's `forkedFrom` header / an optional `channel_binding` column — not a `session_index` row or a `RecordForkLineage` session-table RPC.)** The §3.7 consistency assertion **exempts** a sanctioned fork (it targets mid-run mutation of the *same* session's id). File checkpointing/rewind is an Agent-Runner-local concern (checkpoints are local-disk-only on the per-user PVC) and does not touch the data-spine schema — see the Agent Runner spec.
- **Locked residual defaults (2026-06-17).** #1 `usage_rollup` dropped (§2.6) — usage read from the proxy API. #2 hook execution logs stay **per-user PV** (§7-Q4) — no `hook_log` table/RPC. #3 the **wake-free read path** is made explicit (§3.9). #4 empirical M2 verification **deferred to Phase 0** with the §3.7 per-run assertion + a CI version-pin on `claude-agent-sdk` as backstop (§7-Q3).
- **M5 — no `session_index` table; the filesystem is the session store (2026-06-18; supersedes the session-table model below wherever they conflict).** Decided during the Agent-Gateway drill (`components/agent-gateway.md` §3/§13). The JSONL on the per-user volume is the session of record (decision 15); the **`session_index` table (§2.3), the `cwd_hash`/`config_home` persistence (§2.7), `MintSession`'s row-commit + the `ClaimFirstRun`/status-CAS, and the SDK-sanitizer-avoidance apparatus are all withdrawn.** Replacements: `is_first_run` authority = a **`first_run_done` boolean (CAS) on `channel_binding`** (IM) and **gateway-mint-on-empty + a disk-existence guard** (WebUI, backstopped by agent-runner §2.5 reconcile-as-create); **path derivation is in-pod** (the SDK — one pod = one account, so `os.environ` is correct, agent-runner §1.3), and the reader plane globs `projects/*/<uuid>.jsonl`; **listing** = SDK when the pod is awake, wake-free per-account glob (state-reader) when asleep, admin cross-user = state-reader whole-tree scan. Fork lineage (`fork_parent_uuid`/`RecordForkLineage`) moves off `session_index` (a `channel_binding`/lineage column or dropped — TBD in the body rewrite). The remap fix is the **CREATE/RESUME split** (agent-runner §2.3), which is **table-independent**, so dropping the index does **not** reintroduce remap. Minimal central schema = `account` + `identity_link` + `channel_binding`(+`first_run_done`). Dropping the index is **reversible** (it was never the source of truth). **Body rewritten to M5 (2026-06-18):** §1.1, §1.6, §1.7 (Sessions/Bindings RPCs), §2.3 (table withdrawn), §2.4 (`first_run_done` + FK dropped), §2.12, §2.14, §3 (lifecycle), §5 (migration), §6 (R1/R3/R7), §7-Q5. Remaining `session_index` mentions are historical ("withdrawn"/"was") only.
- **M6 — BYOK + metering deferred (2026-06-18; supersedes C3; the spend-machinery bodies §2.6/§2.8/§4 #5 are rewritten M6-correct — cleanup DONE).** Users bring their **own LLM keys** (BYOK); **spend tracking/enforcement is deferred** (token-count-only, **pod-self-reported**). Withdrawn "for the moment": the metering-proxy ledger as spend system-of-record, the **Redis `spend:reserve`** counter (§4 #5), reserve-before / `402`, and any `$`. **`quota.monthly_spend_cap_usd` (§2.7) stays as config but is not enforced**; `usage_rollup` was already dropped (now usage is **pod-self-reported**, not "from the proxy API"). The BYOK key is stored in the **`secret`/`account_dek` envelope-encryption tables (§2.9)** like any user secret (the operator unwraps + injects — operator §6). The **egress security gateway is deferred** (agent-runner §13-2). Reversible. *(Also fixed here: `quota.idle_grace_seconds` default corrected **180 → 1800** to match the locked 30-min idle — agent-runner §13-1 / operator §1.3.)*

---

## 1. SQLite Data-Plane Service (`priva-dataplane`) & Client

### 1.1 The non-negotiable M1 boundary

Today every store opens a YAML/JSONL file directly under `fcntl.flock` (`user_store.py`, `audit_log.py`, `temp_files.py`, `user_env.py`, `scheduler/job_store.py`, `scheduler/run_history.py`, `mcp/config_manager.py`, `channels/config_store.py`, **`hooks/config_manager.py`, `hooks/log_store.py`, `pty_session.py`**, and the `_pagination.py` cursor helper). That is safe only because exactly one uvicorn process on one host writes them. The fork breaks that: gateway, scheduler, operator, panel, and **N per-user pods** all need the same central facts.

SQLite is a *library*, not a server. If many processes `sqlite3.connect()` the same file over the shared RWX FS, we recreate the original disease in a worse form — POSIX advisory locks (`fcntl`) over NFS/CephFS are unreliable, SQLite's WAL is explicitly unsupported for multiple writers across network filesystems, and concurrent writers silently corrupt. The blueprint concedes this for the per-session JSONL only because one pod is the sole writer of *its own* JSONL (`multi-tenant-platform.md:196-197`); the **central DB has no single-writer-by-construction property** — many tenants write `channel_binding` and `job_run_record` at once (there is no `session_index` — M5).

> **Hard rule (M1).** The SQLite file is opened by exactly **one process** — `priva-dataplane`. It lives on that pod's **local/block volume (RWO PVC), never the shared RWX FS**. Every other component speaks a typed RPC; the client library physically has no FS handle to the file. In-process write serialization replaces distributed locking entirely.

### 1.2 Deployment shape

- **One writer, period.** Kubernetes `Deployment`, `replicas: 1`, `strategy: Recreate` (never `RollingUpdate` — two pods must never co-mount the file; `Recreate` makes K8s detach the old volume before the new pod starts, structurally preventing split-brain). `RWO` PVC on local-path/block storage.
- **Internal only.** `ClusterIP` + NetworkPolicy admitting ingress solely from gateway / scheduler / operator / panel / pod service accounts. mTLS on every call.
- **Small.** A Python (FastAPI for the HTTP/JSON panel facade; grpclib for the typed gRPC surface) process whose only job is owning the connections, serializing writes, running transactions, returning rows. The only "logic" it carries is the transaction-level invariants that *must* be enforced at the single writer: audit hash-chain continuity, monotonic secret `generation`, budget-ledger atomicity, exactly-once job-fire claims.

```
  gateway ─┐                 priva-dataplane  (replicas:1, Recreate)
  scheduler┤  gRPC/mTLS   ┌───────────────────────────────────────┐
  operator ┼───────────►  │ async RPC handlers                    │
  panel    ┤              │   ├─ write queue → 1 writer conn (WAL) │
  pod ×N  ─┘              │   └─ read-only conn pool (mode=ro)     │
                          │ SQLite file on RWO local volume        │
                          └───────────────┬───────────────────────┘
                                          │ WAL frames (continuous)
                                          ▼  object store (S3/MinIO/OSS)
```

### 1.3 Write serialization & transactions

- **One writer connection, one asyncio task, one in-process queue.** Every write RPC enqueues `(fn, args, future)`; the writer drains serially and resolves futures. This is the in-process equivalent of the old `fcntl.LOCK_EX` — but with zero FS locking and zero NFS dependence. Because only one writer ever asks, we never hit `SQLITE_BUSY` on the write path; `busy_timeout` is a backstop, not the mechanism.
- **Reads use a separate `mode=ro` pool.** In WAL, readers see the last committed snapshot and never block the writer (and vice-versa).
- **One RPC = one transaction**, wrapped `BEGIN IMMEDIATE … COMMIT`. Compound invariants (e.g. secret-rotation re-wrap; the IM `first_run_done` 0→1 CAS; a `ClaimJobFire` insert) run inside a single transaction so they cannot interleave — a *direct benefit* of single-writer ordering. *(Audit append is no longer one of these — it's JSONL on the audit volume, C1; budget reserve+settle is gone — C3 / M6.)*
- **Idempotency for at-least-once callers.** `ClaimJobFire`, `AppendAudit`-with-key, and any mutating RPC accept an optional `idempotency_key` deduped via a small TTL-pruned `idempotency` table; durable claims also use `INSERT … ON CONFLICT DO NOTHING` on a natural UNIQUE.

### 1.4 PRAGMAs and the per-table durability contract

```sql
PRAGMA journal_mode = WAL;        -- readers ∥ writer
PRAGMA synchronous = NORMAL;      -- default: fsync at checkpoint, not every commit
PRAGMA busy_timeout = 5000;       -- backstop only; the write queue is the real serializer
PRAGMA foreign_keys = ON;         -- OFF by default in SQLite; MUST be set per connection
PRAGMA wal_autocheckpoint = 1000; -- bound WAL growth; plus a timer-driven PASSIVE checkpoint
PRAGMA temp_store = MEMORY;
PRAGMA mmap_size = 268435456;     -- 256MB read mmap
PRAGMA foreign_keys;              -- assert == 1; CI guard against forgetting it
```

`synchronous=NORMAL` for **every** table. After **C1** (audit out) and **C3** (`budget_ledger` out), **no table holds crash-sensitive money or audit data**, so none needs `synchronous=FULL`. The central SQLite now carries only config / metadata / index (accounts, identities, bindings, quotas, wrapped secrets, retention, scheduler, MCP/hook config, temp index — **no `session_index`, M5**) — facts that are re-derivable or re-enterable, so losing the last sub-second of writes on a hard crash is tolerable. This is exactly what makes the no-OSS DR story (§1.5) comfortable: PVC + periodic snapshot suffices; nothing in the DB demands sub-second durability.

### 1.5 HA / DR

> **No object storage (C2).** The original Litestream→object-store plan is withdrawn. The ladder below uses only block PVCs, a backup volume, and (optionally) node-to-node replication.

| Layer | Mechanism | Contract |
|---|---|---|
| **Primary durability (day one)** | The **RWO block PVC itself** + scheduled CSI `VolumeSnapshot`s | The PVC survives pod reschedule (only the pod restarts; `Recreate` detaches before re-attach). Periodic `VolumeSnapshot`s give point-in-time recovery. **No object store involved.** |
| **Continuous backup (no-OSS, optional)** | **Litestream with a `file` replica** to a backup volume (separate PVC / NFS path), or a timer-driven `VACUUM INTO` to that volume | RPO ≈ seconds (Litestream file replica) or ≈ the backup interval (`VACUUM INTO`). On reschedule, an init-container restores from the file replica into the RWO PVC **before** the writer opens; `Readyz` gates traffic until clean. Litestream's `file` replica target needs no object storage. |
| **Write-blackout window** | single writer + `Recreate` | Central writes pause for PVC detach→attach + WAL recovery (~seconds). **Agent runs keep going** — they touch the DB only on cache-miss; the hot paths are Redis. Nothing money- or audit-critical lives in the DB (C1/C3), so the gap risks only delayed metadata reads/writes — never spend or audit integrity. |
| **Write-HA upgrade (still no OSS)** | **rqlite** (Raft, 3-node quorum) or **LiteFS** (FUSE primary + RO followers) | Adopt only if the ~seconds write-blackout becomes an SLA breach. Both replicate **node-to-node with no object store**; followers/quorum keep the single-writer (single-leader) invariant intact. The thin-client absorbs the swap — only the storage adapter changes, callers don't. |

**Read scaling is staged, smallest-first:** Stage A = single process, one writer conn + small `mode=ro` pool (WAL gives reader/writer concurrency free; p99 read < 1 ms at this scale). Stage B = widen the RO pool. Stage C = LiteFS followers. We **start at A**; B/C are documented levers, not day-one infra. What keeps A viable: the per-message hot paths (routing, awake, lock-status) are **Redis**, and transcript bytes are **never in SQLite** — the DB carries facts-queried-across-users at human/agent cadence, read on cache-miss. *(Spend enforcement was a fourth Redis hot path pre-M6; it is deferred — §2.8.)*

### 1.6 The thin client

Transport: **gRPC** (proto-typed, streaming list endpoints) + an HTTP/JSON facade for the panel. The caller's identity (`account_id`, role, calling-service) rides in a signed context header, enforced server-side — **the client never passes raw SQL; it calls domain methods.** The client (`priva.dataplane.client`) is a drop-in for today's store classes (`UserStore`, `JobStore`, `RunHistory`, `TempFileIndex`, channel config/session stores, MCP config, **Hooks config + log**; **not** `AuditLogger` — audit stays file-based on the audit volume, C1): each becomes a stub calling the matching RPC, method signatures preserved so callers don't change. **Every `fcntl`/`yaml`/`open()` line in the §1.1 file list is deleted.**

> **CI guard (resolving critique blocker #1).** Data-plane and pod-parent code is **banned from importing `claude_agent_sdk._internal.sessions`**. Those helpers (`get_session_info`, `get_session_messages`, `delete_session`, `fork_session`) resolve paths through `_get_project_dir`/`_find_project_dir`, which read `CLAUDE_CONFIG_DIR` from `os.environ` **only** — they do **not** honor `options.env`/`env_override` (verified: `sessions.py:144` calls `_get_projects_dir()` with no arg; only `_get_projects_dir(env_override)` at `sessions.py:130` honors it). In a multi-tenant parent process there is no single correct `CLAUDE_CONFIG_DIR`, so any read/delete/reconcile via these helpers looks in the wrong directory. **Under M5 the platform persists no path metadata** (no `cwd_hash`/`config_home`): the **pod** derives its own JSONL path **in-process** via the SDK (one pod = one account, so `os.environ["CLAUDE_CONFIG_DIR"]` *is* correct — agent-runner §1.3 carves this single-account exception), and the read-only **`state-reader`** locates a file by **globbing** `projects/*/<session_uuid>.jsonl` under the account subtree. The CI ban still holds (the data-plane / gateway never resolve SDK paths); it is simply satisfied by in-pod derivation + glob rather than a persisted `cwd_hash`.

### 1.7 RPC surface (by domain → caller)

- **Accounts** (← `user_store.py`): `GetAccount`, `ListAccounts`, `Create/Update/DeleteAccount`, `VerifyPassword`, `FindByApiKey`, `CountAdmins` (last-admin guard), `Get/UpdateRuntimeConfig`. `password_hash`/`api_key` never returned in list form unless caller role=admin.
- **Sessions (M5 — no session table):** there is **no `session_index`, no `MintSession` row-create, no `TouchSession`, and no `GetSession`/`ListSessionsByAccount` DB query.** WebUI session ids are minted **gateway-local** (`uuid4().lower()`); IM session ids are minted by **`BindChannel`** (a `channel_binding` write). Session **listing/reads** are served from the filesystem by the read-only **`state-reader`** (glob `projects/*/<session_uuid>.jsonl`; titles from JSONL headers or an optional sidecar), not the data-plane. `is_first_run` is a **`first_run_done` CAS on `channel_binding`** (IM) / gateway-mint + disk-guard (WebUI) — not a status RPC. Fork lineage, if retained, is a `channel_binding`/lineage column, not a `RecordForkLineage` session-table write. **No `RemapSession`** (still — M2).
- **Identities & Bindings**: `ResolveIdentity(surface_type, surface_user_id) → account_id` (cached in Redis, DB on miss), `Link/Unlink/ListIdentities`, **`BindChannel`** (mints a fresh `session_uuid` + `first_run_done=0` — the IM session mint point), **`RebindChannel`** (new `session_uuid`, reset `first_run_done=0` — the `/reset` path), **`ClaimFirstRunIM(binding_id) → {is_first_run}`** (the atomic `first_run_done` 0→1 CAS), `GetBinding`, `ListBindings`.
- **ChannelConfig** (← `channels/config_store.py`): WeCom/OpenClaw config get/put (secrets via Secrets envelope, masked on read), `Get/PutWeComSession`.
- **Scheduler / runs** (← `scheduler/*`): job CRUD + `SetJobStatus`; `ClaimJobFire(job_id, fire_epoch) → bool` (durable exactly-once via `UNIQUE(job_id, fire_epoch)`); `ListRuns`. **Run-record ownership is split (scheduler.md §3.3): the scheduler writes `StartRun(running | skipped)` at dispatch; the *pod* writes `FinishRun(outcome)` on completion** (the pod has the execution facts + the M6 self-reported usage; it is already a data-plane client, §1.4). `ListActiveJobs` feeds each scheduler replica's clock.
- **Audit** (← `audit_log.py`): **no data-plane RPCs (C1).** Audit is per-user JSONL on the audit volume (`/audit/<account_id>/…`), appended by the owning pod and read cross-tenant by the read-only `audit-reader` service. The data-plane neither stores nor proxies audit.
- **Secrets** (← `crypto.py`): see §6 trust-boundary resolution — the data-plane returns **wrapped DEK + ciphertext**; the **pod** calls KMS `UnwrapDEK` scoped to its own account. RPCs: `PutSecret`, `GetWrappedSecret`, `ListSecrets`, `RotateAccountDEK`.
- **TempFiles** (← `temp_files.py`): `IndexTempFile`, `SoftDeleteTempFile`, `ListTempFiles`, `ExpireTempFiles(before)`.
- **Mcp** (← `mcp/config_manager.py`): `GetMcpServers(scope)`, `Put/DeleteMcpServer`.
- **Hooks** (← `hooks/config_manager.py`, `hooks/log_store.py`) *(added per critique blocker #2)*: `Get/PutHookConfig(scope)` only. **Hook execution logs are per-user PV bytes (residual default #2, locked)** — no `AppendHookLog`/`QueryHookLog` RPC, no `hook_log` table; cross-tenant log reads go through the `state-reader` (§3.9). Only hook *config* is central.
- **Retention** (new, offboarding FSM): `GetRetentionState`, `SetOffboardingStatus`, `SchedulePurge`.
- **Admin/health**: `Healthz`, `Readyz` (DB reachable, WAL writable, last-backup age < threshold, migration manifest = verified), `Stats` (queue depth, checkpoint lag).

---

## 2. Full SQLite Schema (DDL)

**Conventions.** Entity PKs are `TEXT` (platform-minted UUID/hash). The one remaining high-churn append-only log in SQLite — `job_run_record` (scheduler history; `audit_log`→PVC per C1, `budget_ledger`/`usage_rollup` withdrawn per C3/residual-default-#1) — uses `INTEGER PRIMARY KEY` (rowid alias — fastest monotonic append, free clustering) plus a `TEXT` business key under a UNIQUE index. **All timestamps are `TEXT` ISO-8601 UTC** (`YYYY-MM-DDTHH:MM:SS.sssZ`) — lexicographic == chronological, cross-pod meaningful; **never `time.monotonic()` floats** (pod-local, meaningless across hosts). All tables `STRICT`. Booleans are `INTEGER CHECK (col IN (0,1))`. **DDL order is irrelevant for FK *definition*** — SQLite resolves FK target tables lazily at write time, not at `CREATE TABLE` time (resolving critique major: the "`foreign_keys=OFF` in a transaction" workaround was both impossible — the PRAGMA is a no-op inside a transaction — and unnecessary). Just create all tables, then keep `foreign_keys=ON` on the writer connection.

### 2.1 account

```sql
CREATE TABLE account (
    account_id    TEXT PRIMARY KEY,                          -- platform UUID; stable FK target for everything
    username      TEXT NOT NULL,                             -- login handle (was the YAML map key)
    password_hash TEXT NOT NULL,                             -- bcrypt
    role          TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user','admin')),
    status        TEXT NOT NULL DEFAULT 'active'
                       CHECK (status IN ('active','disabled','offboarding','purged')), -- mirrors retention FSM; auth fails-closed on one read
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;
CREATE UNIQUE INDEX ux_account_username ON account(username);

CREATE TABLE account_runtime_config (
    account_id TEXT NOT NULL REFERENCES account(account_id) ON DELETE CASCADE,
    cfg_key    TEXT NOT NULL,   -- cli_path, append_systemprompt, history_retention_days, retryable_tools, risky_tool_list, pii_masking, skill_exclude
    cfg_value  TEXT NOT NULL,   -- JSON (json1)
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (account_id, cfg_key)
) STRICT;
```
*Note (resolving critique minor on lazy migrations):* the two existing read-time migrations (`sensitive_data_patterns → pii_masking`, `enable_global_skills → skill_exclude`) are **baked fully into the one-time migrator (§5) and the schema is then frozen** — no runtime lazy migration over opaque JSON blobs post-cutover.

### 2.2 identity_link

```sql
CREATE TABLE identity_link (
    identity_id     TEXT PRIMARY KEY,
    account_id      TEXT NOT NULL REFERENCES account(account_id) ON DELETE CASCADE,
    surface_type    TEXT NOT NULL CHECK (surface_type IN ('wecom','feishu','discord','openclaw','browser')),
    surface_user_id TEXT NOT NULL,
    display_name    TEXT,
    verified        INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0,1)),
    linked_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;
CREATE UNIQUE INDEX ux_identity_surface ON identity_link(surface_type, surface_user_id); -- one (surface,user) → one identity → one account (inbound auth hot path)
CREATE INDEX        ix_identity_account ON identity_link(account_id);
```

### 2.3 session_index — WITHDRAWN (M5, 2026-06-18)

**There is no `session_index` table (M5).** The JSONL on the per-user volume is the session of record (decision 15); a central index over it was only an optimization, and at this scale a filesystem read serves listing. Decided during the Agent-Gateway drill (`components/agent-gateway.md` §3/§13). What the table used to carry now lives as follows:

| Former `session_index` column | Under M5 |
|---|---|
| `session_uuid` (PK) | the JSONL **filename** on the per-user volume; minted **gateway-local** (`uuid4().lower()`, WebUI) or by **`BindChannel`** (IM); never stored in a central session row |
| `account_id` | implied by the per-account volume subtree (`/export/<account_id>/…`) the file lives under |
| `cwd` / `cwd_hash` / `config_home` | **not persisted** — the in-pod SDK derives the path (one pod = one account, `os.environ` correct, agent-runner §1.3); the `state-reader` globs `projects/*/<session_uuid>.jsonl` |
| `status` | **not persisted** — `is_first_run` is a `first_run_done` CAS on `channel_binding` (IM) / gateway-mint + disk-existence guard (WebUI, §3.4); run liveness is the Redis `route` / `lock:session` mirror (§4) |
| `title` / `last_activity` | read from the JSONL header / mtime (or an optional pod-written `sessions.index.jsonl` sidecar) by the `state-reader` |
| `fork_parent_uuid` | the child JSONL's `forkedFrom` header (agent-runner §3.1), or a `channel_binding`/lineage column — **not** a central session row |

The **lowercase invariant** survives without a DB `CHECK` (there is no row to constrain): mint lowercases `uuid4()`; the pod normalizes before `--session-id`/`--resume`; the §3.7 consistency assertion compares lowercase byte-for-byte. The `trg_session_uuid_immutable` trigger is moot (no row to update); immutability is intrinsic — the uuid *is* the filename and is never rewritten. **No `cli_session_id` column** (still — M2). Dropping the index is **reversible** (it was never the source of truth): a `sessions.index.jsonl` sidecar or a rebuilt table can be added later with zero migration.

### 2.4 channel_binding (decisions 17/18 — **one read-write binding per session, N read-only**)

```sql
CREATE TABLE channel_binding (
    binding_id   TEXT PRIMARY KEY,
    identity_id  TEXT NOT NULL REFERENCES identity_link(identity_id) ON DELETE CASCADE,
    session_uuid TEXT NOT NULL,                              -- bound session's uuid (M5: bare column, NO session_index FK — the JSONL is the store)
    first_run_done INTEGER NOT NULL DEFAULT 0 CHECK (first_run_done IN (0,1)), -- M5: IM is_first_run authority — atomic CAS 0→1 == the CREATE turn (§3.4)
    channel_id   TEXT,                                       -- reply-addressing only (from the inbound frame); NOT a resolution key (no-chat_id-key, 2026-06-18)
    bot_id       TEXT,
    channel_type TEXT NOT NULL CHECK (channel_type IN ('wecom','feishu','discord','openclaw')),
    mode         TEXT NOT NULL DEFAULT 'readwrite' CHECK (mode IN ('readwrite','readonly')), -- decision 18: 2nd surface attaches read-only
    bound_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    rebound_at   TEXT
) STRICT;
CREATE UNIQUE INDEX ux_binding_identity      ON channel_binding(identity_id);                       -- decision 17: a channel/identity points at exactly ONE session; rebind = UPDATE this row
CREATE UNIQUE INDEX ux_binding_session_rw    ON channel_binding(session_uuid) WHERE mode='readwrite'; -- exactly ONE read-write binding per session; read-only attachers unconstrained
CREATE INDEX        ix_binding_session       ON channel_binding(session_uuid);
-- (no channel→session index: no-chat_id-key, 2026-06-18 — bindings resolve by identity only; channel_id is reply-addressing metadata)
```
*Resolving critique major on binding uniqueness.* The load-bearing rule (decisions 17+18) is **exactly one `readwrite` binding per session, plus optional `readonly` attachers**. That is the partial unique index `ux_binding_session_rw … WHERE mode='readwrite'` — **not** Draft 4's `WHERE rebound_at IS NULL`, which is semantically wrong (`rebound_at` means "rebound at least once," not "active," so an ever-rebound session would be excluded from the check). `ux_binding_identity` keeps "one channel → one session." Rebind = UPDATE the row's `session_uuid` + **reset `first_run_done=0`** + stamp `rebound_at`. **M5:** `session_uuid` is a **bare column** (no `session_index` FK — the JSONL is the store, so there is no row to dangle against); **`first_run_done` is the IM `is_first_run` authority** — the first message flips it `0→1` via an atomic single-writer CAS (= the CREATE turn), every later message reads `1` (= RESUME). See §3.4. This table (plus `account` + `identity_link`) is the **entire** central session-mapping schema under M5. **Resolution is by `identity_id` only (no `chat_id` key, 2026-06-18):** for **any group** message the bot validates the **sender** (the access modes, §10.2-gateway), then routes via the sender's identity binding to the sender's own session/account — there is no chat_id-keyed shared group session. `wecom_session` (the old chat_id-keyed map) is therefore withdrawn (§2.12).

### 2.5 audit_log / chain_checkpoint — WITHDRAWN from SQLite (C1)

**Withdrawn per C1 (revision note).** Audit is **not** a SQLite table. It stays JSONL, per-user, on a dedicated audit volume `/audit/<account_id>/YYYY-MM-DD.jsonl` (separate from the session PVC so decision 24's "retained separately ≥1 y" holds while the session PVC purges at 30 d). Each pod is the **sole writer** of its own audit subdir (the same single-writer-by-construction property as the session JSONL — no contention, no lock); a read-only **`audit-reader`** service scans the tree for the control panel's cross-tenant queries (a file scan, not a SQL query — fine for rare admin use). The `audit_log` and `chain_checkpoint` tables, their `AppendAudit`/`QueryAudit` RPCs (§1.7), and the audit→SQLite migration row (§5) are all gone. The hash-chain integrity property is preserved **in the JSONL writer** (each line carries `prev_hash`‖`entry_hash`), not in a DB. See §3.8 for the audit-volume mount scoping and §3.9 for how the `audit-reader` is reached wake-free.

**Remaining SQLite archival note.** With audit, `budget_ledger`, and `usage_rollup` all out of SQLite, the **only** append-only log left in the DB is `job_run_record` (scheduler history). It is **append-only and never deleted from the live table** — a single `ts`-indexed table scales to tens of millions of rows. If physical archival is ever forced, it runs as **two separate committed transactions** (copy-into-archive commit, then delete-from-live commit), never relying on cross-attached-DB atomicity (SQLite gives no atomic transaction across two attached DB files in WAL mode). No hash-chain / `chain_checkpoint` is needed for scheduler history.

### 2.6 usage_rollup — WITHDRAWN from SQLite (locked: dropped)

**Locked (residual default #1; reframed by M6):** there is **no `usage_rollup` table.** The central SQLite stays purely config/metadata/index. Under **M6 (token-count-only) there is no metering proxy**: token **usage is pod-self-reported** — each pod posts its SDK `result` token counts to the data-plane after a run (observability-only, nothing enforced). The control panel reads those counts and computes `$ = tokens × price card` **for display**; **spend tracking/enforcement is deferred**. A denormalized SQLite rollup would only be a second, lagging copy, so it stays out; if panel charts ever need pre-aggregation, build it then as a read-model over the self-reported counts, never as a STRICT-discipline liability in the central write path. SQLite holds **zero token/cost rows**. *(Pre-M6 this read "straight from the LLM-metering-proxy API"; the proxy is dropped.)*

### 2.7 quota

```sql
CREATE TABLE quota (
    account_id              TEXT PRIMARY KEY REFERENCES account(account_id) ON DELETE CASCADE,
    tier                    TEXT    NOT NULL DEFAULT 'default',
    max_concurrent_sessions INTEGER NOT NULL DEFAULT 3,       -- effective cap = min(mem-derived, this); enforced in-pod
    monthly_spend_cap_usd   REAL    NOT NULL DEFAULT 150,     -- config only; NOT enforced (M6 — spend deferred); blueprint default
    spend_period_start      TEXT    NOT NULL,                 -- calendar-month UTC reset anchor (dormant under M6)
    idle_grace_seconds      INTEGER NOT NULL DEFAULT 1800,    -- maxIdle before scale-to-zero (locked 30-min; M6/operator §1.3 — was 180)
    updated_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;
```

### 2.8 budget_ledger — WITHDRAWN from SQLite (see C3)

The central SQLite keeps **no spend ledger** (C3). Under **M6, spend tracking is deferred entirely** ("token-count-only, for the moment"): there is **no metering proxy, no `spend:reserve`, no reserve-before-call, no `402 budget_exceeded`, and no settlement** anywhere — not in SQLite, and not in a proxy (there is none).

Current model (M6):
- **No enforcement.** `quota.monthly_spend_cap_usd` (§2.7) stays as **config**, but nothing reads it to gate a turn. The scheduler's fire-time gate is an **active-check, not a budget check** (scheduler §5.3).
- **Token counts only.** Each pod **self-reports** its per-run token usage (SDK `result`) to the data-plane (observability-only — agent-runner §7 SB8). `$` is computed for display as `tokens × price card`, never stored as a ledger row.
- **Nothing to reconcile.** With no reservation and no charge, the old pod-death overshoot gap (R9) **cannot occur** — it is dissolved, not mitigated.

`quota` (caps, tiers, idle_grace) stays in SQLite because it is **configuration**, not money movement; `usage_rollup` (§2.6) stays dropped.

*Reversible (the pre-M6 model, restorable): reservation (Redis `spend:reserve` #5) → enforce against the cap → durable record + settle. Re-enabling spend = restore that path; keeping the token counts flowing now makes it cheap.*

### 2.9 secret + account_dek (envelope encryption)

```sql
CREATE TABLE account_dek (
    account_id  TEXT    NOT NULL REFERENCES account(account_id) ON DELETE CASCADE,
    generation  INTEGER NOT NULL,                             -- bump on rotation; old gen kept to decrypt legacy ciphertext
    wrapped_dek BLOB    NOT NULL,                             -- DEK wrapped by KMS master KEK; plaintext DEK NEVER stored
    kek_id      TEXT    NOT NULL,                             -- which KMS master key wrapped it
    active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (account_id, generation)
) STRICT;
CREATE UNIQUE INDEX ux_dek_active ON account_dek(account_id) WHERE active = 1; -- one active DEK gen per account

CREATE TABLE secret (
    secret_id      TEXT    PRIMARY KEY,
    account_id     TEXT    NOT NULL REFERENCES account(account_id) ON DELETE CASCADE,
    secret_name    TEXT    NOT NULL,                          -- anthropic_api_key, wecom_bot_secret, openclaw_auth_token, openclaw_device_key, priva_api_key, ANTHROPIC_BASE_URL/AUTH_TOKEN/MODEL…
    secret_type    TEXT    NOT NULL CHECK (secret_type IN ('api_key','channel_token','device_key','credential','env_var')),
    ciphertext     BLOB    NOT NULL,                          -- value encrypted with the account DEK (gen below)
    dek_generation INTEGER NOT NULL,
    nonce          BLOB,                                      -- AEAD IV
    active         INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    rotated_at     TEXT,
    FOREIGN KEY (account_id, dek_generation) REFERENCES account_dek(account_id, generation)
) STRICT;
CREATE UNIQUE INDEX ux_secret_active ON secret(account_id, secret_name) WHERE active = 1; -- one active value per (account,name); rotation deactivates+inserts
CREATE INDEX        ix_secret_account ON secret(account_id);
```
*Replaces the single hardcoded Fernet key (`crypto.py:10`).* **Custody (resolving critique major).** The data-plane stores the per-account DEK **wrapped** by a KMS KEK and **never holds a plaintext DEK or plaintext secret**: `GetWrappedSecret` returns `{ciphertext, wrapped_dek, dek_generation, kek_id, nonce}`; the **pod calls KMS `UnwrapDEK` scoped to its own account** (KMS IAM/RBAC keyed on the pod's account context) and decrypts locally — preserving the blueprint's per-account blast radius. A compromise of the one data-plane process must **not** be able to decrypt every account's secrets. (Picking the concrete KMS backend is a §7 pre-migration blocker — §5's re-encrypt step cannot run without it.) `ANTHROPIC_*` env vars (plaintext today in `settings.local.json`) and channel tokens/PEM keys (plaintext today in `.priva.user.yml`/`.priva.openclaw.device.json`) all move into `secret` as `env_var`/`channel_token`/`device_key`.

### 2.10 retention_state (offboarding FSM)

```sql
CREATE TABLE retention_state (
    account_id           TEXT PRIMARY KEY REFERENCES account(account_id) ON DELETE CASCADE,
    offboarding_status   TEXT NOT NULL DEFAULT 'active'
                              CHECK (offboarding_status IN ('active','disabled','retained','purge_scheduled','purged')),
    disabled_at          TEXT,   -- immediate, synchronous cutoff
    retention_until      TEXT,   -- PV/sessions retained until (default +30d)
    audit_retained_until TEXT,   -- audit kept separately ≥1y (decision 24)
    purge_scheduled_at   TEXT,
    purged_at            TEXT,   -- terminal: PVC + secrets gone, audit survives
    updated_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;
CREATE INDEX ix_retention_purge_due ON retention_state(purge_scheduled_at) WHERE offboarding_status = 'purge_scheduled'; -- finalizer sweep
```

### 2.11 Scheduler (jobs, runs, fire-claims)

```sql
CREATE TABLE scheduled_job (
    job_id        TEXT PRIMARY KEY,
    account_id    TEXT NOT NULL REFERENCES account(account_id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    prompt        TEXT,
    job_type      TEXT NOT NULL CHECK (job_type IN ('scheduled_agent','http_call','user_script','tool_retry')),
    trigger_type  TEXT NOT NULL CHECK (trigger_type IN ('interval','cron')),
    trigger_config TEXT NOT NULL,                             -- JSON: Interval|Cron (json1)
    job_config    TEXT,                                       -- JSON: Agent|Http|Script|Retry config
    timezone      TEXT NOT NULL DEFAULT 'UTC',
    model         TEXT,
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused')),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;
CREATE INDEX ix_job_account ON scheduled_job(account_id);
CREATE INDEX ix_job_active  ON scheduled_job(status) WHERE status = 'active';

CREATE TABLE job_fire (                                       -- exactly-once fire claim (durable authority)
    job_id     TEXT NOT NULL REFERENCES scheduled_job(job_id) ON DELETE CASCADE,
    fire_epoch INTEGER NOT NULL,                              -- the scheduled fire instant
    claimed_by TEXT NOT NULL,                                 -- scheduler replica id
    claimed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (job_id, fire_epoch)                          -- INSERT success == this replica owns the fire
) STRICT;

CREATE TABLE job_run_record (
    run_id         TEXT PRIMARY KEY,
    job_id         TEXT REFERENCES scheduled_job(job_id) ON DELETE SET NULL, -- keep history even if job deleted
    job_name       TEXT NOT NULL,                             -- denormalized: survives job deletion
    account_id     TEXT NOT NULL REFERENCES account(account_id) ON DELETE CASCADE,
    session_uuid   TEXT,                                      -- M2: the run's session (was mutable session_id)
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    status         TEXT NOT NULL CHECK (status IN ('running','success','error','cancelled','skipped')),
    skip_reason    TEXT CHECK (skip_reason IN ('already_running','budget_exceeded') OR skip_reason IS NULL),
    duration_ms    INTEGER,
    is_error       INTEGER NOT NULL DEFAULT 0 CHECK (is_error IN (0,1)),
    error_message  TEXT,
    num_turns      INTEGER,
    total_cost_usd REAL,                                      -- reporting only
    result_summary TEXT
) STRICT;
CREATE INDEX ix_run_job_started     ON job_run_record(job_id, started_at DESC);
CREATE INDEX ix_run_account_started ON job_run_record(account_id, started_at DESC);
CREATE INDEX ix_run_status          ON job_run_record(status) WHERE status = 'running';
```
*`session_id → session_uuid` (M2). Polymorphic trigger/job configs stay JSON columns (always read with the parent; normalizing buys only JOINs). `job_fire` is the durable exactly-once authority; the Redis `job:{job_id}:fire:{fire_epoch}` lock is a fast pre-filter only (§4 #14).*

### 2.12 channel_config_wecom / channel_config_openclaw  (wecom_session WITHDRAWN — no-chat_id-key)

```sql
CREATE TABLE channel_config_wecom (
    account_id                  TEXT PRIMARY KEY REFERENCES account(account_id) ON DELETE CASCADE,
    enabled                     INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0,1)),
    bot_id                      TEXT,
    secret_id                   TEXT REFERENCES secret(secret_id),  -- bot secret in `secret`, not plaintext here
    ws_proxy_url                TEXT,
    allowed_user_ids            TEXT NOT NULL DEFAULT '[]',         -- JSON array
    single_chat_access_mode     TEXT CHECK (single_chat_access_mode IN ('all','allowed_user_ids','private')),
    welcome_message             TEXT,
    reject_message              TEXT,
    model                       TEXT,
    max_queue_size              INTEGER,
    idle_session_timeout_minutes INTEGER,
    enable_permission_feedback  INTEGER NOT NULL DEFAULT 0 CHECK (enable_permission_feedback IN (0,1)),
    feedback_timeout_seconds    INTEGER,
    updated_at                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

CREATE TABLE channel_config_openclaw (
    account_id      TEXT PRIMARY KEY REFERENCES account(account_id) ON DELETE CASCADE,
    enabled         INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0,1)),
    gateway_url     TEXT,
    auth_token_id   TEXT REFERENCES secret(secret_id),       -- token in `secret`
    default_agent   TEXT,
    max_turns       INTEGER,
    timeout_seconds INTEGER,
    agents          TEXT NOT NULL DEFAULT '[]',              -- JSON array of {id,description}
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

-- wecom_session: WITHDRAWN (no-chat_id-key, 2026-06-18) — folded into channel_binding (§2.4);
-- bindings resolve by identity, never chat_id, so a chat_id-keyed per-chat table is redundant.
```
*`wecom_session` withdrawn (no-chat_id-key, 2026-06-18).* The old per-chat session map is **folded into `channel_binding`** (§2.4): bindings resolve by **`identity_id` only** — a group message validates the **sender** then routes via the sender's identity binding to the sender's own session/account, so there is no chat_id-keyed table and no shared group session. `last_activity` for listing is now the JSONL mtime (read by the `state-reader`). `channel_config_wecom`/`_openclaw` above are **config** and stay.

### 2.13 temp_file / mcp_server_config / hook_config

```sql
CREATE TABLE temp_file (
    uuid         TEXT PRIMARY KEY,
    account_id   TEXT NOT NULL REFERENCES account(account_id) ON DELETE CASCADE,
    original_name TEXT NOT NULL,
    stored_name  TEXT NOT NULL,
    ext          TEXT,
    size         INTEGER NOT NULL,
    mime_type    TEXT,
    upload_date  TEXT,
    uploaded_at  TEXT NOT NULL,
    deleted      INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0,1)),
    deleted_at   TEXT
) STRICT;
CREATE INDEX ix_temp_account_uploaded ON temp_file(account_id, uploaded_at DESC);
CREATE INDEX ix_temp_expiry           ON temp_file(uploaded_at) WHERE deleted = 0;

CREATE TABLE mcp_server_config (
    config_id   TEXT PRIMARY KEY,
    account_id  TEXT NOT NULL REFERENCES account(account_id) ON DELETE CASCADE,
    server_name TEXT NOT NULL,
    scope       TEXT NOT NULL CHECK (scope IN ('project','global')),
    type        TEXT NOT NULL DEFAULT 'http',
    url         TEXT,
    headers     TEXT NOT NULL DEFAULT '{}',                  -- JSON TEXT (opaque header map)
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;
CREATE UNIQUE INDEX ux_mcp_account_scope_name ON mcp_server_config(account_id, scope, server_name);

CREATE TABLE hook_config (                                   -- added per critique blocker #2
    account_id TEXT NOT NULL REFERENCES account(account_id) ON DELETE CASCADE,
    scope      TEXT NOT NULL CHECK (scope IN ('project','global')),
    config     TEXT NOT NULL DEFAULT '{}',                   -- JSON TEXT
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (account_id, scope)
) STRICT;
-- NO hook_log table: hook execution logs are per-user PV bytes (residual default #2, locked, §7-Q4).
-- Only hook_config (above) is central; cross-tenant log reads go through the state-reader (§3.9).
```

### 2.14 Cross-cutting schema decisions

| Choice | Why |
|---|---|
| `foreign_keys=ON` per connection + CI guard | SQLite ignores FKs by default; the single writer must set it on every pooled connection or every FK is decorative. |
| `STRICT` tables | Reject type-confused writes; the single writer must not corrupt the shared store. |
| TEXT UUID PKs for entities, INTEGER rowid for append logs | Entities need stable cross-system keys; logs need fast monotonic append + free clustering. |
| json1 columns for polymorphic/schemaless blobs | `account_runtime_config`, trigger/job configs, `audit.details`, MCP `headers`, `hook_config` — heterogeneous, always read with parent; normalizing only adds JOINs. |
| Append-only-first; **no cross-attached-DB transaction** | `job_run_record` (the only append-only log left in SQLite — audit→PVC per C1, `budget_ledger`/`usage_rollup` withdrawn) never deletes from live; if archival is forced it's two committed txns. SQLite WAL gives no cross-file atomicity. |
| audit/ledger/rollup `actor`/`account_id` NOT FK | Decision 24 retains these after account purge; an FK would block purge or cascade-delete evidence. |
| `synchronous=NORMAL` on every table (no `FULL`) | The would-be `FULL` cases — audit tamper-chain + spend settlement — are both **out of SQLite** (audit→PVC JSONL C1; `budget_ledger` withdrawn C3; spend deferred M6). Nothing money/audit-critical remains, so no commit needs the last-fsync guarantee (§1). |
| **No session table at all (M5)** | The JSONL is the session store (decision 15); no `session_index`, no `cli_session_id`. `is_first_run` = `channel_binding.first_run_done` CAS (IM) / gateway-mint + disk-guard (WebUI); paths derived **in-pod** by the SDK and located by the `state-reader` via glob. Central session-mapping schema = `account` + `identity_link` + `channel_binding`. |
| DDL order irrelevant for FK definition | SQLite resolves FK target tables lazily at write time; the `foreign_keys=OFF`-in-transaction "workaround" is a no-op and unneeded. |

---

## 3. No-Remap Session Lifecycle (M2)

### 3.1 Verdict

**M2 is achievable with zero remap.** Verified against `claude-agent-sdk==0.2.93`: the platform-minted `session_uuid` can *be* the CLI's session id, immutably, for the session's entire life — provided we (a) treat a fork as a first-class CHILD creation — a new immutable `session_uuid`, never a mutation of the parent's id (see C4) — and (b) obey the CLI's rule that `--session-id` and `--resume` are mutually exclusive unless `--fork-session` is set (`types.py:1646-1650`). The single root cause of today's remap is that current code sets `options.resume = session_id` for **both** create and resume (`options.py:260-261`) — on first run that `--resume`s a non-existent id, the CLI mints a fresh id, and the code captures-and-remaps it (`service.py:1024`). We split create from resume; the remap disappears.

### 3.2 `session_uuid` — mint point, format, immutability

| Property | Decision |
|---|---|
| **Format** | RFC-4122 UUIDv4, **lowercase**, canonical hyphenated. The CLI's `--session-id` requires a valid UUID (`types.py:1649`); the SDK fork path also `_validate_uuid()`s. Lowercase is a **hard enforced invariant**, not cosmetic — see §2.3 (regex is IGNORECASE, validate returns verbatim, FS is case-sensitive). |
| **Generator** | `str(uuid.uuid4()).lower()`. **Server-side only.** A client *can* pass `session_id` today (`models/agent.py:24,209`); under M2 that input is **dropped** — the platform owns minting. |
| **Mint point (M5)** | **WebUI:** gateway-local (`uuid4().lower()`) at create-request accept time — **no DB row**. **IM:** `BindChannel` writes the `channel_binding` row (the durable mint, `first_run_done=0`). Either way the uuid is valid the instant it is minted (Redis keys root on it + `account_id`), and **before any pod is woken**; if the pod dies before first write, no JSONL exists yet and the client retries as a fresh create. |
| **Immutability (M5)** | Intrinsic: the uuid *is* the JSONL filename and is never rewritten — no row, no remap. An IM binding's `session_uuid` changes only on an explicit `/reset` (`RebindChannel` → a *new* session, not a mutation of the old one). **No `cli_session_id`, no `session_index`.** |

### 3.3 Path derivation — in-pod (SDK), never from central state (M5)

```
<config_home>/projects/<cwd_hash>/<session_uuid>.jsonl
```
- `config_home` = the per-account `CLAUDE_CONFIG_DIR`, operator-injected at spawn (a fixed per-account path, e.g. `/pv/<account_id>/claude`) — **not** stored in any session row.
- `cwd_hash` = `_sanitize_path(cwd)`, computed **by the in-pod SDK at write time** (one pod = one account, so it is correct) — **not** persisted centrally (M5).
- `<session_uuid>` is used **verbatim** as the filename (no transform).

**M5 — who resolves the path.** The **pod** writes/reads via the in-pod SDK: `options.env["CLAUDE_CONFIG_DIR"]` is injected (`subprocess_cli.py:434`) and, because one pod serves one account, even the SDK's `os.environ`-only in-process helpers (`_get_project_dir`/`_find_project_dir`, `sessions.py:144`) resolve correctly (agent-runner §1.3 carves this single-account exception to the §1.6 ban). The **data-plane and gateway never resolve a path** — there is no central row to resolve from. The read-only **`state-reader`** serves wake-free reads by **globbing** `projects/*/<session_uuid>.jsonl` under the account subtree (the uuid is unique within an account, so the glob is unambiguous without knowing `cwd_hash`).

```
PVC (per-user RWX, mounted into every pod serving this user):
  /pv/<account_id>/claude/                 ← CLAUDE_CONFIG_DIR (per-account, operator-injected; M5: not stored in any row)
    └── projects/<cwd_hash>/
        ├── <session_uuid>.jsonl
        └── <session_uuid>/subagents/agent-*.jsonl   (same uuid lineage, same pod pin)
```

### 3.4 The two invocation shapes (the whole ballgame)

The SDK appends `--resume` and `--session-id` **independently with no client guard** (`subprocess_cli.py:291-295`); the CLI *binary* rejects the illegal combination. So the platform must form exactly one shape:

- **CREATE (first run):** `options.session_id = session_uuid`; `options.resume = None`; `options.fork_session = False` → `claude … --session-id <uuid>`. The CLI uses the UUID directly as id and filename; nothing to capture, nothing to remap.
- **RESUME (any later turn):** `options.resume = session_uuid`; `options.session_id = None`; `options.fork_session = False` → `claude … --resume <uuid>`. The CLI reopens the same JSONL, appends, writes the same id into new entries.

`is_first_run` is **server-authoritative** but its source is M5, not a status column: **IM** = an atomic `first_run_done` 0→1 CAS on `channel_binding` (`ClaimFirstRunIM`); **WebUI** = empty `session_id` ⇒ CREATE (the gateway just minted it), populated ⇒ RESUME guarded by an NFS-safe `stat` of the derived path (absent ⇒ reconcile-as-create, §3.7). It is **never inferred from a CLI event.** A defensive `assert not (options.session_id and options.resume)` lives in `build_agent_options`.

### 3.5 Caller-side code delta

| File | Change |
|---|---|
| `options.py:260-266` | Replace single `options.resume = session_id` with the create-vs-resume split (§3.4). **Remove** the `fork_session` branch. Add the `assert not (session_id and resume)`. Add `is_first_run` param. Inject `CLAUDE_CONFIG_DIR=<config_home>` into `env_dict`. |
| `service.py` | **Remove** the init/result `session_id` capture-and-mutate logic (the lines setting `current_resume_id` from `message.data["session_id"]`, ~486-490/822-829/847-855) and **remove** the `registry.remap_session(...)` call (~1024). Register `PermissionCoordinator` under `session_uuid` at spawn — no temporary `stream_id`. Add the consistency assertion (§3.7). |
| `permission_coordinator.py` | `remap_session()` becomes dead code — delete it; registry keyed on immutable `session_uuid` from creation. |
| `routers/agent.py:104-107` | `_session_jsonl_path()` derives **in-pod via the SDK** (single-account, M5 §3.3) — never from a central row or a captured CLI event. |
| `models/agent.py:24,209` | Client-supplied `session_id` ignored for id assignment (platform mints). |
| Pre-retry helpers `heal_orphan_tool_uses()`/`strip_synthetic_records()` | Keep — they operate on the JSONL by path; under M2 the path is the stable `<session_uuid>.jsonl`. Verify they re-open by the **derived** path, not a captured event id. |

### 3.6 Flows (all keyed on `session_uuid`)

- **CREATE (M5):** mint (gateway-local `uuid4` for WebUI / `BindChannel` for IM) → router wakes pod, sets Redis route + awake-pin → pod acquires in-pod asyncio.Lock, mirrors to Redis → CREATE shape (`--session-id <uuid>`) → CLI writes `<cwd_hash>/<uuid>.jsonl`. **No row, no status flip** — `is_first_run` was the binding CAS (IM) / empty-id (WebUI). No init/result id capture.
- **RESUME (M5):** inbound turn → binding/route → `session_uuid` (Redis T1 binding-cache, backed by `channel_binding`) → router checks Redis lock-mirror (one-active-run); if another pod holds it, queue/reject → pod acquires lock → RESUME shape (`--resume <uuid>`) → CLI appends the same id. **No `TouchSession`** (no session table); `last_activity` for listing is the JSONL mtime.
- **LIST (M5):** the **`state-reader`** globs the account's `projects/*/<uuid>.jsonl` (works at scale-to-zero; titles from JSONL headers or an optional `sessions.index.jsonl` sidecar). When the pod is awake, its own SDK `list_sessions` serves the same view. **No central `session_index` query**, and — per §1.6 — the parent never imports `_internal.sessions` (the glob needs no SDK helper).
- **REBIND (M5):** UPDATE `channel_binding.session_uuid` + **reset `first_run_done=0`** + stamp `rebound_at`, invalidate the Redis binding-cache. The session id is untouched — rebinding points a channel at a *different existing or fresh* session; it never renames one. Cross-channel leak guard = a session is reachable only through its binding, and only one `readwrite` binding per session (§2.4).
- **FORK (SUPPORTED — see C4):** exposed via the pod's fork seam (`routers/agent.py:546-569` → `sdk_fork_session`). Fork is the **only** verified id-minting path (`session_mutations.py` generates a new UUID, O_EXCL-written, only when `fork_session()` is explicitly called). The child is a **new JSONL** (new lowercase uuid) carrying a `forkedFrom` header (agent-runner §3.1); **lineage is that header / an optional `channel_binding` lineage column — not a `session_index` row** (M5). The **parent's** id is untouched (additive; **still no remap**). Mid-session (`up_to_message_id`) and tail forks are both supported. *(The Agent Runner spec resolves who performs the child-JSONL write under the sole-writer invariant.)*

### 3.7 Immutability guarantee + consistency assertion (verification-as-runtime-guard)

| Vector that could change the CLI id | Verified behavior | Control |
|---|---|---|
| Bare spawn (no `--session-id`/`--resume`) | mints fresh id (today's remap root cause) | Always pass one shape; never spawn bare. |
| Today's `resume` on first run (`options.py:260`) | `--resume` of missing id → fresh id captured & remapped | Split create vs resume (§3.4). |
| Both `--session-id` and `--resume` | SDK appends both unguarded (`subprocess_cli.py:291-295`); CLI errors or forks | Form exactly one shape; `is_first_run` from central status; defensive assert. |
| `fork_session` / `--fork-session` | mints NEW UUID | **Supported (C4):** the child is a new JSONL with its own lowercase uuid + a `forkedFrom` header; **no `session_index` row** (M5). Parent id unchanged → not a remap. |
| Autocompact / thinking summarization | **asserted** id-unchanged | **Must be empirically verified** against a real id-mutating run (§7-Q3 / blueprint Phase-0 exit) + backstopped by the runtime assertion + CI version-pin. |
| Subagent spawn | child under `<uuid>/subagents/`, parent id intact | Same pod pin. |
| `--no-session-persistence` | disables file writing | Never pass for persistent sessions. |
| Concurrent resume from two pods | double-writer corruption on one JSONL | One-active-run lock keyed on `session_uuid` (in-pod asyncio.Lock authoritative + Redis T2 mirror); router gates on it. |
| Mixed-case UUID round-trip | forks the JSONL on case-sensitive FS | Hard lowercase invariant, 4 layers (§2.3). |

**Consistency assertion (defense-in-depth, not state).** On first `system.init` and on `ResultMessage`, the pod reads the reported `session_id` (from `message_parser.py:71,255` — what the CLI *thinks* its id is) and **asserts it equals (lowercase, byte-for-byte) the `session_uuid` it passed.** A mismatch can only mean a contract violation (an unsanctioned id mutation, or SDK semantics drift) — a **sanctioned fork is exempt**: it creates a separate child session resumed under its own `session_uuid`, so the running session's id never changes. This turns M2's "verify, not assume" into a per-run, self-checking invariant; pin the SDK/CLI version and run the assertion in CI against the pinned version so a semantics-changing bump fails the build, not production. On any observed id change at runtime, **quarantine the session** — stop the run and clean the Redis keys for that `session_uuid`; under M5 there is **no status row to set**, so quarantine is a teardown, never a re-key.

**One caveat that must be empirically closed before the assertion's failure-action is finalized (§7-Q3):** the assertion is correct to *fail-hard* on an unexpected NEW id mid-stream, but on the RESUME path with a **missing** JSONL (e.g. RWX/NFS attribute-cache lag so the file isn't yet visible), if `claude --resume <id>` mints+reports a fresh id, a fail-hard would convert a recoverable visibility lag into a spurious "tamper" alarm. The SDK's *materialized*-resume path writes the JSONL when absent (`session_resume.py:149,164`), but the **bare `--resume`-of-missing** behavior is unverified by static read. If it mints, the resume path needs a pre-flight existence check (NFS-cache-safe `stat` of the derived path, or trust the central `is_first_run` flag) before choosing resume-vs-create, and the assertion must **reconcile (treat as create), not page**, in that specific case.

### 3.8 RWX storage isolation (per-user mount scoping)

The session JSONL (§3.3) and audit (§2.5) ride on a **shared RWX backend**, but **no tenant pod ever mounts more than its own subdir** — isolation is a property of the *mount*, not the backend.

```
Networked RWX filesystem (one server / export)
  /export/
    ├── <account_a>/   (owned uid:gid Ua, mode 0700)
    └── <account_b>/   (owned uid:gid Ub, mode 0700)

pod(account_a) ─ mounts ONLY /export/<account_a> as /workspace
  • subPath=<account_id>, OR a per-user PV whose path *is* the subdir,
    OR an EFS/Filestore access-point locked to that subdir+uid
  • runAsUser=Ua, runAsNonRoot, drop ALL capabilities, readOnlyRootFilesystem
  • no path to a sibling exists in its mount namespace; cannot `cd ..` out of the
    bind mount; cannot remount (no CAP_SYS_ADMIN); 0700 + foreign uid blocks reads

state-reader / audit-reader (admin, READ-ONLY) ─ mounts /export (whole tree)
  • the ONLY thing that sees across tenants; trusted control-plane, not a tenant pod
```

**Three layers (defense-in-depth):**
1. **Scoped mount (primary).** Each pod mounts only its subdir; the container mount namespace gives it no handle to siblings. **Recommended:** the `AgentTenant` operator (decision 12) provisions a **per-user PV / access-point** whose root *is* the subdir — parent unreachable by construction, natural per-user quota. `subPath: <account_id>` on one shared PVC is the simpler, adequate fallback.
2. **POSIX + non-root (backstop).** Per-user `uid:gid`, dir mode `0700`, pod `runAsUser`=that uid, non-root. A foreign uid is denied even on a misconfigured mount.
3. **No escalation.** Drop ALL caps + non-root → the agent can't remount the parent or `chmod`/`chown` around the perms.

**Requirements/caveats:** NFS export with `root_squash` (never `no_root_squash`); consistent uid mapping across nodes; `CAP_DAC_OVERRIDE` dropped (covered by drop-ALL). **The same scoping applies to the audit volume** (`/audit/<account_id>/`): pods write only their own subdir; only the read-only `audit-reader` / `state-reader` mounts the whole tree. This is what makes "inspect any user anytime" a capability of the **trusted reader**, never of a tenant pod. The **gateway and control panel hold no `/export` mount themselves** — the gateway routes + reads central metadata only (FS-free, stateless), and the control panel reaches file content *through* the `state-reader` (and usage/metadata via the data-plane + proxy). Per-user WebUI views are scoped by the gateway-supplied `account_id`; only admin (control-panel) requests read cross-tenant.

### 3.9 Wake-free read path (inspect any user while their pod sleeps)

A core platform requirement: the control panel (and per-user WebUI) must read any user's metadata, usage, transcripts, audit, and hook logs **at any time — including while that user's Agent Runner is scaled to zero — without waking the pod.** Only *running a new turn* needs a wake; *reading* never does. This holds because every read targets a store the pod does **not** own exclusively:

| What | Wake-free source | Why pod-independent |
|---|---|---|
| Account / quota / bindings / config | central SQLite, via the data-plane | the data-plane is the writer, never the pod |
| Usage (token counts) | **pod self-report → data-plane** (M6 — no proxy) | observability-only; `$` computed `tokens × price card` for display; spend enforcement deferred (residual default #1) |
| Session **list** + transcripts (`<session_uuid>.jsonl`) | `/export/<account_id>/…` via the read-only `state-reader` (glob, M5 — no `session_index`) | the RWX volume persists across the pod's whole scale-to-zero lifecycle |
| Audit JSONL | `/audit/<account_id>/…` via the read-only `audit-reader` | dedicated audit volume, decoupled from the pod (C1) |
| Hook execution logs | per-user PV, via the `state-reader` | per-PV bytes, decoupled from the pod (residual default #2) |

**The `state-reader` / `audit-reader` is the only component with a cross-tenant (whole-tree, read-only) mount** (§3.8); it never writes, never acquires a session lock, and never patches the `AgentTenant` CR — so a read can never trigger a wake. The pod stays the **sole writer** of its JSONL/PV/audit subdir, so even when a reader is attached while the pod is awake there is no write contention (decision 16: a 2nd surface attaches read-only). Net: "inspect any user anytime" is a capability of the trusted reader plane, fully decoupled from scale-to-zero — the pod is woken **only** to execute a turn, never to be observed.

---

## 4. Two-Tier Redis Key Catalog

**Tier contract.** **T1 = persistent/AOF** (`appendfsync everysec`, RDB backup, `maxmemory-policy noeviction`): losing an entry is a *correctness* bug — the gateway "on it…" ack fires only *after* the T1 write confirms. **T2 = ephemeral cache** (no AOF, `allkeys-lru`, reconstructible from SQLite/operator-CR/proxy-log): a loss costs a re-derivation, never correctness. On any T1↔T2 vs SQLite conflict, **SQLite is authoritative** and Redis is re-seeded. **Preferred deployment: two separate Redis instances** so T2 cache load can never evict a T1 inbox/approval entry (fallback: logical DB split T1=db0/T2=db1). **Universal keying invariant (M2): every key is rooted on `{account_id}` or the immutable `{session_uuid}` — never on a mutable `cli_session_id` (which does not exist).**

### Tier 1 — Persistent / AOF (correctness-critical)

| # | Key | Type | TTL | Writer | Reader | Rationale |
|---|---|---|---|---|---|---|
| 1 | `inbox:{account_id}` | LIST (FIFO; RPUSH/LPOP+LRANGE; `LTRIM` cap ~50) | ~1h per-entry (msg `ts` + drain-prune) | Gateway (post pre-gate) | Pod boot drain | The "on it…" ack fires only after this RPUSH confirms; loss = silently dropped promised message. |
| 2 | `inbox:dedup:{account_id}` | HASH (`fingerprint→ts`) | ~1h (sidecar to #1) | Gateway (coalesce on push) | Gateway | Duplicate-coalescing; AOF so failover doesn't re-admit a coalesced dup. Paired 1:1 with #1 (mutate via MULTI/Lua). |
| 3 | `approval:index:{request_id}` | HASH `{session_uuid,account_id,pod,status,created_at}` | `approval_window+1h = 25h` | Pod (at permission-gate mint) | Any gateway replica on reply; pod boot purge | Late reply (hours later, different replica) maps `request_id→session_uuid→pod` only via this. Loss = unresolvable reply + zombie-pinned pod. |
| 4 | `pin:approval:{account_id}` | SET (members = `request_id`) | none (membership; SREM on resolve) | Pod (SADD mint / SREM resolve) | Operator (scale-to-zero gate); pod boot purge | Non-empty set = operator's hard refusal to scale-to-zero. Paired 1:1 with #3; boot reconciler kills orphans (DEL index + SREM pin, atomic). |
| 5 | `spend:reserve:{account_id}` | — | — | — | — | **DEFERRED (M6 — spend tracking off).** No reservation is written; the cap is config-only and unenforced; usage is pod-self-reported instead (§2.8). *Reversible: re-arm as a Tier-1 INCRBY/DECRBY pre-charge (writer = pod/scheduler; reader = pre-gate) to close the check→spend race when metering returns.* |
| 6 | `binding:cache:{session_uuid}` | HASH `{channel_id,bot_id,channel_type,identity_id}` | none (write-through; refresh on rebind) | Identity-auth (on bind/rebind) | Gateway (reply-routing hot path) | Write-through mirror of `channel_binding`; gateway routes without a DB round-trip. Single-writer (identity-auth writes, gateway reads); SQLite wins on conflict. |
| 7 | `in:reply:{request_id}` | LIST (relay; RPUSH/BLPOP) | ~60s (safety reaper) | Gateway replica that received the reply | Gateway replica owning the pod WS | Cross-replica reply relay (sticky L4 can miss); a dropped reply re-creates the zombie-pin. Short TTL = transient hand-off. |

### Tier 2 — Ephemeral Cache (reconstructible)

| # | Key | Type | TTL | Writer | Reader | Rationale |
|---|---|---|---|---|---|---|
| 8 | `route:{account_id}` | HASH `{pod_ip,pod_name,state,updated_at}` | ~30s sliding (pod heartbeat) | Pod (boot + heartbeat) | Gateway (asleep/awake pre-check) | Pure optimization; a miss falls through to "treat asleep → wake" (always safe). The CR patch is the only real wake trigger. |
| 9 | `awake:set` | SET (members = `account_id`) | none (self-heals via #8 expiry) | Operator/pod register | Gateway, scheduler | Fast liveness hint; reconstructible from CR/route; loss only adds idempotent wake nudges. |
| 10 | `awake:lock:{account_id}` | STRING (SET NX PX) | ~10s | Any waker before CR patch | — (mutex only) | Serializes the one scale-up trigger so IM+scheduler don't double-patch the CR. PX self-heals a crashed waker. |
| 11 | `lock:session:{session_uuid}` | HASH `{holder_pod,acquired_at,heartbeat_at}` | ~30s (= 3× heartbeat) | Owning pod (mirror of in-pod asyncio.Lock) | Gateway/WebUI (display); operator; status reconciler | **STATUS MIRROR ONLY — never an acquisition gate.** In-pod asyncio.Lock is authoritative. Demoting to a mirror kills the dual-authority race. Loss harmless (rebuilt next heartbeat). |
| 12 | `wake:pod:{account_id}` | Pub/Sub channel | n/a | Waker (after CR patch) | Operator reconcile loop | Nudge only — accelerates reconcile; CR patch is sole authority. A dropped message just defers to periodic reconcile. |
| 13 | `channelconn:lease:{channel}:{bot_id}` | STRING (SET NX PX; value=`replica_id`) | 10s TTL / 3s heartbeat | Gateway replica claiming the channel socket | All gateway replicas | Exactly one replica holds the WeCom/IM socket (no double-connect split-brain). Fast TTL → brief inbound stall on owner death, recovered when another replica re-leases + re-handshakes. |
| 14 | `job:{job_id}:fire:{fire_epoch}` | STRING (SET NX PX; value=`scheduler_replica_id`) | ~120s | Scheduler replica claiming a due fire | Scheduler replicas | **Fast pre-filter only**; authority is the SQLite `job_fire` PK `(job_id,fire_epoch)`. Scheduler calls `ClaimJobFire` (durable) **only after** winning this Redis pre-filter, so the single-writer DB sees ~one claim per fire, not one per replica (§6). |

### 4.x Cross-cutting Redis policies

- **TTL summary (from locked knobs):** lease 10s/hb 3s (#13); `awake:lock` ~10s (#10); session-lock mirror ~30s = 3× hb (#11); route ~30s sliding (#8); reply relay ~60s (#7); job claim ~120s (#14); inbox per-entry ~1h cap 50 (#1-2); approval index 25h = window(24h)+1h (#3). (`spend:reserve` #5 is **deferred — M6**; no key, no reset job.)
- **Boot-purge reconciliation (zombie-pin fix):** on boot, for each `pin:approval:{account_id}` member the pod re-validates `approval:index:{request_id}` against live in-pod Futures; orphans = `DEL index + SREM pin` atomically (Lua/MULTI).
- **DB-status reconciler — MOOT under M5.** There is no `session_index.status` to drift, so the stale-`running` reconciler is withdrawn. Run liveness is purely the Redis `lock:session:{session_uuid}` mirror (#11, ~30s TTL) + `route` (#8): a dead pod's mirror simply **expires**, and any reader treats absent/expired as "not running." No periodic DB sweep is needed.
- **Spend reservation authority — DEFERRED (M6).** No reservation, no proxy charge, no settlement (§2.8): spend tracking is off; the pod self-reports token counts for observability only. *Reversible (pre-M6 model): reservation (#5, T1) → proxy charge, with the reaper reconciling orphans against the proxy event log via `proxy_event_id`.*
- **M2 audit hook:** because nothing keys on a mutable id, a **sanctioned fork** creates a NEW child session with its own keys and so never invalidates the parent's #3/#6/#11. The only invalidating event is an **unsanctioned** id change (the §3.7 rule) → quarantine the session and re-key #3/#6/#11 to the new `session_uuid` before resuming.

---

## 5. Migration of File-State into SQLite

A standalone, **idempotent** `priva-dataplane migrate` job (K8s `Job`, run once at M1 cutover, **before** any caller points at the service). It reads legacy files with the *existing* `_load()` logic (so semantics match exactly), then bulk-inserts one transaction per domain.

| Legacy source | → SQLite | Transform / note |
|---|---|---|
| `.priva.settings.yml` `users.*` | `account`, `account_runtime_config` | bcrypt `password_hash` copied as-is. **`api_key`: decrypt with the old hardcoded Fernet key → re-encrypt under the new per-account DEK.** The hardcoded key (`crypto.py:10`) is used **only** by the migrator, then deleted. |
| `.priva.settings.yml` `runtime.*` | `account_runtime_config` | `cli_path`, `append_systemprompt`, `retryable_tools`, `risky_tool_list`, `pii_masking`, `history_retention_days`. **Bake in** the lazy migrations (`sensitive_data_patterns→pii_masking`, `enable_global_skills→skill_exclude`) here, then freeze — no runtime lazy migration post-cutover. |
| `.priva.audit.{date}.jsonl` + counts sidecar | **stays JSONL (C1)** | **Not migrated into SQLite.** Relocated per-user to `/audit/<account_id>/YYYY-MM-DD.jsonl` on the dedicated audit volume (pod = sole writer). Counts sidecars dropped. Optional per-file hash-chain is opt-in. |
| `.priva.wecom.sessions.json` | `channel_binding` (identity-keyed; **no `wecom_session`** — withdrawn) | `last_activity` already ISO-8601 UTC TEXT → plain copy (no monotonic re-baseline). **M5: no `session_index` row to seed.** A **1:1** entry's `session_id` (already the JSONL filename) → a `channel_binding` row keyed on the resolved **sender identity** (`session_uuid = that id`, `first_run_done=1`). **Group** (`chat_id`-keyed) entries are **not** migrated as shared sessions (no-chat_id-key, 2026-06-18) — members re-establish their own identity bindings on first message. |
| `.priva.user.yml` `channels.wecom`/`.openclaw` | `channel_config_wecom`/`_openclaw` + `secret` | `secret`/`auth_token`/OpenClaw Ed25519 PEM: plaintext today → **encrypt under per-account DEK** (rows in `secret`, configs reference `secret_id`). |
| `.priva.user.yml` `scheduled_jobs[]` | `scheduled_job` | polymorphic `trigger_config`/`job_config` as JSON TEXT; `job_config.session_id → session_uuid`. |
| `.priva.scheduler.history.{date}.jsonl` + counts | `job_run_record` | `session_id → session_uuid` (nullable). |
| `{user}/temp/uploads/.index.jsonl` | `temp_file` | bytes stay on PV; only the index migrates; preserve `deleted`/`deleted_at`. |
| `.mcp.json` (project) + `~/.claude/settings.json` `mcpServers` | `mcp_server_config` | `headers` → JSON TEXT; `scope ∈ {project,global}`. |
| **`hooks/config_manager.py` source (per-scope hook config)** | **`hook_config`** | per critique blocker #2 — central per-account fact every pod's options-builder reads; must leave the RWX FS. |
| **`hooks/log_store.py` source** | **stays on per-user PV (RWX)** | Residual default #2 (locked): hook execution logs are never centralized — pod is sole writer; `state-reader` serves cross-tenant reads (§3.9). No `hook_log` table. |
| `.priva.user.yml` `skill_exclude` | `account_runtime_config` | list → cfg_value JSON; apply `enable_global_skills→skill_exclude` in the migrator. |
| `{user}/.claude/settings.local.json` `ANTHROPIC_*` | `secret` (`secret_type='env_var'`) | plaintext today → encrypt under per-account DEK. |

**Full fcntl audit (nothing left on RWX):** the migrator + RPC surface together cover `user_store`, `audit_log`, `temp_files`, `user_env`, `_pagination`, `scheduler/job_store`, `scheduler/run_history`, `mcp/config_manager`, `channels/config_store`, **`hooks/config_manager`, `hooks/log_store`, `pty_session`**. (`pty_session` state: confirm whether it is transient per-pod runtime — if so it stays in-pod and out of the spine; this is verified during the audit, not assumed.)

**Safety.** *Read-only on source* — never deletes legacy files; cutover flips `STORE_BACKEND=dataplane`, legacy files kept one release as a rollback escape hatch, then archived. *Idempotent* — every insert `ON CONFLICT DO NOTHING` on the natural PK (username, `run_id`, temp uuid, `binding_id`; M5: no `session_index`/`session_uuid` PK); a `migration_manifest` row records source→rows-imported + content hash so re-runs skip done files and partial runs resume. *Verification gate* — post-migrate the job asserts row-counts against the legacy counts sidecars (audit, scheduler.history) and **decrypt-round-trips every migrated secret** before declaring success; `Readyz` stays red until the manifest shows all domains `verified`. *Per-user files* (`.priva.user.yml`, `.mcp.json`, temp index, hook config) are walked once per `account` — naturally parallel-safe since each row keys on `account_id`.

---

## 6. Resolved Risks

| # | Risk (from critique) | Severity | Resolution (folded into spec) |
|---|---|---|---|
| R1 | SDK in-process session helpers (`_get_project_dir`/`_find_project_dir`) read `CLAUDE_CONFIG_DIR` from `os.environ` only (`sessions.py:144`); unusable in a multi-tenant parent for read/reconcile/delete. | blocker | §1.6 CI ban on importing `claude_agent_sdk._internal.sessions`. **M5:** paths are derived **in-pod** by the SDK (single-account, `os.environ` correct — agent-runner §1.3) and located by the read-only `state-reader` via glob — no persisted `cwd_hash`, no parent-side path resolution (§3.3). |
| R2 | Hooks state (`hooks/config_manager.py`, `hooks/log_store.py`) + `pty_session.py` omitted from migration → stay fcntl on RWX, violating M1. | blocker | Hooks added to RPC surface (§1.7) + schema (`hook_config`, optional `hook_log`, §2.13) + migration table (§5); full fcntl audit listed; `pty_session` confirmed transient-or-migrated in the audit. |
| R3 | UUID casing: `_UUID_RE` IGNORECASE + `_validate_uuid` verbatim (`sessions.py:47,69-72`) → mixed-case forks the JSONL on case-sensitive FS, an id-mutation bug. | blocker | Hard lowercase invariant: mint lowercases, pod normalizes pre-call, the §3.7 assertion compares lowercase byte-for-byte. **M5:** the DB `CHECK` is gone (no `session_index` table), so enforcement is mint + pod + assertion (3 layers). |
| R4 | Litestream + single writer: restore-window write-blackout + seconds-RPO can lose the audit chain head (false tamper) / a settlement (silent spend leak). | blocker | **Dissolved by C1+C2+C3:** audit→PVC JSONL (C1), no Litestream→OSS (C2), spend in the proxy not SQLite (C3). SQLite holds zero money/audit data → no `synchronous=FULL`, no chain head to lose. Residual write-blackout (~10-60s) touches only config/metadata reschedule — harmless (§7-Q2, resolved). |
| R5 | §6 consistency assertion fail-hard would turn a recoverable resume-of-missing-file (RWX visibility lag) into a spurious tamper alarm; bare-`--resume`-of-missing behavior unverified. | major | Assertion fail-hard on unexpected NEW id mid-stream; **reconcile-as-create** on resume-of-missing; pre-flight existence check on resume path (§3.7). Empirical verification flagged as hard pre-lock open question (§7-Q3). |
| R6 | Forbidding fork doesn't prove autocompact preserves the id; asserted, not shown. | major | Empirical autocompact id-stability test required (blueprint Phase-0 exit) (§7-Q3); runtime assertion as backstop; SDK/CLI version pinned + assertion run in CI against the pin; "on any observed id change, quarantine the session" generalized (§3.7, §4.x). |
| R7 | Three drafts disagree on `session_index` columns and binding-uniqueness; Draft 4's `UNIQUE(session_uuid) WHERE rebound_at IS NULL` is semantically wrong. | major | **M5: `session_index` withdrawn entirely** (§2.3) — the column debate is moot. Binding uniqueness still locked on `channel_binding`: partial UNIQUE `(session_uuid) WHERE mode='readwrite'` (one read-write, N read-only) + `UNIQUE(identity_id)`; `rebound_at` predicate dropped (§2.4). |
| R8 | Envelope-encryption custody flaw: central decryption in the data-plane collapses per-account blast radius into one process. | major | Data-plane returns **wrapped DEK + ciphertext**; **pod calls KMS `UnwrapDEK` scoped to its own account** — plaintext DEK/secret never in the data-plane (§2.9, §1.7). KMS backend = pre-migration blocker (§7-Q1). |
| R9 | Spend correctness hole: pod dies between the Redis reservation and the durable charge → charged-but-unreconciled → silent cap overshoot. | dissolved (M6) | **Cannot occur** — spend tracking is deferred (M6): no reservation, no charge, nothing to reconcile (§2.8). Usage is pod-self-reported, observability-only. *Re-arms (`proxy_event_id` + reconcile) with a future metering mode.* |
| R10 | Audit archival via one transaction across attached DB files is non-atomic in WAL; "snapshot boundary prev_hash" underspecified. | major | **Moot for audit (C1: audit is JSONL-on-PVC, not SQLite).** For the one remaining SQLite append-only log (`job_run_record`): append-only-never-delete by default (single `ts`-indexed table scales to tens of millions); if archival forced, two separate committed txns, no cross-attached-DB transaction relied on (§2.5). |
| R11 | `daemon.py` `last_activity` migration based on misreading (persisted value is already ISO, not monotonic); epoch-millis would create a 2nd timestamp format. | major | Persisted `.priva.wecom.sessions.json` `last_activity` is already ISO UTC (daemon.py:1339) → plain copy, no re-baseline; single ISO-8601 UTC TEXT convention kept (§2.12, §5). |
| R12 | `channel_binding` forward-reference + `foreign_keys=OFF`-in-transaction workaround is a no-op (PRAGMA ignored inside txn) and unnecessary. | major | Workaround dropped; DDL order irrelevant — SQLite resolves FK targets lazily at write time; keep `foreign_keys=ON` + CI guard (§2.14). |
| R13 | DB `status='running'` drifts forever for a dead pod; partial index accumulates stale rows. | minor | **Moot under M5** — there is no `session_index.status` (no session table). Run liveness is the Redis `lock:session` mirror (~30s TTL) + `route`; a dead pod's mirror just expires (§4.x). |
| R14 | `ClaimJobFire` durable claim vs Redis claim — which is authority; cron-storm hits the single writer. | minor | Redis `job:*:fire:*` = pre-filter; SQLite `job_fire` PK = authority; scheduler calls `ClaimJobFire` only after winning the Redis pre-filter ⇒ ~one DB claim per fire (§2.11, §4 #14). |
| R15 | EAV `account_runtime_config` defeats STRICT discipline; read-time lazy migrations have no hook over opaque JSON. | minor | Lazy migrations baked into the one-time migrator; schema frozen post-cutover — no runtime lazy migration (§2.1, §5). |

---

## 7. Open Questions for the User

1. **KMS backend (hard pre-migration blocker).** §5's secret re-encryption cannot run until the master-KEK custody is chosen. Which concrete KMS — a cloud KMS (which provider) or an in-cluster soft-HSM (e.g. Vault Transit / SoftHSM)? **The decryption locus is RESOLVED (no longer per-pod):** the **operator** unwraps at pod spawn and injects a tmpfs bundle — the pod calls **no KMS and holds no DEK** (agent-runner §13-3, operator §6). Under **M6** that bundle carries the user's **own BYOK LLM key** alongside MCP/env secrets (§2.9). KMS RBAC is scoped to the **operator only** (per-account unwrap preserves blast radius). Only the **backend choice** (Vault vs cloud) remains open; it no longer blocks the pod build.

2. **Durability SLA — RESOLVED (residual default #1, locked).** With audit (C1), `budget_ledger` (C3), and `usage_rollup` all out of SQLite, the DB holds only config/metadata/index — nothing money/audit-critical — so **no table needs `synchronous=FULL`** and a few seconds of write-blackout on reschedule is harmless. **`usage_rollup` is dropped** (§2.6); under **M6** token **usage is pod-self-reported** to the data-plane (no metering proxy) and **spend enforcement is deferred** — SQLite caches no token/cost rows. *(KMS backend, Q1, remains the only hard pre-migration blocker.)*

3. **Empirical M2 verification — DEFERRED to Phase 0 (residual default #4, locked).** Two behaviors are unresolved by static read and **must be tested against the installed `claude CLI`/`claude-agent-sdk==0.2.93` as a Phase-0 exit criterion** (not a design-freeze blocker): (a) what **bare `claude --resume <id>` does when the JSONL is absent** — error, or silently mint+report a new id (determines whether the resume path needs a pre-flight existence check and whether the §3.7 assertion fail-hards or reconciles); (b) whether **autocompact preserves the session id** across a real token-threshold-triggered compaction run. **Backstop until Phase 0:** the §3.7 per-run consistency assertion (fail-hard on any id drift) plus a **CI version-pin** on `claude-agent-sdk` so a semantics-changing bump fails the build, not production. Design proceeds on the documented `types.py`/`sessions.py` static reads; Phase 0 confirms them against a real id-mutating `claude` run before cutover.

4. **Hook execution logs — RESOLVED: per-user PV (residual default #2, locked).** `hooks/log_store.py` output stays **per-user PV bytes on the RWX volume**, never centralized — same treatment as the session JSONL and audit JSONL (pod is sole writer; `state-reader` serves cross-tenant reads, §3.9). There is **no `hook_log` table and no `AppendHookLog`/`QueryHookLog` RPC**. Only hook *config* is central (`hook_config`).

5. **`session_index` column set — MOOT (resolved by M5, 2026-06-18).** The table is **withdrawn** (§2.3) — there is no column set to confirm; the JSONL is the session store. The **binding-uniqueness** rule stands on `channel_binding`: partial UNIQUE `(session_uuid) WHERE mode='readwrite'` + `UNIQUE(identity_id)` (no `rebound_at` predicate), plus the new `first_run_done` CAS column that now carries IM `is_first_run` (§2.4, §3.4).
