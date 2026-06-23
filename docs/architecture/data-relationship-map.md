# Priva Cloud — Data Relationship Map

> Where every kind of state lives, who owns it, and how it relates.
> Companion to `components/data-spine.md` (design) and the Phase-1 plan.
> Reflects the **locked** Phase-1 schema: 16 designed tables → **5 in SQLite**, the rest
> dropped / deferred / pushed to pod-owned PVC files.

---

## 1. The durable relational core — data-spine SQLite (5 tables)

`account` is the hub; every other table hangs off `account_id`.

```
                       ┌─────────────────────────────────────────────┐
                       │  account                         ★ THE HUB   │
                       │ ─────────────────────────────────────────── │
                       │ PK account_id         TEXT (minted uuid)     │
                       │    username           ── UNIQUE              │
                       │    password_hash      bcrypt                 │
                       │    api_key            Fernet 'enc:v1:'       │
                       │    api_key_lookup     ── UNIQUE (HMAC)       │
                       │    role | status                             │
                       │    feishu_user_id     ── UNIQUE (nullable)   │
                       │    feishu_display_name                       │
                       │    created_at | updated_at                   │
                       └───────────────┬─────────────────────────────┘
                                       │  account_id   (FK · ON DELETE CASCADE)
          ┌────────────────┬──────────┴──────────┬───────────────────────┐
          │ 1 : 1          │ 1 : 0..1            │ 1 : N                 │ 1 : N
          ▼                ▼                     ▼                       │
 ┌─────────────────┐ ┌──────────────────┐ ┌──────────────────────┐     │
 │ quota           │ │ channel_binding  │ │ scheduled_job        │     │
 │ ─────────────── │ │ ──────────────── │ │ ──────────────────── │     │
 │ PK account_id   │ │ PK binding_id    │ │ PK job_id            │     │
 │    tier         │ │ FK account_id  U │ │ FK account_id        │     │
 │    max_concurr… │ │    session_uuid U│ │    name | prompt     │     │
 │    idle_grace…  │ │    first_run_done│ │    trigger    (json) │     │
 │    updated_at   │ │    feishu_chat_id│ │    job_type          │     │
 └─────────────────┘ │    bound_at      │ │    job_config (json) │     │
   one row /account  │    rebound_at    │ │    timezone | model  │     │
   (operational      └──────────────────┘ │    status            │     │
    levers)            greenfield;         └──────────┬───────────┘     │
                       feishu only;                   │ job_id          │
                       no Phase-1 caller              │ (FK·SET NULL)   │
                                                      │ 1 : N           │
                                                      ▼                 │
                                          ┌──────────────────────┐     │
                                          │ job_run_record       │◄────┘
                                          │ ──────────────────── │ account_id
                                          │ PK run_id            │ (FK·CASCADE)
                                          │ FK job_id  (SET NULL)│
                                          │ FK account_id (CASC) │
                                          │    job_name (denorm) │
                                          │    session_id        │
                                          │    started/finished  │
                                          │    status | duration │
                                          │    is_error|num_turns│
                                          │    error|result_summ │
                                          └──────────────────────┘
                                            metadata only — the run's
                                            OUTPUT transcript lives on PV
```

**Reading the edges**

| Relationship | Cardinality | FK rule | Note |
|---|---|---|---|
| `account → quota` | 1 : 1 | CASCADE | PK = account_id; seeded with defaults |
| `account → channel_binding` | 1 : 0..1 | CASCADE | `UNIQUE(account_id)` + `UNIQUE(session_uuid)`; one feishu binding/account |
| `account → scheduled_job` | 1 : N | CASCADE | many jobs per account |
| `account → job_run_record` | 1 : N | CASCADE | deleting an account wipes its runs |
| `scheduled_job → job_run_record` | 1 : N | **SET NULL** | runs survive job deletion; `job_name` denormalized so history stays readable |
| `account → secret` | 1 : 1 | CASCADE | BYOK bundle (Fernet); operator materializes a K8s Secret at wake |
| `account → account_resource_spec` | 1 : 1 | CASCADE | cpu_cores / memory_mb / volume_gb; operator → container resources + PVC size |
| `pending_registration` | — | **no FK** | self-registration request awaiting approval; the account does not exist yet |

Deleting an `account` cascades to quota, binding, jobs, runs, secret, and resource-spec — one
clean blast radius. `pending_registration` stands alone (independent of `account`); a `pending`
row is consumed at approval (→ creates the account) or rejected.

> **As-built increment (2026-06-24).** `account` gained `agent_runner_type` (`auto_scale` |
> `persistent`); two tables were added — `account_resource_spec` (per-account pod sizing) and
> `pending_registration` (self-service approval queue). The slice's `secret` table (line 147's
> "DROPPED" list) is in fact **re-added** as-built (BYOK creds for operator injection).

