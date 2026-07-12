# Scheduler — Implementation Design (rev 2 · 2026-07-12)

> Status: design complete pending review · implementation not started.
> This is **Phase 4a** (the scheduler half of `migration_progress/phase-4.md`; the channel-connector is 4b).
> Relationship to `architecture/components/scheduler.md` (2026-06-18 drill): the drill's decision framework
> **stands** (the inversion, leaderless exactly-once, SKIP-not-queue, run-record ownership split); this doc
> re-grounds its *transport idioms* in the shipped platform — Postgres default, **no Redis**, the proven
> EPP/operator wake spine — and freezes the v1 build. Produced in the 2026-07-12 re-evaluation session
> (decision rounds 1–3 all closed same day — no open decisions remain).

---

## §1 Problem, scope & stage findings

**Problem:** the platform has no scheduler. The monolith daemon (`priva/api/services/scheduler/daemon.py`)
still runs agent jobs **in-process** (`daemon.py:298-315`, `bypassPermissions`, user env) — the exact
coupling Phase 4 exists to sever. The user SPA already advertises the feature (disabled sidebar item
`Sidebar.jsx:904`, intro scene); the legacy scheduler UI was deleted ("Phase-4 deferred, no backend").

**Findings that shaped everything (2026-07-12 audit):**

| # | Finding | Consequence |
|---|---|---|
| 1 | The dataplane scheduler domain is **already built**: `scheduled_job` + `job_run_record` tables (SQLite+PG twins, `schema.py:59-98`), full in-process `SchedulerService` (`service.py:245-417`), `scheduler.proto` with 10 RPCs + generated stubs, `SchedulerClient` protocol (`client.py:193-217`); monolith `job_store.py`/`run_history.py` already re-pointed to it | v1 is mostly **wiring + one new deployable**, not schema design |
| 2 | **No `job_fire` table, no `ClaimJobFire` RPC** — the exactly-once authority was never scaffolded (only a Redis key-builder string) | Build it (§5) — work item #1 |
| 3 | **Redis is not deployed and not connected anywhere** (`redis_catalog.py` docstring; `agent-runner/app.py:160`); the drill's inbox / busy-mirror / pub-sub / claim-pre-filter are catalog strings | v1 uses **no Redis**: Postgres-only claim, reconcile re-list instead of pub/sub, wake+dial instead of inbox (§3 D1/D5/D6) |
| 4 | The **wake spine is real and battle-tested**: `AgentTenant spec.wake.requestedAt` (CRD), kopf operator scales `ar-{account_id}` 0↔1 (`reconcile.py:93-139`, idle-sweep `:236-248`), control-panel EPP `wake_and_wait` → podIP (`provisioner.py:451-537`) | The scheduler becomes a **second caller** of this spine — a clock instead of a browser |
| 5 | The runner's **`run_registry` is the detach/attach backbone** (`run_registry.py:1-19`): runs are registry-owned asyncio tasks that survive socket death, hold an `activity` slot (idle-sweep can't sleep the pod mid-run), buffer seq-numbered events (4000) for `attach` replay; `WS /ws/run` accepts `init` **or `attach`** (`agent.py:1032`); `GET /sessions` + `GET /sessions/running` feed the sidebar dots (`sessionStatusStore.js`) | Scheduled runs executed **through `run_registry`** inherit live-watch, replay, stop, and pod-keepalive for free (US-3/US-4) |
| 6 | Postgres is the data-spine default since 2026-07-11 (`config.py:146`); SQLite is legacy opt-in | Claim = plain `INSERT … ON CONFLICT DO NOTHING`; the drill's Redis pre-filter (which existed to spare single-writer SQLite) is dropped |
| 7 | Canonical job type renamed `scheduled_agent`→`agent_run` (backcompat validator `models/scheduler.py:89-93`); user-facing CRUD models already exclude `ToolRetryConfig` | v1 type fence already half-enforced by the models |
| 8 | The uncommitted **hooks-policy diff touches the same files** (schema/repo/service/server + dataplane client/converters + control-panel app) | **Land hooks-policy first**, then start this |

**In scope (v1):** `agent_run`, `http_call`, `user_script` jobs · user CRUD (WebUI + the 7 in-pod MCP tools) ·
leaderless firing + Postgres exactly-once claim · wake+dial dispatch · run history + live watch via sessions ·
admin per-account oversight · migration of monolith jobs.
**Out (parked, §15):** Redis inbox durability upgrade · IM notify (`PushToChannel`) · detached-approval
permission inbox (§11.2, needs channel-connector) · `tool_retry` redesign · pre-warm-before-fire · spend/budget
gate (M6) · replica sharding.

---

## §2 User stories (normative v1 behaviour)

