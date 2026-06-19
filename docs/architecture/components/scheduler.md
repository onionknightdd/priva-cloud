---
Status: Draft · Date: 2026-06-18 · Branch: multi-tenant-platform
Parent: ../multi-tenant-platform.md · Component: Central Scheduler (owns 100% of cron/interval/one-shot jobs; N stateless leaderless replicas) — realizes blueprint **decision 9** (§85) and **§205-208**
Consumes: ./data-spine.md (the `scheduled_job`/`job_fire`/`job_run_record` schema §2.11, the scheduler RPC surface §1.7, the Redis fire-claim #14 / `awake:lock` #10 / `inbox` #1), ./operator.md (the CR-patch wake — the scheduler is a *waker*, never a scaler; the wake-storm limiter), ./agent-runner.md (the pod **executes** every dispatched job; the inbox drain §11 B7c; the in-pod single-writer lock), ./agent-gateway.md (the `PushToChannel` proactive-IM seam §8.4 — the scheduler's only delivery path), ./control-panel.md (admin `/scheduler/*` verbs land here) as binding contracts
---

# Priva Central Scheduler — Component Specification

**Scope.** The **Central Scheduler** owns **100% of cron / interval / one-shot jobs** (blueprint decision 9 §85; §205). Unlike the operator, this is **not** a green-field component: there is a substantial, working single-machine scheduler today — **~3.5k LOC** across `priva/api/services/scheduler/*` (a standalone `AsyncIOScheduler` daemon `daemon.py:68`, a YAML job store `job_store.py`, daily-partitioned JSONL run history `run_history.py`, four job-type executors, an in-pod MCP tool server `mcp_tools.py`, and a file-command IPC bus `shared.py:46`) plus the `routers/scheduler.py` API and `models/scheduler.py`. So this drill is a **fork-strip-and-invert** of real code, grounded `file:line` in it (§1) and in every cross-doc promise (§0).

**The one load-bearing inversion.** Today the scheduler **is the agent runner**: a `scheduled_agent` job calls `agent_run_events(...)` **in-process inside the daemon** (`daemon.py:298-315`), with `bypassPermissions`, reading the user's env (`daemon.py:247`), spawning the `claude` subprocess, writing the user's JSONL. That is impossible in the multi-tenant platform: the scheduler has **no pod, no PVC mount, no user secrets, and — under M6 — no LLM key** (the user's BYOK key lives in *their* pod). Therefore the multi-tenant scheduler **executes nothing**. It becomes a pure **fire → claim → wake → dispatch** service; **the pod executes the job** (agent-runner §0 `:61` "central scheduler wakes the pod to run a turn"). Everything below follows from that single move.

> **Terminology guard — four distinct things, do not conflate.** (1) **the Central Scheduler** (this doc) — the platform component; `N` stateless replicas that decide *when* a job fires and *dispatch* it; **executes no user payload**. (2) **APScheduler** — the in-process Python library (`AsyncIOScheduler`, `CronTrigger`, `IntervalTrigger`, `DateTrigger`) each replica uses as its local *clock*; it is **not** the durable store (the data-plane is) and **not** cross-replica (the Redis+SQLite fire-claim is). (3) **the operator** — the **sole scaler**; the scheduler **requests** a wake by patching the CR (`spec.wake.requestedAt`), exactly like the brain does, and **never** scales a pod (operator §0 O3, §10 `:362`). (4) **the pod** — **executes** every dispatched job (the `claude` turn *and* the http/script/tool-retry tasks); it is the single writer for its account. The rule everything stands on: **the scheduler dispatches; the pod executes; the operator scales.**

> **Verification note.** Every behaviour is cited to the code it forks (`priva/api/...:line`) or the spec that requires it (`blueprint`/`components/*.md:line`). Where this drill **refines** a contract written before the dispatch-to-pod inversion was fully realized — notably the `StartRun`/`FinishRun` ownership split in data-spine §1.7 — it is called out as a concrete owed reconciliation (§3.3, §13).

> **M6 banner (BYOK + metering deferred, locked 2026-06-18 — written-in, not bolted-on).** This doc is authored **M6-correct from the start**. Two blueprint statements about the scheduler are **superseded by M6** and the supersession is baked into §5.3: (a) blueprint §206 / flow-3 §324 — "**budget pre-check** against the proxy's live Redis counter → over-cap `skipped(budget_exceeded)`, no wake" — is **withdrawn**; there is no metering proxy, no live spend counter, and no cap to enforce, so the fire-time gate is an **active-check only** (account enabled? session not busy?), **never a spend check**. (b) `job_run_record.total_cost_usd` is **pod-self-reported and reporting-only** (M6; the pod has the SDK `result` usage, the scheduler never sees a price). The `skip_reason='budget_exceeded'` enum value **survives in the schema** (data-spine §2.11 `:317`) as a **dormant, reversible** forward-compat slot — the scheduler simply never writes it while M6 holds. Re-enabling spend later = restore the pre-check; nothing else in this design changes.

Section map: §0 the contract surface (scattered promises → scheduler, cited) · §1 the current code (fork / strip / invert / lift) · §2 topology & the "executes nothing" invariant · §3 the durable model (jobs · fires · runs; ownership split) · §4 the firing engine (leaderless APScheduler + claim-based exactly-once) · §5 the dispatch model (the inversion: wake + inbox; all four types → pod; active-check; two-layer SKIP) · §6 proactive delivery (the `PushToChannel` resolution) · §7 the agent-facing MCP tools (in-pod, re-pointed) · §8 cancel & stop-a-turn · §9 offboarding hook & history retention · §10 HA, scale & wake-storms · §11 new + changed artifacts · §12 resolved risks · §13 resolved decisions & open items.

---

## 0. The contract surface (what the other specs already require of the scheduler)

This table is the drill's spine — each row is a requirement **cited to where it was locked**, and the §ref is where this doc delivers it.

| # | Required behaviour | Promised in (cited) | Delivered |
|---|---|---|---|
| SC1 | **Owns 100% of cron/interval/one-shot jobs** in the data-plane (not in `.priva.user.yml`) | blueprint dec 9 §85, §205; data-spine §2.11 (`:282` `scheduled_job`), §5 (`:600` YAML→`scheduled_job`) | §3, §4 |
| SC2 | **Exactly-one-fire** via `UNIQUE(job_id, fire_epoch)` (durable authority) + Redis claim lock (fast pre-filter) | blueprint §207, §392; data-spine §2.11 (`:300` `job_fire`), §4 #14 (`:577`), §1.7 (`:102` `ClaimJobFire`) | §4.2 |
| SC3 | **Fire → wake the pod** by **CR-patch** (`spec.wake.requestedAt`); the scheduler is a *waker*, **never** a scaler | blueprint §85, §323-326; operator §0 O3, §3.1, §10 (`:362` "Wake … brain ext_proc, **scheduler**"); data-spine §4 #10 `awake:lock` (`:573`) | §5.2 |
| SC4 | **Dispatch via the durable inbox**: `RPUSH inbox:{account_id}` (T1), the pod **drains and executes** | blueprint flow-3 §325-326; agent-gateway §6 (`:346` RPUSH, `:359` "POD drains inbox FIFO"); agent-runner §11 B7c (`:827`) | §5.1 |
| SC5 | **SKIP-if-busy, not queue** — if the target session's in-pod writer lock is held → `skipped(already_running)`, rely on next fire | blueprint §327, §354 ("**SKIP** adopted … matches the single-writer invariant"); agent-runner §4 (`:343` gate on the in-pod lock); data-spine §4 #11 (session-lock mirror) | §5.4 |
| SC6 | **Active-check at fire time — NOT a budget check (M6)**: a disabled/offboarded account is `skipped` with no wake | blueprint §206/§324 **superseded by M6**; data-spine §2.10 `retention_state` (`:268`) | §5.3 |
| SC7 | **Bounded wake-retry** then misfire policy: **5 attempts, 2s→60s + jitter** | blueprint §328, residual default §423 | §5.5 |
| SC8 | **Cron-storm spreading**: per-fire **jitter** on the CR patch, composing with the operator's wake-concurrency limiter (~20) | blueprint §329, §318; operator §9 / OP5 (`:395`), §9 (`:342` "per-fire jitter: wakers (scheduler) spread CR patches") | §5.5, §10.2 |
| SC9 | **Proactive IM only through the gateway, only to the user's own bound channels** — never an IM endpoint directly | blueprint §208; agent-gateway §8.4 (`:463-465`), §13-7 (`:601` `PushToChannel(account_id, session_uuid, payload)`, scheduler→gateway mTLS) | §6 |
| SC10 | **Offboarding-hooked deactivation**: jobs stop firing when the account leaves `active` | blueprint §394 (Phase 4 "offboarding-hooked deactivation"); operator §8 (offboard FSM) | §9.1 |
| SC11 | **Admin `/scheduler/*` verbs land here** (Control Panel → scheduler), over the internal API | control-panel §0 (`:54` admin `/scheduler/*`), §7 (`:340` "RE-POINT at the central scheduler"); agent-gateway §0 (`:48`) | §2.3, §7.2 |
| SC12 | **Agent-created jobs** (the in-pod `scheduler_*` MCP tools) persist to the data-plane + nudge the scheduler — no shared-FS YAML, no file-command bus | mcp_tools.py `:38,268-269` (today: `JobStore` + `write_command`); data-spine §5 (`:600`) | §7.1 |
| SC13 | **Run history is durable + cross-pod**: `job_run_record` in SQLite (append-only); the detailed event stream is a per-user JSONL the WebUI tails | data-spine §2.11 (`:308`), §199 (append-only, never deleted); run history API (`routers/scheduler.py:237-328`) | §3.3, §9.2 |

**What is genuinely new vs lifted.** *Lifted* (logic survives, re-homed): the trigger model (cron/interval/date), the SKIP-if-busy decision, the misfire grace, the run-history shape, the agent-facing MCP tools. *Net-new*: the **leaderless multi-replica claim** (today is one process), the **CR-patch wake + inbox dispatch** (today is an in-process function call), the **pod as executor** (today the daemon executes). *Deleted*: the in-process `agent_run_events` call, the file-command IPC bus, the YAML job store, the per-host state files.

---

## 1. The current code — fork / strip / invert / lift

Mapping every existing scheduler file to its multi-tenant fate. **STRIP** = deleted; **INVERT** = survives but its execution moves to the pod; **LIFT** = re-homed into this component (re-pointed to the data-plane/Redis); **→ pod** = the code physically moves into the agent-runner runtime.

| Current code | Fate | Why / where it goes |
|---|---|---|
| `AsyncIOScheduler` single daemon (`daemon.py:65-131`, launched `server.sh:455` `nohup python -m api.services.scheduler.daemon`) | **LIFT + multiply** | Becomes the firing engine of **`N` stateless replicas** (§4). APScheduler stays as the per-replica *clock*; the daemon's single-process, single-host assumption is replaced by the leaderless Redis+SQLite claim (§4.2). |
| `scheduled_agent` → `agent_run_events(...)` **in the daemon** (`daemon.py:246-315`: reads `read_user_env`, `bypassPermissions`, `cwd=work_dir/username`, spawns CLI) | **INVERT → pod** | The scheduler **cannot** run an agent (no pod, no PVC, no BYOK key — M6). The job becomes a **dispatched inbox turn**; the **pod** runs `agent_run_events` on drain (§5.1). All of `daemon.py:282-315` deletes from the scheduler. |
| `execute_http_call` / `execute_user_script` (`builtin_tasks.py`), `execute_tool_retry` (`tool_retry.py:128`, reads `McpConfigManager(username)`) | **→ pod** | These execute **user payload** (arbitrary scripts in the user's `cwd`, the user's MCP creds, the user's egress identity). They belong in the **pod runtime**, invoked by the boot-drain handler for non-agent frames (§5.6). The scheduler keeps **only the job *definitions*** (`models/scheduler.py`), never the executors. |
| `job_store.py` — per-user `.priva.user.yml` `scheduled_jobs[]` under `fcntl.flock` (`:25-26,40,70-110`) | **STRIP → data-plane** | Jobs move to `scheduled_job` (data-spine §2.11 `:282`); CRUD via the data-plane RPC (§3.1). The YAML store + its file lock are deleted (data-spine §5 `:600` migration). |
| `shared.py:46-68` `write_command` + `daemon.py:438-482` command-file polling (`reload_user`/`trigger_now`/`remove_user`/`cancel_run`/`tool_retry`) | **STRIP → Redis pub/sub** | The file-command bus existed only to cross the **API-process ↔ daemon-process** gap on one host. With `N` replicas on `N` hosts it is replaced by **Redis pub/sub** (`scheduler:reload`, `scheduler:trigger`) + the data-plane as the source of truth (§4.3). `cancel_run` **re-routes to the pod** (§8). |
| `daemon.py:573-622` state files (`state.json`, `jobs_state.json`, `heartbeat`) read by `routers/scheduler.py:286,368` | **STRIP** | Per-host JSON files read by the co-located API. Replaced by: running/next-fire = data-plane + Redis (§3.3); health = K8s liveness/readiness + a `last_claim` heartbeat key (§10.3). |
| `run_history.py` — daily JSONL `.priva.scheduler.history.{date}.jsonl` + counts sidecar (`:40-41,79-95`) | **INVERT** | The **record** (`job_run_record`) is written to the data-plane — by the **scheduler** at dispatch (`running`/`skipped`) and by the **pod** at completion (`FinishRun`) (§3.3). The detailed **event JSONL** is written by the **pod** to its PVC (`.scheduler-runs/{run_id}.jsonl`) and tailed by the WebUI via the state-reader (§9.2), exactly like a session transcript. |
| `mcp_tools.py:38` `build_scheduler_mcp_server(username)` → `JobStore` + `write_command` (`:268-269`) | **LIFT → pod, re-point** | Stays an **in-pod** MCP server (the agent creating its own jobs), but re-pointed: `get_job_store()`→ data-plane job-CRUD RPC; `write_command("reload_user")`→ `PUBLISH scheduler:reload` (§7.1). |
| `routers/scheduler.py` (`:38` `/api/scheduler`, CRUD/trigger/pause/history/running/cancel) | **SPLIT** | *User* job-CRUD → reached via the brain → the scheduler's internal API (or straight to the data-plane + a reload nudge). *Admin* `/scheduler/*` → Control Panel → this component (SC11). *Live run output* `/running/{run_id}/output` → re-point to the **pod's** run JSONL via the state-reader (§9.2). `lint-script` (`:45`) → stays a pod/Control-Panel utility. |
| `models/scheduler.py` (trigger configs `:11-25`, job configs `:30-67`, `ScheduledJobDefinition` `:72`, `JobRunRecord` `:167`) | **LIFT (shared)** | The Pydantic models are the wire/DB shape; they map 1:1 onto `scheduled_job`/`job_run_record` (data-spine §2.11). The legacy `agent_run`→`scheduled_agent` backcompat (`:84-104`) is preserved through the migration then retired. |
| `config.py` `SchedulerSettings` (`:95` poll 1.0 / heartbeat 5.0 / shutdown 60) | **RESHAPE** | Poll-interval (file bus) is gone; new knobs: replica id, claim-lock TTL (~120s, data-spine §4 #14), reload-channel name, wake-retry schedule (5×, 2s→60s), jitter window (§5.5), reconcile-sweep interval (§10.3). |

---

## 2. Topology & the "executes nothing" invariant

### 2.1 Shape

`N` identical, **stateless, leaderless** replicas (a `Deployment`, not a singleton — the single-daemon era ends). Any replica can fire any job, claim any fire, dispatch any wake — because the only durable state lives in **the data-plane (SQLite, system of record) + Redis (coordination)**, never in a replica. Kill a replica mid-fire: another replica's APScheduler holds the same trigger and will fire the same `(job_id, fire_epoch)`; the claim dedupes to one winner (§4.2). No leader election, so **no failover gap**.

```
        scheduled_job (data-plane, SoR)         every replica loads the SAME active job set
                  │  ListActiveJobs (gRPC/mTLS)        │ into its OWN in-process APScheduler
                  ▼                                     ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │  Central Scheduler — N stateless replicas (leaderless)               │
   │                                                                     │
   │   APScheduler clock ── fire ──▶ [SET NX job:{id}:fire:{epoch}]  ◀── Redis T2  (pre-filter, #14)
   │                                        │ won                         │
   │                                        ▼                             │
   │                                 ClaimJobFire(id, epoch) ─────────▶ data-plane (UNIQUE PK = authority)
   │                                        │ true                        │
   │                  active-check (M6: enabled? NOT budget) ─ skip ─▶ FinishRun(skipped)
   │                                        │ ok                          │
   │              session-lock mirror held? ─ yes ─▶ skipped(already_running)
   │                                        │ no                          │
   │     StartRun(running) ─▶ data-plane    │                             │
   │     RPUSH inbox:{account_id} <frame> ─▶ Redis T1 (durable #1)        │
   │     [awake:lock] CR-patch wake (+jitter) ─────────────────────────▶ operator  (sole scaler 0→1)
   └─────────────────────────────────────────────────────────────────────┘
                                            │                             │
            proactive result delivery       ▼                             ▼
            PushToChannel (mTLS) ◀── gateway ◀── pod EMITS result    POD drains inbox → EXECUTES
                                                  (agent turn / http / script / tool-retry)
                                                  → FinishRun(outcome, self-reported usage M6)
```

**The invariant: a scheduler replica executes no user payload.** No `claude` subprocess, no user script, no user-MCP call, no user-defined HTTP. It holds no PVC mount, no user secret, no BYOK key. This is the security spine: the scheduler is a central, comparatively-privileged component (it can wake any pod and talks to the operator); making it *also* the executor of every tenant's arbitrary code + outbound calls would collapse the pod-per-user isolation into one fat confused-deputy. The pod — already the per-user isolation boundary — executes; the scheduler only decides *when* and *whom to wake*.

### 2.2 What it talks to (all gRPC/mTLS unless noted; data-spine §1.4 lists the scheduler as an allowed data-plane client)

- **data-plane** — `ListActiveJobs`, job CRUD + `SetJobStatus`, `ClaimJobFire`, `StartRun`/`FinishRun`/`ListRuns` (§3.3), `GetRetentionState` for the active-check (§5.3).
- **Redis** — T2 `job:{job_id}:fire:{fire_epoch}` claim pre-filter (#14), `awake:lock:{account_id}` (#10), `awake:set` liveness hint (#9), `lock:session:{session_uuid}` mirror read for SKIP (#11), pub/sub `scheduler:reload` / `scheduler:trigger`; **T1** `inbox:{account_id}` RPUSH (#1) — the only durable dispatch write.
- **operator** — by **CR patch** only (`spec.wake.requestedAt`), via the K8s API; never a direct pod call, never a scale.
- **gateway** — the `PushToChannel(account_id, session_uuid, payload)` seam for proactive delivery (§6) — but, as §6 resolves, the *common* path routes delivery through the **pod's** outbound emit, not a direct scheduler call.
- It **never** calls a pod directly and **never** touches an IM endpoint (SC9; agent-runner §9.6 IM egress denied).

### 2.3 Internal API surface (replaces `routers/scheduler.py`'s host-local file/state coupling)

A small internal HTTP/gRPC API on the replica, reached by the **brain** (on behalf of the user WebUI) and the **Control Panel** (admin, SC11):
- Job CRUD + pause/resume + `trigger-now` (publishes `scheduler:trigger`).
- `ListRuns` / run detail (reads the data-plane; live event tail is delegated to the state-reader/pod, §9.2).
- Admin: cross-account job/fleet views, force-trigger, deactivate-account-jobs (offboarding, §9.1).

---

## 3. The durable model (jobs · fires · runs)

The schema is already designed in **data-spine §2.11** (`scheduled_job`, `job_fire`, `job_run_record`); this section states only what the scheduler *does* with it and the **one ownership refinement**.

### 3.1 Jobs — `scheduled_job` (data-spine `:282`)

One row per job; `job_id TEXT PK` (globally unique — the today `{username}::{job_id}` composite collapses to a bare `job_id` with `account_id` as a column). Polymorphic `trigger_config` (interval|cron JSON) + `job_config` (agent|http|script|retry JSON) stay JSON columns (always read with the parent — normalizing buys only JOINs, data-spine §2.11 note). `status active|paused`; `timezone` IANA (cron honors it). CRUD is data-plane RPC; every mutation **publishes `scheduler:reload`** so all replicas re-sync their APScheduler set (§4.3).

### 3.2 Fires — `job_fire` (data-spine `:300`) — the exactly-once authority

`PRIMARY KEY (job_id, fire_epoch)`. **`fire_epoch` = the *scheduled* fire instant** (epoch seconds of the trigger's planned time, **not** the wall-clock moment a replica happened to process it) — this is what makes all replicas compute the **same** key for the same fire and dedupe to one winner. `claimed_by` = the winning replica id. INSERT-success == ownership (§4.2). Rows are durable proof a fire was claimed; a periodic prune drops `job_fire` rows older than the misfire window (they exist only to dedupe concurrent claims).

### 3.3 Runs — `job_run_record` (data-spine `:308`) — **ownership split (refines data-spine §1.7)**

data-spine §1.7 (`:102`) lists `StartRun`/`FinishRun` under "Scheduler (← `scheduler/*`)" — written before the dispatch-to-pod inversion. The realized split:

- **The scheduler writes the run's *birth*** at dispatch decision time (preserving today's "record running FIRST" race-fix, `daemon.py:260-268`):
  - **skipped before dispatch** (account disabled, or session-lock held) → scheduler writes `job_run_record{status:'skipped', skip_reason}` directly. No pod involved.
  - **dispatched** → scheduler writes `{status:'running', started_at, run_id, account_id, job_id, job_name, session_uuid?}` (so the WebUI shows "running" the instant the fire is claimed), then RPUSHes the inbox frame carrying that `run_id`.
- **The pod writes the run's *outcome*** — on completing the dispatched frame it calls `FinishRun(run_id, status, finished_at, duration_ms, num_turns, total_cost_usd, result_summary, session_uuid)`. `num_turns`/`total_cost_usd` are **pod-self-reported** from the SDK `result` (**M6** — reporting-only). The pod is already a data-plane client (data-spine §1.4), so this is one more RPC it owns.
- **Lost-run reconciliation** — a run dispatched but never finished (pod crash, never woke, evicted mid-drain) leaves a stale `running` row. A scheduler **reconcile sweep** (§10.3) ages out `running` rows past a ceiling → `error('dispatch_lost')`. (`ix_run_status … WHERE status='running'`, data-spine `:327`, makes this scan cheap.)

> **Done (applied this drill): data-spine §1.7** now reads "the scheduler writes `StartRun(running|skipped)`; the **pod** writes `FinishRun(outcome)`" + `ListActiveJobs`. The §2.11 schema was already correct.

---

## 4. The firing engine (leaderless APScheduler + claim-based exactly-once)

### 4.1 Each replica's clock

On boot and on every `scheduler:reload`, a replica calls `ListActiveJobs` and loads each into its local APScheduler via the existing `build_trigger(config, timezone)` (`shared.py:71`): `CronTrigger.from_crontab(expr, tz)` or `IntervalTrigger(...)`. Settings carried over from `daemon.py:161-169`: **`misfire_grace_time` ≈ 60s**, **`coalesce=True`** (a replica behind by >1 period fires once, not N times), **`max_instances=1`** *per replica per job* (cross-replica de-dup is the claim, not this). Triggers are deterministic functions of `(trigger_config, timezone, fire_epoch)`, so every replica independently arrives at the **same** next-fire instant.

### 4.2 The claim — exactly-one-fire across N replicas

At a fire, the replica computes `fire_epoch` from the trigger's scheduled time and runs the **two-stage claim** (data-spine §4 #14, §2.11):

```
1. SET NX job:{job_id}:fire:{fire_epoch} = {replica_id} PX 120000      # Redis T2 — fast pre-filter
   └─ lost?  → another replica is handling this fire; no-op.            #  (most replicas stop here)
2. ClaimJobFire(job_id, fire_epoch)  →  INSERT … UNIQUE(job_id,fire_epoch)  # data-plane — DURABLE authority
   └─ false (row exists)? → no-op (Redis was flushed / raced).         #  the SQLite PK is the real arbiter
   └─ true?  → THIS replica owns the fire → proceed to dispatch (§5).
```

The Redis pre-filter exists so the **single-writer SQLite** sees ~**one** `ClaimJobFire` per fire, not one per replica (data-spine §4 #14: "Scheduler calls `ClaimJobFire` only after winning this Redis pre-filter"). Redis is **never** the authority — an AOF flush at the wrong moment is harmless because the durable `UNIQUE` PK still admits exactly one winner. This is the whole exactly-once story; no leader, no lease.

### 4.3 Reload & trigger-now (replacing the file-command bus)

- **Reload** — any job mutation (user MCP tool §7.1, WebUI CRUD, admin) writes the data-plane then `PUBLISH scheduler:reload {account_id?}`. Every replica re-syncs (full re-list, or per-account delta). **Backstop:** a periodic full reconcile (§10.3) catches a missed pub/sub message — pub/sub is best-effort, the periodic re-list is the correctness floor.
- **Trigger-now** (`daemon.py:503-520` `_handle_trigger_now`) — `PUBLISH scheduler:trigger {job_id}`; the first replica to pick it up runs a synthetic immediate fire with `fire_epoch = now()` through the **same claim dance** (so a duplicate trigger or two replicas racing still fires once). Falls back to the same dispatch path (§5).
- **tool-retry one-shot** (`daemon.py:542-571`) — a failed in-pod tool call that wants a delayed retry creates a one-shot `tool_retry` `scheduled_job` (date trigger) via the data-plane; the scheduler fires it **back into the same pod** (§5.6). Externalizing it to the scheduler (vs an in-pod sleep-loop) is deliberate: the retry survives the pod going idle between attempts.

---

## 5. The dispatch model — the inversion

This is the heart of the drill: what happens after a replica **owns** a fire (§4.2). It mirrors blueprint **flow-3 (scheduled wake, §323-330)**, corrected for M6, and reuses the **cold-start IM machinery** wholesale (blueprint flow-1; agent-gateway §6).

### 5.1 The dispatch path (one diagram, then the gates)

```
OWN the fire (§4.2)
   │
   ├─▶ resolve account_id (job row) + target_session_uuid (job_config; null for non-agent jobs §5.6)
   │
   ├─▶ ACTIVE-CHECK (§5.3, M6: enabled? — NOT budget) ── fail ─▶ FinishRun(skipped, reason='account_disabled'); STOP (no wake)
   │
   ├─▶ SKIP-CHECK (§5.4) — for agent jobs: lock:session:{uuid} mirror held? ── yes ─▶ FinishRun(skipped,'already_running'); STOP
   │
   ├─▶ StartRun(running)  (§3.3 — WebUI sees it immediately)
   │
   ├─▶ RPUSH inbox:{account_id}  <SCHED FRAME>   (Redis T1 #1, durable, coalesce via inbox:dedup #2)
   │       SCHED FRAME = { origin:'scheduler', run_id, job_id, job_type, session_uuid?,
   │                       payload (prompt | http|script|retry config), notify:bool, surface? }
   │
   └─▶ if route:{account_id} (T2 #8) ABSENT (asleep):
            [awake:lock:{account_id} SET NX PX 10s]  →  CR-patch spec.wake.requestedAt (+ per-fire JITTER §5.5)
       else (awake): the pod is already draining; the RPUSH alone delivers (no wake needed).
   │
   ▼
operator scales 0→1 (sole scaler) → POD BOOT (agent-runner §11) → drains inbox FIFO (B7c) → EXECUTES the frame (§5.6)
   → on completion: FinishRun(outcome) + (if notify) emit result → gateway → PushToChannel (§6)
```

The dispatch write is **`inbox` + (maybe) wake** — identical to how the gateway buffers a cold-start IM turn (agent-gateway §6 `:346`). The scheduler adds nothing new to the pod's intake; a `SCHED FRAME` is just an inbound turn whose `origin` is `'scheduler'`. The pod **already** drains the inbox on boot (agent-runner §11 B7c `:827`, non-destructive peek / ack-on-completion), so a crash mid-drain loses no fired job.

### 5.2 Wake = CR-patch, under `awake:lock` (SC3)

The scheduler wakes a sleeping pod **exactly** as the brain does (operator §3.1; agent-gateway §6): take `awake:lock:{account_id}` (Redis #10, `SET NX PX ~10s` — serializes IM + scheduler + a second replica so the CR is patched once), then patch `spec.wake.requestedAt`. The operator (sole scaler) acts on the patch. The lock self-heals (PX ~10s) if a replica dies mid-patch (data-spine §4 #10). The patch is **idempotent** (operator §2 `:144`) — a duplicate wake converges. If the pod is already awake (`route:{account_id}` present, T2 #8), the scheduler **skips the wake entirely** and relies on the inbox RPUSH alone (the awake pod is already draining) — matching agent-gateway §6 `:364`.

### 5.3 The active-check — M6: enabled, not budget (SC6)

Before dispatch the replica checks **only** that the account may run:
- `GetRetentionState(account_id).offboarding_status == 'active'` (data-spine §2.10 `:268`). Anything else (`disabled`/`retained`/`purge_scheduled`/`purged`) → `skipped('account_disabled')`, **no wake** (SC10; §9.1).
- Job `status='active'` is implicit (paused jobs aren't loaded into APScheduler, §4.1).

**No budget/spend gate.** Blueprint §206/§324's "budget pre-check against the live proxy counter → `skipped(budget_exceeded)`" is **withdrawn by M6** — there is no proxy, no live counter, no cap. The `budget_exceeded` skip-reason stays a dormant schema slot (§ M6 banner); the scheduler never writes it while M6 holds. This is the single largest behavioural delta from the blueprint's written flow-3, and it is intentional and reversible.

### 5.4 SKIP-if-busy — two layers (SC5)

Blueprint §354 locks **SKIP, not queue** (matches the single-writer invariant — a session has exactly one writer, the pod; you cannot run two turns on one session). Realized in two layers:
1. **Scheduler pre-filter (cheap, avoids a needless wake).** For an **agent** job, read the session-lock **mirror** `lock:session:{session_uuid}` (Redis #11, T2). Held → `skipped('already_running')`, rely on next fire (`daemon.py:204-215`'s `lock.locked()` check, re-homed to the distributed mirror). Mirror is best-effort (the in-pod lock is authoritative), so this only *saves work*; it is not the correctness gate.
2. **Pod authority (the real gate).** The mirror can be stale, so the **pod** re-checks at drain: when admitting a `SCHED FRAME` for a session whose **in-pod `asyncio.Lock` is held** (agent-runner §4 `:343` "gate on the in-pod lock, not the registry"), the pod records `skipped('already_running')` for that `run_id` rather than queueing. Non-agent jobs (§5.6) take no session lock and skip this gate.

### 5.5 Wake-retry & jitter (SC7, SC8)

- **Wake-retry** (residual default §423): if the CR-patch wake fails (K8s API error, operator unreachable), retry **5×, 2s→60s exponential + jitter**, then apply the misfire policy (record `error('wake_failed')`; the next scheduled fire will try again). The inbox RPUSH already succeeded and is durable (TTL ~1h, #1), so a wake that eventually succeeds within the TTL still drains the turn.
- **Per-fire jitter** (blueprint §318/§329; operator §9 `:342`): the CR patch is delayed by a small random spread (e.g. 0–`jitter_window`s, default a few seconds scaled to fleet size). A cron-aligned storm — every tenant's `0 9 * * *` job firing at 09:00:00 — is thereby smeared across the window instead of patching hundreds of CRs in the same tick. This **composes** with the operator's wake-concurrency limiter (~20 in-flight, OP5 `:395`): jitter spreads the *arrival*, the limiter bounds the *concurrency*, and queued wakes are safe because the turn is already durably buffered in the inbox (operator OP5). Vary jitter by `hash(job_id)` so it's stable per job (no thundering re-sync).

### 5.6 What "execute" means in the pod, per job type (the executors that moved → pod, §1)

The pod's boot-drain handler (agent-runner §11 B7c) dispatches a `SCHED FRAME` by `job_type`:
- **`scheduled_agent`** → run as a normal **session turn**: `agent_run_events(prompt, session_id=session_uuid, …)` — but now under the pod's **real** permission/identity model, **not** the daemon's `bypassPermissions` (a scheduled run is still the user's run in the user's pod; permission policy is the pod's, not a scheduler override). Contends the session's single-writer lock (§5.4). This is the case the inversion is *for*.
- **`http_call`** → `execute_http_call` (lifted from `builtin_tasks.py:35`) runs **in the pod** — outbound from the user's network identity. No session lock (account-level task). *(Efficiency caveat in §13: this is the one type that needs nothing from the pod; running it pod-side is chosen for isolation/egress-consistency, and is the reversible default.)*
- **`user_script`** → `execute_user_script` (`builtin_tasks.py:98`) runs in the pod's `cwd` on the PVC, as the user's uid — the only place it *can* run safely.
- **`tool_retry`** → `execute_tool_retry` (`tool_retry.py:128`) uses `McpConfigManager(account)` + the operator-injected MCP creds (M6 bundle) — only present in the pod.

So **all four executors physically relocate into the agent-runner runtime**; the scheduler keeps their *definitions* only. *(Owed cross-doc note to agent-runner.md: the pod gains a "scheduled-frame executor" branch in the boot-drain — §13.)*

---

## 6. Proactive delivery — the `PushToChannel` resolution

The gateway drill **deferred the scheduler↔gateway orchestration to this drill** (agent-gateway §8.4 `:465`, §13-7 `:601`: "the gateway only exposes the hook"). Here it is resolved.

**The fact pattern.** A scheduled job's output is produced **in the pod** (the run's result + the event JSONL). Some jobs should **notify** a channel (a daily-digest agent bound to the user's WeCom); others are **silent** maintenance (the user reads results in the WebUI run-history later). Delivery must reach **only the user's own bound channel** (SC9, no impersonation) and must **not** make the scheduler stateful per in-flight run.

**Resolution — delivery rides the dispatch frame; the gateway delivers proactively; the scheduler stays fire-and-forget.**
1. The `SCHED FRAME` carries `notify:bool` (+ optional explicit `surface`), derived from the job config at dispatch.
2. The pod runs the turn and **emits its result outbound to the gateway** — exactly as it does for an IM turn (the pod makes **no** IM call itself; agent-runner §9.6). For a scheduler-origin run there is **no live client socket** (the "sender" was the scheduler), so this is precisely the gateway's existing **proactive** case ("any reply *after* a human-feedback wait — the inbound frame's stream is long gone", agent-gateway §8 `:439`).
3. The gateway routes that proactive result to the account's **bound** channel via **`PushToChannel(account_id, session_uuid, payload)`** (§8.4) — same fan-out + chunker + lease owner as interactive replies. The binding is resolved by the gateway from `account_id` (never a scheduler-supplied channel — no impersonation).
4. Silent jobs (`notify=false`) skip steps 2–3 entirely: the pod just writes `FinishRun` + the event JSONL; the WebUI surfaces it.

**Why this shape (and the rejected alternative).** "The scheduler originates proactive IM only through the gateway" (blueprint §208) is satisfied: the scheduler **sets the directive**, the gateway's `PushToChannel` is the **only** IM egress, and neither the scheduler nor the pod touches an IM endpoint. The rejected alternative — *scheduler watches each run to completion, reads the result, then calls `PushToChannel` itself* — is worse on two counts: (a) it makes the scheduler **stateful per in-flight run** and couples its memory to run duration (a 10-minute agent run × a 09:00 storm of hundreds = hundreds of parked delivery-waiters); (b) it adds a pod→data-plane→scheduler→gateway→channel **double-hop** for content the pod already has in hand to emit. Riding the frame keeps the scheduler **stateless after dispatch** — which is the whole point of the leaderless design. The literal `PushToChannel` **caller** is therefore the gateway's own outbound path (triggered by the pod's emit of a scheduler-origin result), which is exactly the seam agent-gateway §8.4 exposes.

---

## 7. The agent-facing MCP tools (in-pod, re-pointed)

### 7.1 `scheduler_*` tools stay in the pod, re-pointed to the data-plane (SC12)

`build_scheduler_mcp_server(username)` (`mcp_tools.py:38`) — the 7 tools (`list/view/create/delete/trigger/pause/resume`) that let the **agent itself** manage the user's jobs — remains an **in-pod** in-process MCP server (it's part of the pod's tool surface). Two re-points, nothing else changes about its UX (incl. the `AskUserQuestion`-before-create rule `:150-151` and the careful "this is durable scheduling, not sub-agent delegation" scoping `:27-35`):
- `get_job_store()` (direct YAML, `:213,268`) → the **data-plane** job-CRUD RPC, scoped to the pod's `account_id` (the pod is single-account, so `username`→`account_id` is implicit — no cross-tenant reach).
- `write_command("reload_user", …)` (`:269`, file bus) → `PUBLISH scheduler:reload {account_id}` (the pod is a Redis client; it cannot write a shared-FS command file in the multi-tenant world).

### 7.2 Admin & user job management (SC11)

`routers/admin.py:293,308` (`/scheduler/*`) and the user WebUI scheduler page (`web/src/api/scheduler.js`, `schedulerStore.js`) re-point at this component's internal API (§2.3), reached through the brain/Control Panel. CRUD writes the data-plane + nudges; `trigger`/`pause`/`resume` map to §4.3 / `SetJobStatus`. The existing run-history + running-tasks views re-point to §9.2.

---

## 8. Cancel & stop-a-turn — routes to the pod, not the scheduler

Today `cancel_run` (`routers/scheduler.py:339` → `daemon.py:532-540`) signals an `asyncio.Event` on the **in-daemon** run. Post-inversion the run executes **in the pod**, so cancelling a scheduled run is **the same operation as stopping any turn** — and the blueprint draws a bright line: **"stop-a-turn = gateway Redis signal, NOT the operator"** (operator §10 `:208` / blueprint §208). So:

- **Cancel a dispatched/running scheduled run** → the **gateway's stop-a-turn Redis signal** to the pod holding that `run_id`/`session_uuid` (agent-gateway §9.4) — **the scheduler is not in this path**. The pod interrupts the run and writes `FinishRun(cancelled)`.
- **Cancel a fire *before* dispatch / un-schedule** → the scheduler's job (pause/delete the `scheduled_job`, §4.3); a not-yet-fired job simply won't fire.

This is a clean consequence of the inversion: once a job is dispatched, it's a pod turn like any other, and its lifecycle controls (cancel) belong to the turn-control plane (gateway→pod), not the scheduler.

---

## 9. Offboarding hook & history retention

### 9.1 Offboarding-hooked deactivation (SC10)

Blueprint Phase-4 (§394) requires jobs to stop when an account is offboarded. Two enforcement points, defense-in-depth:
- **Fire-time active-check (§5.3)** — the authoritative gate: a non-`active` account's fires are `skipped` with no wake, *regardless* of job status. This alone is sufficient for correctness (a job can't run for a disabled account).
- **Eager deactivation** — when the operator's offboard FSM (operator §8) moves an account out of `active`, the Control Panel/operator calls the scheduler's `deactivate-account-jobs(account_id)` (§2.3) → `SetJobStatus(paused)` for that account's jobs + `PUBLISH scheduler:reload` (so APScheduler stops even *trying* to fire them — saves the wasted claim+skip each period). Re-onboarding resumes them.

### 9.2 Run history & retention (SC13)

- **The record** `job_run_record` is in SQLite, **append-only, never deleted from the live table** (data-spine §199 — a single `ts`-indexed table scales to tens of millions of rows; no hash-chain needed for scheduler history). `ListRuns` (cursor-paginated, mirroring `run_history.query_cursor`, `:261`) serves the WebUI/admin history.
- **The detailed event JSONL** (today `.scheduler/runs/{username}/{run_id}.jsonl`, `daemon.py:220-222`) is written by the **pod** to its PVC at `.scheduler-runs/{run_id}.jsonl`. The WebUI "watch a scheduled run live" (`routers/scheduler.py:294-328` offset-tail) re-points to **tailing that file via the state-reader** — identical to how a session transcript is tailed (data-spine §3.8/§3.9). Admin cross-user view = state-reader whole-tree scan (rare, accepted-slower — consistent with the M5 admin-list decision).
- **Retention.** The today daily-cleanup (`daemon.py:429-434`, `run_history.purge_all_users`) purged old run JSONLs. Now: the **record rows persist** (cheap, kept); the prunable artifact is the per-user **event JSONL set** on the PVC, pruned per `history_retention_days` (default 7) — by a **pod-side boot prune** of its own `.scheduler-runs/` (the pod is the only writer of its PVC), with the operator's offboard→purge (operator §8) removing everything on terminal. *(The scheduler itself runs no cross-user FS cleanup — it has no mount.)*

---

## 10. HA, scale & wake-storms

### 10.1 Leaderless = no failover gap

`N` replicas, no leader, no lease on "who runs the clock." Every replica runs every job's trigger; the claim (§4.2) makes execution exactly-once. Losing a replica loses **nothing** — the surviving replicas already hold the same triggers and will fire+claim. This is strictly more robust than the today single daemon (a single point of failure, `server.sh:455`) and avoids a leader-election failover window. Rolling deploys are safe: a draining replica's in-flight *dispatches* are already durable (inbox RPUSH + the claim row); it executes nothing itself to drain.

### 10.2 Wake-storm safety (SC8)

Covered in §5.5: per-fire jitter (scheduler) × wake-concurrency limiter (operator, ~20, OP5). The scheduler's dispatch work is **cheap** (claim + RPUSH + CR-patch — no execution), so a replica can dispatch a large storm quickly; the back-pressure that matters is the operator's scale-up rate, which the limiter owns and the jitter feeds smoothly.

### 10.3 Memory, sharding, health

- **Memory.** Each replica holds **all active jobs** in its APScheduler (in-memory triggers). For hundreds–low-thousands of users × a few jobs each, that's thousands of lightweight triggers per replica — fine. **Escape hatch** (not v1): if the active-job count grows past comfort, shard by `hash(account_id) % R` across replicas (each owns a slice), trading the leaderless "every replica holds everything" simplicity for memory — the claim still backstops correctness during reshard.
- **Reconcile sweep** (the correctness floor under best-effort pub/sub): a periodic (e.g. 60s) full `ListActiveJobs` re-sync catches any missed `scheduler:reload`; the same sweep ages out stale `running` rows (§3.3 lost-run reconciliation) and prunes old `job_fire` rows (§3.2).
- **Health.** Replaces the today heartbeat-file (`daemon.py:575`): K8s liveness/readiness probes + a Redis `scheduler:replica:{id}:last_claim` heartbeat for the admin fleet view. A replica with a stale heartbeat is shedding load, not corrupting state (the claim is per-fire).

---

## 11. New + changed artifacts (code deltas)

| Artifact | Change |
|---|---|
| `services/scheduler/daemon.py` | **Re-author** as a replica engine: keep APScheduler load/build-trigger/misfire (`:140-170`); **delete** the in-process executor branch (`:282-348`), the file-command bus (`:438-571`), the state-files (`:573-622`), the in-daemon drain-of-runs (`:626-671`). Add the two-stage claim (§4.2), the dispatch path (§5.1), pub/sub reload/trigger (§4.3), wake-retry+jitter (§5.5), the reconcile sweep (§10.3). |
| `services/scheduler/job_store.py` | **Delete** (YAML+fcntl). Replaced by data-plane job-CRUD RPC. |
| `services/scheduler/run_history.py` | **Reduce** to a data-plane `ListRuns` client + cursor helper; the JSONL writer/purger moves to the pod (§9.2). |
| `services/scheduler/shared.py` | **Delete** `write_command`/paths (file bus); **keep** `build_trigger` (`:71`). |
| `services/scheduler/builtin_tasks.py`, `tool_retry.py` | **Move → agent-runner runtime** (the pod's scheduled-frame executor, §5.6). Unchanged logic; new home. |
| `services/scheduler/mcp_tools.py` | **Stays in the pod**; re-point `get_job_store`→data-plane RPC, `write_command`→`PUBLISH scheduler:reload` (§7.1). |
| `routers/scheduler.py` | **Split** (§1): user CRUD → internal API via brain; admin → Control Panel; live-output → state-reader tail. |
| `models/scheduler.py` | **Keep** as the wire/DB shape; add `origin`/`notify`/`run_id` to the dispatched-frame model; retire the `agent_run` backcompat post-migration. |
| **NEW** `scheduler/engine.py` (claim + dispatch), `scheduler/wake.py` (awake-lock + CR-patch + retry/jitter), `scheduler/api.py` (internal §2.3) | The net-new multi-replica machinery. |
| **NEW** K8s: scheduler `Deployment` (`N` replicas) + SA/RBAC (data-plane client + **CR-patch** on `AgentTenant`, no scale) + NetworkPolicy + the `scheduler:reload`/`scheduler:trigger` channels | Deployment manifests. |
| `pod` boot-drain (agent-runner §11 B7c) | **Add** the scheduled-frame branch: dispatch by `job_type` to the relocated executors / `agent_run_events`; write `FinishRun`; emit-if-`notify` (§5.6, §6). |
| Migration (data-spine §5) | `.priva.user.yml scheduled_jobs[]` → `scheduled_job`; `.priva.scheduler.history.{date}.jsonl` → `job_run_record` (already specified data-spine `:600-601`). |

---

## 12. Resolved risks

| # | Risk | Sev | Resolution |
|---|---|---|---|
| SR1 | **Double-fire** — `N` replicas each fire the same job | blocker | Two-stage claim: Redis `job:*:fire:*` pre-filter + durable `UNIQUE(job_id, fire_epoch)` PK; the SQLite PK is the authority, Redis only spares the DB N-1 claims (§4.2; data-spine §4 #14). |
| SR2 | **Scheduler as confused-deputy / SSRF** — running every tenant's scripts + HTTP + MCP centrally | blocker | The scheduler **executes nothing** (§2.1); all four executors run in the per-user pod (§5.6). The central component only fires/claims/wakes. |
| SR3 | **Lost fire on replica death** mid-dispatch | major | Dispatch is durable before any execution: the claim row + the `inbox` RPUSH (T1 AOF) both persist; another replica/the pod completes; wake-retry covers a failed patch within the inbox TTL (§5.5). |
| SR4 | **Cron storm melts the operator** (every 9am job at once) | major | Per-fire jitter spreads CR patches × operator wake-limiter bounds concurrency; queued wakes safe (turn already buffered) (§5.5, §10.2; operator OP5). |
| SR5 | **Two turns on one session** (scheduled run races a live user turn) | major | SKIP-not-queue, two layers: scheduler mirror pre-filter + the **pod's authoritative in-pod lock** at admit (§5.4; agent-runner §4). Single-writer invariant preserved. |
| SR6 | **Over-budget run** (the blueprint's flow-3 concern) | — | **Dissolved by M6**: no spend, no cap, no pre-check — the gate is active-only (§5.3). Reversible: the dormant `budget_exceeded` slot + a restored pre-check re-enable it. |
| SR7 | **Wakes a pod for a trivial job** (e.g. a 200ms healthcheck `http_call`) | minor | Accepted for v1 (isolation > efficiency); flagged as the one reversible optimization candidate (scheduler-direct lightweight HTTP **iff** an egress-controlled path exists — moot while egress is deferred, M6) (§13). |
| SR8 | **Missed `scheduler:reload`** (pub/sub is best-effort) → a deleted job keeps firing / a new job never loads | minor | Periodic full reconcile re-list is the correctness floor; pub/sub is only the low-latency path (§4.3, §10.3). |
| SR9 | **Stale `running` record** (pod died after dispatch, never finished) | minor | Reconcile sweep ages `running` past a ceiling → `error('dispatch_lost')` (`ix_run_status` makes it cheap) (§3.3, §10.3). |
| SR10 | **Proactive delivery to the wrong channel** (impersonation) | major | Delivery target is the gateway-resolved **binding** from `account_id`, never a scheduler-supplied channel; `PushToChannel` is the only IM egress (§6; SC9). |
| SR11 | **`trigger-now` double-runs** (two replicas pick up the pub/sub) | minor | Goes through the same `(job_id, fire_epoch=now)` claim → one winner (§4.3). |

---

## 13. Resolved decisions & open items

**Locked this drill (2026-06-18):**
1. **The scheduler executes nothing; the pod executes every job** (the inversion). All four executors relocate to the pod runtime (§2.1, §5.6).
2. **Leaderless `N` replicas**; exactly-once via Redis-pre-filter + durable `UNIQUE(job_id, fire_epoch)`; no leader election (§4.2, §10.1).
3. **Wake = CR-patch under `awake:lock`**, identical to the brain; the scheduler is a waker, never a scaler (§5.2).
4. **Dispatch = `inbox` RPUSH (+ wake if asleep)**; the pod drains and executes; reuses cold-start IM machinery wholesale (§5.1).
5. **M6 active-check, not budget pre-check** — blueprint §206/§324 superseded; `budget_exceeded` kept dormant/reversible (§5.3).
6. **SKIP-not-queue, two layers** (scheduler mirror + pod authoritative lock) (§5.4).
7. **Proactive delivery rides the dispatch frame; the gateway delivers via `PushToChannel`; the scheduler stays fire-and-forget** — resolves the seam agent-gateway §8.4 deferred (§6).
8. **Cancel-a-scheduled-run = the gateway stop-a-turn signal to the pod, not the scheduler** (§8).
9. **Run-record ownership split**: scheduler writes birth (`running`/`skipped`), pod writes outcome (`FinishRun`, self-reported usage) (§3.3).
10. **Pub/sub reload + periodic reconcile floor** replaces the file-command bus (§4.3, §10.3).

**Owed (small, filed):**
- **data-spine §1.7 reword — DONE (applied this drill):** `FinishRun` attributed to the pod; `StartRun(running|skipped)` + `ListActiveJobs` added to the scheduler's list (§3.3). The §2.11 schema was already correct.
- **agent-runner.md note** — the boot-drain (§11 B7c) gains a scheduled-frame branch that hosts the relocated `builtin_tasks`/`tool_retry` executors + `agent_run_events` for `scheduled_agent` (§5.6); and the pod owns the `.scheduler-runs/` JSONL writer + retention prune (§9.2). Fold into the next agent-runner deep pass.
- **This is the LAST component drill.** The **deep M6 body cleanup is now DONE (2026-06-18)** — the spend-machinery + egress-gateway bodies in `agent-runner.md`, `data-spine.md`, `agent-gateway.md`, and `multi-tenant-platform.md §3/§4` are rewritten M6-correct (the blueprint §2 decisions table / system diagram / §5-7 stay under the supersession banner, as with M1/M2/M5). The only remaining owed item is the **channel-connector** sub-pass flagged in agent-gateway §4.4.

**Open (deferred, reversible):**
- **OS1 — `http_call` in the scheduler.** The one job type that needs nothing from the pod. Running it pod-side (v1) costs a wake for a trivial outbound call. A future scheduler-side lightweight HTTP executor is viable **iff** it routes through the controlled egress path — which is **deferred under M6** (no controlled path exists yet), so v1 keeps it in the pod (SR7).
- **OS2 — Sharded replicas.** Leaderless "every replica holds every trigger" is simple and v1-correct; shard by `hash(account_id)` only if the active-job count outgrows per-replica memory (§10.3). The claim backstops correctness during a reshard.
- **OS3 — Re-enabling spend.** When M6 is lifted, restore the fire-time budget pre-check (§5.3) and write the `budget_exceeded` skip-reason; nothing else in this design changes.

---

> **Status.** Central Scheduler drilled (solo/conversational, M6-correct). With this, **all platform components are drilled**: data-spine, agent-runner, agent-gateway, control-panel, operator, scheduler — and the metering proxy is **dropped** (M6). The scheduler is the **last** component drill, and the **deep M6 body cleanup is DONE (2026-06-18)**. The only remaining platform work is the **channel-connector** sub-pass (agent-gateway §4.4).