---

## 2. The three stores — who owns what

Only the relational core is in data-spine. Two other stores hold the rest, by deliberate design.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ① data-spine  ·  SQLite, single-writer gatekeeper  ·  DURABLE / SHARED     │
│   account · channel_binding · quota · scheduled_job · job_run_record       │
│   accessed ONLY via the dataplane client (in-process Phase 1 → gRPC later) │
└────────────────────────────────▲───────────────────────────────────────────┘
                                  │ get_client()  (InProcess → handlers → SqliteRepo)
       brain / control-panel ─────┤
       scheduler ─────────────────┘   (later: agent-runner, operator, connector)

┌──────────────────────────────────────────────────────────────────────────┐
│ ② Redis  ·  networked, multi-client  ·  COORDINATION / EPHEMERAL           │
│   locks · permission requests · session routing · presence · read caches  │
│   accessed DIRECTLY by every service with its own redis client            │
│   shared contract = priva_common.redis_catalog  (key NAMES only)          │
└──────────────────────────────────────────────────────────────────────────┘
       every service ──── direct redis API (NOT proxied through data-spine)

┌──────────────────────────────────────────────────────────────────────────┐
│ ③ PVC per pod  ·  RWO file state  ·  POD-OWNED (agent-runner)              │
│   runtime config   .priva.settings.yml[runtime] · .claude/settings.local… │
│   MCP config       .mcp.json · ~/.claude/settings.json (mcpServers)       │
│   hook config      .claude/settings.json · settings.local.json (hooks)    │
│   temp files       temp/uploads/{date}/{uuid} + .index.jsonl              │
│   session JSONL  · run-output transcript · audit JSONL                    │
│   → edited only while the pod is alive (login wakes it); CLI/SDK read      │
│     these native files directly, so a DB copy would be redundant          │
└──────────────────────────────────────────────────────────────────────────┘
       owned + read/written by the pod (agent-runner); edit-while-alive
```

**Mental model:** data-spine = the one shared ledger a librarian guards · Redis = the public
bulletin board everyone posts to directly · PVC = each pod's private desk drawer.

**Why the split?** SQLite is a single file → it needs one owner process (data-spine + a gRPC-shaped
client). Redis is already a networked multi-client server whose atomic primitives (`SET NX`, `SADD`,
Lua/`MULTI`) *are* the coordination mechanism, so proxying it would only add latency. PVC files that
the Claude Code CLI/SDK read natively (`.mcp.json`, `.claude/settings*.json`) must exist on the pod's
filesystem regardless — a database copy would be redundant and could drift.

---

## 3. Migration sources → which file feeds which table

```
 .priva.settings.yml (users map) ──┬─► account     (mint account_id; api_key → lookup HMAC)
                                    └─► quota       (seed defaults per account)
 {user}/.priva.user.yml            ───► scheduled_job        (agent_run rename applied)
 {user}/.priva.scheduler.history.*.jsonl ─► job_run_record   (metadata only; transcript stays PV)
 (feishu not built yet)            ───► channel_binding      = empty (greenfield)

 NOT migrated — stay as PVC files:
   runtime config · temp_file · MCP · hooks · session JSONL · audit JSONL
```

Migration is idempotent (`INSERT … ON CONFLICT DO NOTHING` on the natural UNIQUEs `username` /
`job_id` / `run_id`); running it twice yields identical row counts.

---

## 4. What left SQLite (and why)

| Disposition | Tables | Reason |
|---|---|---|
| **DROPPED** | `account_runtime_config`, `identity_link`, `account_dek`, `secret`, `channel_config_wecom`, `channel_config_openclaw` | feishu-only channel model · no KMS/envelope encryption (monolith Fernet + bcrypt reused) · runtime config is PVC-owned |
| **DEFERRED** | `retention_state`, `job_fire` | no Phase-1 caller — ship with the offboarding/purge worker and the multi-replica scheduler respectively |
| **WITHDRAWN → PVC** | `temp_file`, `mcp_server_config`, `hook_config` | file-coupled / Claude-Code-native config the SDK reads locally |
| **already JSONL on PV** | session index, audit log, per-run output transcript | append-heavy, per-user, co-located with their data |

---

*Conventions:* all SQLite tables `STRICT`, `foreign_keys=ON` per connection, WAL. Timestamps are
stored **UTC ISO-8601** (`2026-06-20T03:32:01.112Z`) and rendered **UTC+8** (`2026-06-20
11:32:01.112`, Asia/Shanghai) at the display edge only. Proto wire types for timestamps and JSON
blob columns (`trigger`, `job_config`) are `string`.