### US-1 · "Brief me every morning at 9" — create from the Scheduler page

```
 YOU          Scheduler page → [+ New job] → name · prompt · cron 0 9 * * 1-5 ·
              tz Asia/Shanghai · model (default) → card shows ACTIVE · NEXT Mon 09:00
 UNDERNEATH   runner /api/sandbox/scheduler/jobs → dataplane CreateJob → scheduled_job row.
              Every scheduler replica re-lists ≤30s and arms an APScheduler
              CronTrigger(expr, tz). The DB row is the truth; the trigger is only its
              in-memory shadow — kill any replica, nothing is lost.
```

### US-2 · "Hey agent, do this every Friday" — schedule from chat

The agent drafts the job, confirms with you, then calls `scheduler_create_job` — one of the 7 in-pod MCP
tools, the **only** sanctioned self-scheduling path (generic cron tools are disallowed by runner tool
policy). Same dataplane write as US-1; the pod is single-account, so the tool cannot touch other tenants.

### US-3 · Monday 09:00 — it runs while you sleep (the dispatch, exactly)

**Who initiates the HTTP call: the claim-winning scheduler replica** (`dispatch.py`, httpx).
**Connection semantics: fire-and-forget with an admission handshake** — the POST waits only for
*admission* (~ms), never for the run (minutes). Nobody holds a socket across a run.

```
 you: asleep · pod: scaled to ZERO
 09:00:00  replicas A+B fire → INSERT job_fire(job, 09:00) → A wins      exactly-once
 09:00:00  A: account active? ✓ · latest run of this job still running? ✗
 09:00:00  A: StartRun r-311 = running          ← history shows RUNNING instantly
 09:00:01  A: patch AgentTenant wake ─► operator scales ar-you 0→1, poll Ready

 scheduler replica A                  runner pod ar-you
 ───────────────────                  ────────────────────────────────────────────
 POST /scheduled-run ───────────────► 1 verify platform runner-JWT
   {run_id, job_type, job_config…}    2 idempotent by run_id (re-POST → 202 again)
                                      3 overlap (job live here)?      → 409 skip
                                      4 quota slot free?              → 429 (D16)
                                      5 run_registry.register(RunRecord)
                                          · activity slot → idle-sweep can't
                                            sleep the pod mid-run
                                          · seq-numbered event buffer starts
                                      6 asyncio task spawned DETACHED
 ◄──────────── 202 accepted ───────── 7 respond — the connection CLOSES here
     scheduler now stateless about this run (no parked waiters — the drill §6
     rejected scheduler-watches-run: a 09:00 storm × 10-min runs = hundreds of
     held sockets)
                                      8 NEW session s-88, origin=scheduler,
                                        bypassPermissions + ENFORCED admin hooks
                                        fire (D2) · AskUserQuestion disallowed
                                        (D14) · capped 30 min / 50 turns (D14)
 …minutes later…                      9 POD writes the outcome itself:
                                        FinishRun(success · turns · summary ·
                                        session=s-88)   [dataplane gRPC]
 09:0x  pod idle past grace → operator scales back to 0
```

Retry rules: **connection-level failures only** (refused / timeout-before-response), 5× 2s→60s + jitter;
run_id idempotency makes retry-after-ambiguous-timeout safe. `409 job_overlap` → immediate
`skipped(already_running)`. `429 concurrency_cap` → re-try admission ≤2 min, then
`skipped(concurrency_cap)` (D16). Exhausted wake/dial → `error(wake_failed)`.

### US-4 · Seeing it live — the UI attaches; it never needed to own the socket

The browser has no WS to a scheduler-started session — by design it doesn't need one. Discovery is
**poll-based**, live view is **attach-based**, both already shipped (finding 5):

```
 ① DISCOVER   login → pod wakes (EPP) → SPA fetches
              GET /sessions          → JSONL scan → the ⏰ row appears in the sidebar
              GET /sessions/running  → run_registry snapshot → purple RUNNING dot
 ② WATCH      click the row → WS /ws/run {type:"attach", session_id, since_seq:0}
              → RunRecord replays its buffer (≤4000 events) → follows live → RUN_END
 ③ STOP       explicit abort frame on that WS (socket close ≠ abort, by design)
              → FinishRun(cancelled); next fire unaffected
 ④ MISSED IT  terminal records stay attachable 600s; afterwards the session
              transcript is the permanent record — open like any old session
```

Normative consequence: the scheduled executor **must** create its run as a registry-owned `RunRecord`
exactly like `ws_run` does — required anyway for the activity slot.

### US-5 · Yesterday's run still going at today's 09:00

Claim won → latest run of **this job** still `running` → `skipped(already_running)`, **no wake**.
New-session-per-fire means a scheduled run never collides with your live chat — the only collisions are
the job with itself (this story) and the account quota (D16).

### US-6 · Run now · pause · delete

`[Run now]` → scheduler `/internal/trigger/{job}` → synthetic fire (`fire_epoch=now`) through the same
claim (double-click safe). `[Pause]` → `status=paused`, replicas dis-arm ≤30s. `[Delete]` → typed-name
confirm → job gone, run history kept (`job_id` FK `SET NULL`).

### US-7 · The morning it breaks

Agent error → `FinishRun(error, message)`. Pod never came up → `error(wake_failed)` after retries.
Pod died mid-run → stale `running` aged out by the sweep → `error(dispatch_lost)`. Ran too long →
runner kills at the cap → `error(timeout | max_turns)` (D14). The job stays armed; tomorrow tries again.
v1 has no IM alert — failures are red rows in history.

### US-8 · The scheduler itself was down at 09:00

Back within the 60 s misfire grace → `coalesce=True` fires **once**, late. Back later → that fire is
missed by design (SKIP philosophy); tomorrow untouched; nothing ever double-runs.

### US-9 · Admin oversight & the kill switch

Account detail → Scheduler tab: jobs, runs, `[Pause all]`. Disabling an account makes every fire skip at
the active-check (no wake); offboard/purge CASCADE-deletes jobs + records.

---

## §3 Decisions (rounds 1+2, all closed 2026-07-12)

| # | Decision | Ruling |
|---|---|---|
| D1 | Dispatch path | **Wake + dial** (Option B): CR-patch wake → poll ready → POST to the pod. Behind a `Dispatcher` interface so the Redis inbox (Option A) can replace it later without touching the firing engine |
| D2 | Unattended permission mode | **`bypassPermissions` + enforced admin hooks still fire** (the hooks-policy layer is the guardrail). Per-job override parked |
| D3 | Session visibility | Scheduled runs are real sessions, **shown in the sidebar marked ⏰** (origin=scheduler); live watch + stop reuse existing machinery |
| D4 | v1 job types | `agent_run` + `http_call` + `user_script`; `tool_retry` stays a dormant enum (imported rows are paused) |
| D5 | Exactly-once | **Single-stage Postgres claim**: `INSERT INTO job_fire (job_id, fire_epoch, …) ON CONFLICT DO NOTHING`; rowcount 1 = own the fire. No Redis pre-filter |
| D6 | Job-set propagation | No pub/sub. Every replica **re-lists `ListActiveJobs` every ~30s**. `trigger-now` = scheduler internal API |
| D7 | Cancel a running job | The runner's existing run-abort (it's a session). The scheduler only owns future fires (pause/delete) |
| D8 | Active-check at fire | `account.status == 'active'` via the dataplane (no `GetRetentionState`; CASCADE handles purge) |
| D9 | SKIP-if-busy | Scheduler checks `get_latest_run(job_id).status=='running'` before dispatch; the runner 409s as backstop |
| D10 | Session semantics | **Each fire opens a new session** (never continues an existing one) |
| D11 | Skip/loss reasons | Recorded in `job_run_record.error_message` (`already_running` · `account_disabled` · `concurrency_cap` · `wake_failed` · `dispatch_lost` · `timeout` · `max_turns`). **No schema change** |
| D12 | Admin surface | Per-account drill-down reusing `ListJobs(account)`/`ListRuns(account)` — no new cross-account RPCs (M5 "admin-list accepted-slower" precedent) |
| D13 | Dispatch protocol | **Admission handshake, then fire-and-forget**: POST returns 202 after registering a registry-owned `RunRecord`; idempotent by `run_id`; the pod writes `FinishRun` itself; the scheduler holds no per-run state (US-3) |
| D14 | Runaway guard | Runner enforces **wall-clock timeout (default 30 min) AND max_turns (default 50)** on scheduled agent runs, both per-job overridable (`AgentRunConfig.timeout_seconds/max_turns`) → kill → `error(timeout|max_turns)`. `AskUserQuestion` is **disallowed** in scheduled runs (a detached run would park forever awaiting an answer) |
| D15 | Transcript retention | **Pod-side boot prune deletes scheduler-origin session JSONLs older than `history_retention_days` (default 7)** — legacy parity. Run records + summaries persist forever; pruned runs lose only the "open session" link |
| D16 | Account concurrency | Scheduled runs **count against `quota.concurrency`**; at cap the runner returns **429** and the scheduler **retries admission ≤2 min (backoff)**, then records `skipped(concurrency_cap)`. Protects interactive latency without spurious skips |

---

## §4 v1 architecture

```
            scheduled_job (data-spine · Postgres)
                    │ ListActiveJobs (gRPC, every ~30s)          every replica arms the SAME set
                    ▼
  ┌─ services/scheduler · N leaderless replicas (v1: N=1, N is a knob not a code change) ─┐
  │  APScheduler clock: CronTrigger/IntervalTrigger(tz) · misfire_grace=60 · coalesce ·   │
  │  max_instances=1 · fire_epoch = int(scheduled_run_time.timestamp())                   │
  │      │ fire                                                                           │
  │      ▼                                                                                │
  │  ClaimFire: INSERT job_fire(job_id, fire_epoch, replica) ON CONFLICT DO NOTHING       │
  │      │ won (losers no-op — this alone is the exactly-once story)                      │
  │      ▼                                                                                │
  │  active-check (account.status) ── fail ─▶ record_run(skipped, account_disabled), STOP │
  │      ▼                                                                                │
  │  overlap-check (latest run running?) ── busy ─▶ record_run(skipped, already_running)  │
  │      ▼                                                                                │
  │  StartRun(running, run_id)              ← run history shows RUNNING instantly         │
  │      ▼                                                                                │
  │  Dispatcher.dispatch(frame)   ◀── interface; v1 impl = WakeDial, future impl = Inbox  │
  │    1. patch AgentTenant spec.wake.requestedAt (+ per-fire jitter, hash(job_id))       │
  │    2. poll CR status.podIP / Ready   (the operator is the sole scaler)                │
  │    3. POST http://ar-{acct}.{ns}.svc/…/scheduled-run  (minted runner JWT)             │
  │       202 → done (stateless) · 409 → skipped(already_running) ·                       │
  │       429 → re-admit ≤2 min → skipped(concurrency_cap) (D16)                          │
  │    conn-fail retry 5× 2s→60s+jitter → exhausted ⇒ FinishRun(error, wake_failed)       │
  │                                                                                       │
  │  reconcile sweep (~60s): stale 'running' past ceiling → error(dispatch_lost) ·        │
  │  prune job_fire rows >24h · full re-list (D6)                                         │
  └───────────────────────────────────────────────────────────────────────────────────────┘
           │                                                        ▲
           ▼                                                        │ FinishRun(outcome)
     POD ar-{account_id}: /scheduled-run admits → registry-owned RunRecord → executes
       agent_run   → NEW session, origin=scheduler, bypassPermissions + ENFORCED hooks,
                     no AskUserQuestion, capped 30min/50 turns (D2/D14)
       http_call   → ported executor (outbound from the pod's identity)
       user_script → ported executor (pod cwd, uid 10001, timeout)
```

**Invariants kept from the drill:** the scheduler **executes no user payload** (no PVC, no BYOK key, no
scripts); the operator is the **sole scaler** (the scheduler only patches the wake field); the pod is the
single writer for its account; run-record ownership split (scheduler writes *birth*, pod writes *outcome*).

---

## §5 Data model & proto deltas (the only schema work)

**New table `job_fire`** — both `schema.py` + `schema_pg.py`, added to `TABLES` and `copy_to_pg._TABLES`:

```sql
CREATE TABLE IF NOT EXISTS job_fire (
  job_id     TEXT    NOT NULL REFERENCES scheduled_job(job_id) ON DELETE CASCADE,
  fire_epoch INTEGER NOT NULL,        -- the trigger's SCHEDULED instant (epoch s), not wall clock
  claimed_by TEXT    NOT NULL,        -- replica id (pod name)
  claimed_at TEXT    NOT NULL DEFAULT {NOW},
  PRIMARY KEY (job_id, fire_epoch)
) STRICT                              -- PG twin: BIGINT fire_epoch, no STRICT
```

Rows exist only to dedupe concurrent claims; the reconcile sweep prunes rows older than 24 h.
`fire_epoch` = scheduled time so every replica computes the **same key** for the same fire.

**Existing tables unchanged.** `job_run_record` already has `session_id`, the 5-status CHECK, and the three
indexes (incl. partial `ix_run_status WHERE status='running'` — makes the stale-run sweep cheap). Skip/loss
reasons ride `error_message` (D11). The table has **no** `total_cost_usd` column (M6-correct); the pydantic
field stays dormant.

**Model delta:** `AgentRunConfig` gains `timeout_seconds: int = 1800` and `max_turns: int = 50` (D14) —
JSON `job_config` column, no DDL change.

**Proto delta (`scheduler.proto`)** — two RPCs, then `protos/gen.sh`:

```proto
message ClaimFireRequest { string job_id = 1; int64 fire_epoch = 2; string claimed_by = 3; }
message ClaimFireResponse { bool claimed = 1; }
service SchedulerService {
  // …existing 10 RPCs unchanged…
  rpc ClaimJobFire(ClaimFireRequest) returns (ClaimFireResponse);
  rpc RecordRun(Run) returns (Run);   // one-shot full-record write (skipped records; migrator parity —
}                                     // mirrors the existing in-process service.record_run)
```

**Serving wiring (the "deferred to Phase 4" gap, closed here):**
`repo.py` `fire_claim`/`fire_prune_before` (Sqlite + Pg) → `service.py` `claim_fire` →
`server.py` `_SchedulerServicer` + `_job_pb`/`_run_pb` builders + register in `build_server` →
`converters.py` `job_from_pb`/`run_from_pb` → `grpc_client.py` replace `_SchedulerDeferred` with a real
`_Scheduler` stub wrapper. Follow the hook_policy 9-file pattern file-for-file.

---

## §6 `services/scheduler` (new deployable)

```
services/scheduler/src/priva_scheduler/
  entry.py / __main__.py   settings · dataplane client (grpc transport) · start engine + api
  engine.py                30s re-list → diff by (job_id, updated_at) → arm/disarm APScheduler
                           triggers (fork build_trigger from priva/api/services/scheduler/shared.py:71);
                           on_fire pipeline exactly as §4
  dispatch.py              Dispatcher protocol + WakeDialDispatcher (D1, D13, D16 admission retry)
  wake.py                  AgentTenant patch + status.podIP/Ready poll (lift the provisioner.py:451-537
                           pattern; kubernetes client, in-cluster config)
  reconcile.py             stale-running ageout (ceiling default 2h — safely above the D14 run caps) ·
                           job_fire prune · metrics gauges
  api.py                   FastAPI internal :8082 — POST /internal/trigger/{job_id} (synthetic fire,
                           fire_epoch=now, same claim dance → double-trigger safe) · GET /healthz
  config.py                reshape priva_common SchedulerSettings: relist_seconds=30, sweep_seconds=60,
                           running_ceiling_seconds=7200, wake_retry(5, 2→60s), jitter_window_s=5,
                           admission_retry_window_s=120, runner_port, namespace
```

APScheduler settings carried from the monolith (`daemon.py:161-169`): `misfire_grace_time=60`,
`coalesce=True`, `max_instances=1` *per replica per job* (cross-replica dedupe is the claim, not this).
Replica id = `HOSTNAME`. The scheduler holds **no state** outside the DB — kill/roll replicas freely.

**K8s:** `Deployment` (replicas 1, no PVC) + ServiceAccount + namespaced Role: `get/list/watch/patch`
`agenttenants` + `agenttenants/status` **read** (waker, never scaler — no pod/exec verbs) + `envFrom
priva-shared-secret` (mint runner JWTs the same way control-panel does, `app.py:207`) +
`PRIVA_DATASPINE__TRANSPORT=grpc`, `…GRPC_DSN=data-spine:50051`. Liveness/readiness = `/healthz`.
Dockerfile mirrors `deploy/docker/data-spine.Dockerfile`.

---

## §7 Runner-side (the executor half)

**New endpoint** `POST /api/sandbox/agent/scheduled-run` (service-authed with the platform runner JWT):

```
body  { run_id, job_id, job_name, job_type, job_config, model?, permission_mode }
202   admitted — registry-owned RunRecord created (D13); run continues detached;
      activity slot keeps the pod awake; idempotent re-POST by run_id → 202 again
409   this job_id already has a live run in this pod (D9 backstop)
429   account concurrency cap reached (D16) — scheduler may re-admit ≤2 min
```

Executors by `job_type`:
- **`agent_run`** — open a **new session** through the same internals as `ws_run` (registry-owned task,
  event buffer → `attach` works, US-4), with `permission_mode='bypassPermissions'` (D2 — enforced admin
  hooks fire via the hooks-policy builder), **`AskUserQuestion` removed from the tool surface**, and the
  D14 caps enforced (wall-clock kill + `max_turns`). Session metadata tagged `origin='scheduler'`,
  `job_id`, `job_name`, `run_id` → sidebar ⏰ (D3). On completion:
  `FinishRun(status, duration, num_turns, result_summary≈first 200 chars, session_id)`.
- **`http_call` / `user_script`** — port `builtin_tasks.py` (`execute_http_call:35`,
  `execute_user_script:98`); run under the pod's identity (uid 10001, pod cwd, config timeouts); no
  session (`session_id` null); summary/error → `FinishRun`.
- Any admission/execution exception → `FinishRun(error, message)` — the runner owns the outcome write;
  the scheduler's sweep only catches runs the pod never acknowledged.

**Retention (D15):** a pod boot task prunes scheduler-origin session JSONLs older than
`history_retention_days` (default 7). Run records persist in data-spine forever.

**MCP tools (agent self-scheduling):** port `mcp_tools.py` (7 tools, `build_scheduler_mcp_server`) into the
runner as an in-process SDK MCP server registered in the options builder. Re-points: `get_job_store()` →
the dataplane scheduler client (pod is single-account — no cross-tenant reach); `write_command("reload_user")`
→ **delete** (the 30 s re-list covers propagation, D6). The confirm-before-create UX rule
(`mcp_tools.py:150-151`) and the "durable scheduling ≠ subagent delegation" scoping (`:27-35`) carry over
verbatim. Create/update unions already fence to the three v1 types.

---

## §8 API surface

**User (runner, `/api/sandbox/scheduler/*`, session-authed):**
`GET /jobs` (list + server-computed `next_run_time` — APScheduler trigger math; the
`ScheduledJobResponse.next_run_time` field already exists) · `POST /jobs` · `PUT /jobs/{id}` ·
`DELETE /jobs/{id}` · `POST /jobs/{id}/pause|resume` · `POST /jobs/{id}/trigger` (proxies the scheduler
internal API) · `GET /runs?job_id=&status=` (keyset, `ListRuns`) — plus the 7 MCP tools (§7).

**Admin (control-panel, `require_admin`, mutations audited):**
`GET /api/admin/scheduler/accounts/{id}/jobs` · `…/runs` (D12 drill-down) ·
`POST /api/admin/scheduler/accounts/{id}/pause-all` (eager `SetJobStatus(paused)` loop) ·
`POST /api/admin/scheduler/jobs/{id}/trigger`.

**Scheduler internal (`:8082`, cluster-internal):** `POST /internal/trigger/{job_id}` · `GET /healthz`.

---

## §9 UI (LOCKED with the user, 2026-07-12 — master-detail; presets+custom-cron trigger editor;
## inline error expand; always-visible row actions)

### §9.1 User — Scheduler page (sidebar item goes live) — **master-detail**

```
 ┌ Scheduler ──────────────────────────────────────────────────────[+ New job]──────────┐
 │ ┌ JOBS ─ 2 active · 1 paused ─┐  ┌ Daily briefing   AGENT ───────────────────────┐   │
 │ │▌ Daily briefing        ◀sel │  │ 0 9 * * 1-5 · Asia/Shanghai  (≈ weekdays 9am) │   │
 │ │   next Mon 09:00            │  │ NEXT RUN Mon 09:00 · created 2026-07-01       │   │
 │ │▌ Weekly review              │  │ [Run now] [Pause] [Edit]                      │   │
 │ │   ● running 0:41            │  │                                               │   │
 │ │▌ Nightly backup   PAUSED    │  │ RUNS ────────────────────────  [All status ▾] │   │
 │ │   next —                    │  │ ▌ ✓  2m41s   today 09:00       → open session │   │
 │ │                             │  │ ▌ ⊘  —       Thu 09:00       already_running  │   │
 │ │ ── ALL RUNS ──              │  │ ▌ ✗  12s     Wed 09:00              error ▾   │   │
 │ └─────────────────────────────┘  │ ▌ ✓  2m12s   Tue 09:00         → open session │   │
 │                                  │               [ Load more ]                   │   │
 │                                  └───────────────────────────────────────────────┘   │
 └───────────────────────────────────────────────────────────────────────────────────────┘
```

- **Left pane (~300 px fixed, `flex-shrink-0`):** job rows — 2 px **status** left border, name (600),
  second line = next run / live state (mono). **Selection = background only** (`--bg-elevated`), never a
  border (the border already encodes status — same rule as the hooks Runtime UI). Auto-select the first
  job on load. Footer pseudo-entry **`ALL RUNS`** → right pane becomes the cross-job run list (adds a
  job Dropdown filter next to status) — this is also the only reachable home of **deleted jobs' runs**
  (`job_id` FK `SET NULL`).
- **Right pane (`flex-1 min-w-0`):** detail header — name (lg/700) + type chip + `PAUSED` chip when
  paused; trigger line = mono cron/interval + dim human paraphrase; `NEXT RUN` + created meta; action
  row **always visible** (`Run now / Pause|Resume / Edit`; while running: `Stop` instead of Run now).
  Below: this job's runs (keyset `[Load more]`, status filter).
- **Run rows:** 2 px status border + glyph (✓ ✗ ⊘), duration (mono), fired time, right affordance:
  agent runs → `→ open session`; errors → **`error ▾` inline expand** — the row opens a mono, copyable
  `error_message` block (copy-on-hover, Check-icon feedback) + reason/duration/run-id meta line; skips
  show the reason inline (no expand needed).
- **Status-border semantics (jobs list):** running=`--purple` · active+last ✓=`--green` ·
  active+last ✗=`--red` · paused=`--border`. Run rows: success/error/skipped/running → green/red/yellow/purple.
- **States:** skeleton = 3 left job-row bars + right header bar + 4 run bars (shimmer, shapes match);
  empty = centered `CalendarClock` + "No scheduled jobs yet" + `[+ New job]` + dim hint
  "…or ask your agent in chat". Zero horizontal scroll; both panes `overflow-y-auto` independently.

### §9.2 Create/edit drawer (480 px right, 220 ms slide)

- **Type** Dropdown (Agent / HTTP call / Script) — **immutable on edit**. Name input.
- **Trigger editor = presets + custom-cron escape.** `Repeat` Dropdown → mapping:
  `Every day at…` → cron `M H * * *` · `Weekdays at…` → `M H * * 1-5` · `Every week on…` (+day Dropdown)
  → `M H * * d` · `Every N hours` / `Every N minutes` → **IntervalTriggerConfig** · `Custom cron` →
  raw mono input, server-validated on blur (invalid = red border + message). Always-live preview line:
  `≈ Mon–Fri at 09:00 · next Mon 09:00`. Edit mode reverse-maps a matching cron back to its preset,
  else shows Custom. Timezone Dropdown (searchable) defaults to browser TZ.
- **Type sections:** agent = prompt textarea + model Dropdown + collapsed `▸ Advanced` (D14
  timeout/max_turns); http = method Dropdown + URL + header key/value rows + body + timeout;
  script = language Dropdown + inline/file source toggle + mono editor + timeout.
- Edit mode only: `[Delete job]` → typed-name confirm (danger rule). Footer `[Cancel] [Create/Done]`.

### §9.3 Sidebar & admin

- **Sidebar:** enable the `CalendarClock` nav item (`Sidebar.jsx:904`); session rows with
  `origin=scheduler` get a 12 px `CalendarClock` (text-dim) before the title + "scheduled" in the meta
  line (D3); runs pruned by D15 lose only their "open session" link.
- **Admin (account detail › Scheduler tab):** same job rows read-only + pause, `[Pause all]` (confirm),
  same run list read-only (D12).

### §9.2 Admin — Scheduler section (per-account drill-down)

Account detail gains a Scheduler tab: jobs (status/trigger/last-run) + runs + `[Pause all]`
(confirm dialog). No fleet aggregation in v1 (D12, parked).

---

## §10 Failure semantics (US-7/US-8 made exhaustive)

| Failure | Behaviour | Record |
|---|---|---|
| Replica dies before claim | Another replica claims the same `(job_id, fire_epoch)` | normal |
| Replica dies after claim, before dispatch | Fire lost this period; next fire unaffected | *(optional hardening parked: claim-without-run sweep)* |
| Wake/dial fails (K8s error, pod never Ready, conn refused) | Retry 5× 2s→60s+jitter, then give up | `error · wake_failed` |
| Ambiguous POST timeout after admission | Safe re-POST — idempotent by `run_id` (D13) | normal |
| Account at concurrency cap | 429 → re-admit ≤2 min → give up (D16) | `skipped · concurrency_cap` |
| Same job still running at next fire | No wake, no pod (scheduler check; runner 409 backstop) | `skipped · already_running` |
| Run exceeds wall-clock / turn cap | Runner kills the run (D14) | `error · timeout` / `error · max_turns` |
| Pod admits then crashes mid-run | Stale `running` aged out by sweep (ceiling 2 h) | `error · dispatch_lost` |
| Account disabled/offboarded | No wake (D8); purge CASCADE-deletes jobs+runs | `skipped · account_disabled` |
| data-spine down at fire | Claim fails → all replicas skip; misfire grace 60 s, `coalesce` fires once if back in time | nothing recorded (logged) |
| Scheduler down over a fire | Same misfire semantics on restart — late-once within grace, else missed by design; never double-runs | — |

---

## §11 Cross-cutting

- **Metrics (ADR-0002 rails):** scheduler `/metrics` — `priva_scheduler_fires_total{outcome=dispatched|skipped_busy|skipped_inactive|skipped_cap|error}`, `priva_scheduler_claim_lost_total`, `priva_scheduler_dispatch_seconds` histogram, `priva_scheduler_armed_jobs` gauge, `priva_scheduler_sweep_reaped_total`. Runner — `priva_scheduled_runs_total{job_type,status}`.
- **Logging:** the seeded `get_scheduler_logger` channel (`logging.py:45`, target `logs/scheduler.log`).
- **Security:** scheduler holds no user payload/secrets beyond the shared JWT/HMAC (runner-token minting); RBAC limited to `agenttenants` patch+status; it is a waker, never a scaler, never an executor (drill SR2 preserved). Runner endpoint rejects non-platform tokens. `http_call`/`user_script` run with the pod's existing egress identity — no *new* capability vs the agent's own Bash (the missing NetworkPolicy is the standing platform-wide risk, tracked separately).
- **System Map:** add the scheduler node + scheduler→data-spine / scheduler→operator(CR) / scheduler→pod edges (`control-panel admin.py` map).

## §12 Migration & rollout

1. `migrate.py` already imports monolith YAML jobs + JSONL history (`migrate.py:76-101`). Rows with `job_type='tool_retry'` are imported **paused** (D4).
2. Rollout order: data-spine image (new table + servicer) → scheduler deployable → runner image (endpoint + executors + MCP tools) → control-panel (admin router + SPAs). Each step is inert without the next.
3. The monolith daemon keeps running until the E2E gate passes, then its launch is removed (`server.sh:455`) — the **inversion severed** moment; run the phase-4 acceptance grep (no `claude_sdk` import in `services/scheduler`) and log it in `phase-4.md §6`.

## §13 Test plan

**Unit:** claim uniqueness (two concurrent `claim_fire` → exactly one True, both backends) · trigger
arm/disarm diffing · fire pipeline skip paths (inactive / overlap / cap) · misfire+coalesce · wake
retry/backoff schedule · admission idempotency by run_id · D14 kill paths (timeout, max_turns) ·
runner-token minting/verification · D15 prune selects only scheduler-origin JSONLs past cutoff.
**Integration:** gRPC roundtrip job/run/claim (extend `tests/api` à la `test_hook_policy.py`) · dispatcher
against a fake runner (202/409/429/timeout) · executor branch per job type incl. FinishRun writes ·
attach-to-scheduled-run replays the buffer · MCP tools against in-process dataplane.
**E2E (minikube):** create → fires on schedule → pod wakes 0→1 → ⏰ session, live attach mid-run →
success record → pod idles to 0 · Run-now · pause stops arming ≤30s · overlap fire → skipped · cap fire
→ 429 path · kill scheduler pod mid-run → run finishes, records intact · kill runner mid-run →
`dispatch_lost` after ceiling · runaway prompt → killed at cap · admin pause-all.

## §14 Build order

1. **Dataplane** — `job_fire` table+repo+service+proto (ClaimJobFire, RecordRun) + regen + servicer
   registration + real gRPC client + converters + `copy_to_pg` (+ integration tests). *Prereq: land the
   hooks-policy diff (finding 8).*
2. **Runner** — `/scheduled-run` endpoint (D13 admission, D16 429) + three executors through
   `run_registry` + D14 caps + session origin tag + FinishRun + D15 prune (+ tests).
3. **Scheduler service** — engine/claim/dispatch/reconcile/internal-API + Dockerfile + k8s manifests
   (+ unit tests). E2E gate here.
4. **MCP tools** port into the runner.
5. **UI** — user Scheduler page (ASCII-confirm first) + sidebar enable + ⏰ markers + admin tab + locales.
6. **Docs & cutover** — product-spec status flip (en+zh), `scheduler.md` addendum banner, `phase-4.md`
   verification log, monolith daemon retirement.

## §15 Decision ledger

**Closed:** D1 wake+dial behind `Dispatcher` · D2 bypass+enforced-hooks · D3 sidebar ⏰ · D4 three job
types · D5 Postgres single-stage claim · D6 re-list ≤30s, no pub/sub · D7 cancel via runner · D8
`account.status` active-check · D9 overlap-skip + 409 backstop · D10 new session per fire · D11 reasons in
`error_message` · D12 admin drill-down only · D13 202-admission fire-and-forget, idempotent by run_id,
registry-owned · D14 30min/50-turn caps + AskUserQuestion disallowed · D15 7-day pod prune of ⏰
transcripts (records forever) · D16 quota-counted + ≤2 min admission retry → `concurrency_cap` skip.

**Confirmed defaults (round 3, closed 2026-07-12):** Run-now on a paused job = allowed, one-shot, job
stays paused · no failure badge on the Scheduler nav item in v1 (failures are red history rows; badge is a
fast-follow) · ⏰ sessions mix chronologically into the sidebar, marked, no filter/group in v1 ·
`result_summary` = first ~200 chars, raw · `next_run_time` blank for paused jobs · global 2 h reconcile
ceiling (above the D14 caps so the runner always kills first).

**Parked (say the word to pull forward):** Redis inbox dispatcher (durability upgrade; drops into
`Dispatcher`) · IM notify + detached permission inbox (Phase 4b, channel-connector) · `tool_retry`
redesign · per-job `permission_mode` · pre-warm before fire (product-spec tier 3) · claim-without-run
sweep · fleet-wide admin aggregation · replica sharding by `hash(account_id)` · spend gate (M6 reversal).
