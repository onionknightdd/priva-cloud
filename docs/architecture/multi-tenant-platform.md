# Priva Multi-Tenant Platform — Architecture Blueprint

> **Status:** Draft · **Date:** 2026-06-17 · **Branch:** `multi-tenant-platform`
> **Relationship to OSS Priva:** this is a *fork* of single-machine Priva into a Kubernetes
> multi-tenant platform (decision 14). The public single-machine Priva stays untouched.

> **⚠ Superseded in part by component drills — read these first.** Where this blueprint
> conflicts with a component spec under `components/`, **the component spec wins.** Binding
> modifications already locked by drills:
> - **M1 — SQLite + two-tier Redis, *not* Postgres** (`components/data-spine.md`). The central
>   DB is a single SQLite file owned by one `priva-dataplane` writer process; every reference
>   to "Postgres" below means this SQLite-service. **No object storage** (C2).
> - **M2 — no CLI session-id remap; no `cli_session_id` column** (`components/data-spine.md` §2.3,
>   §3; `components/agent-pod.md` §2). The platform-minted `session_uuid` *is* the immutable
>   `--session-id`; CREATE (`--session-id`) and RESUME (`--resume`) are split. There is **no
>   `RemapSession` RPC and no mutable `cli_session_id` column** — wherever this doc says "remap
>   hook" or "mutable `cli_session_id`," that model is withdrawn.
> - **M5 — no `session_index` table; the filesystem is the session store** (`components/agent-gateway.md`
>   §3/§13, 2026-06-18). The JSONL is the session of record (decision 15); the central DB keeps only
>   `account` + `identity_link` + `channel_binding`(+`first_run_done`). `is_first_run` = a binding CAS (IM) /
>   gateway-mint + disk-guard (WebUI). Drops `session_index`, `cwd_hash`/`config_home` persistence, and the
>   `MintSession`-row-commit / `ClaimFirstRun` central session RPCs. The remap fix is the CREATE/RESUME split
>   (table-independent), so M5 does not reintroduce remap. **data-spine + agent-pod bodies are fully
>   rewritten to M5** (2026-06-18) — `session_index` withdrawn, `channel_binding` gained `first_run_done`.
> - **M6 — BYOK + metering deferred** (2026-06-18). Every user supplies their **own real LLM provider key**
>   (BYOK) — there is **no shared org key and no virtual-key indirection**. **Spend tracking/enforcement is
>   deferred** ("token count only, for the moment"): no reserve-before-call, no `$`/cost, no budget ledger, no
>   `402 budget_exceeded`, no `spend:reserve`. Token usage is **pod-self-reported** (the SDK `result` usage →
>   data-plane, observability-only — trust is fine with nothing enforced). The **metering-proxy component is
>   dropped**; the BYOK key is just an **operator-injected secret** (like MCP tokens). The **egress security
>   gateway is deferred** — pods call the provider/internet directly (default-deny-internet relaxed). **Supersedes**
>   decision 11 (metering proxy) + **C3**, the **virtual-key half** of decision 23 / agent-pod SB2·§13-3, and the
>   **egress-injection/metering half** of decision 3 / agent-pod §13-2. Reversible: token-count keeps flowing so
>   `$ = tokens × price card` + caps + egress allow-listing switch on later. *All component docs reconciled; the
>   **deep M6 body cleanup is DONE (2026-06-18)** — agent-pod (§7/§9.6/§11/§13), data-spine (§2.6/§2.8/§4 #5), and
>   this blueprint **§3/§4** are rewritten M6-correct, not just banner-flagged. Still under this banner by design
>   (as with M1/M2/M5): the §2 decisions table, the system diagram, and §5/§6/§7.*
> - **C1 — audit log is per-user JSONL on a dedicated audit volume**, not a DB table.
> - **C3 — spend lives in the metering proxy + Redis `spend:reserve`**, not a SQLite `budget_ledger`. *(Spend now **deferred** — see M6; the proxy/ledger/`spend:reserve` are not built "for the moment.")*
> - **C4 — session fork is SUPPORTED** (a fork is a first-class CHILD create with a new immutable
>   `session_uuid`; parent untouched ⇒ not a remap). File checkpointing/rewind is in scope,
>   local-disk-only on the per-user PVC (`components/agent-pod.md` §3–§4).
> - **Residual defaults:** `usage_rollup` dropped (usage **pod-self-reported**, M6 — was "read from the proxy
>   API," now no proxy); hook execution logs stay per-user PV; M2 empirically verified at Phase 0.
>
> Component drills landed: **data-spine** (done), **agent-pod** (done), **agent-gateway** (done + reconciled to
> full-edge-adoption 2026-06-18), **Control Panel** (done — `components/control-panel.md`, full edge adoption on
> agentgateway.dev + option (a)), **`AgentTenant` operator** (done — `components/operator.md`), **central scheduler**
> (done — `components/scheduler.md`, M6-correct). The **metering proxy is DROPPED (M6)** — BYOK + token-count-only
> collapses it. **All components are now drilled.** The scheduler drill locked the **inversion** (the scheduler
> executes nothing; the per-user pod executes every job — the scheduler only fires/claims/wakes), resolved the
> deferred **`PushToChannel`** seam (delivery rides the dispatch frame; the gateway pushes; the scheduler stays
> fire-and-forget), and applied one small cross-doc fix: **data-spine §1.7** now attributes `FinishRun` to the pod and
> adds the scheduler's `StartRun(running|skipped)` + `ListActiveJobs` (the §2.11 schema was already correct).
> The **deep M6 body cleanup is DONE (2026-06-18)** — agent-pod, data-spine, agent-gateway, and this blueprint
> **§3/§4** are rewritten M6-correct (the §2 decisions table, the system diagram, and §5/§6/§7 stay under the
> supersession banner above, as with M1/M2/M5). **The only remaining platform work** (neither a drill): the
> agent-pod.md **full-edge terminology pass** (gateway→agentgateway/brain/connector) + the **channel-connector**
> sub-pass (agent-gateway §4.4).

---

## 0. Overview

Fork single-machine Priva into a Kubernetes multi-tenant platform for **hundreds of
semi-trusted internal users**: one **scale-to-zero Agent Pod per user** (standard pod +
namespace + RBAC + NetworkPolicy isolation), a **stateless Agent Gateway** as the single
front door for every surface, and a central control plane (**Postgres + two-tier Redis +
LLM metering proxy + scheduler + `AgentTenant` operator + admin console**) that holds all
durable metadata and coordination state. Live transcripts are JSONL on a per-user RWX
volume; Postgres is the system of record for anything queried across users.

> **The single load-bearing correction over a naïve fork:** every key, lock, route, and
> binding is keyed on a **platform-minted stable `session_uuid`** — *never* a CLI-derived id.
> **(M2, refined by the data-spine drill):** the `session_uuid` *is* the CLI `--session-id`,
> immutable for the session's life — CREATE passes `--session-id <uuid>`, RESUME passes
> `--resume <uuid>`, and the two are never combined. There is **no remap and no mutable
> `cli_session_id` column**; the `session_uuid` resolves the JSONL path directly. The only
> sanctioned id-minting path is an explicit **fork**, which creates a CHILD session with its
> own new immutable `session_uuid` (parent untouched ⇒ not a remap; see C4). See
> `components/data-spine.md` §3 and `components/agent-pod.md` §2.

---

## 1. The 26 locked decisions

| # | Decision | Choice |
|---|----------|--------|
| 1 | Audience / scale | Hundreds of **semi-trusted internal** users |
| 2 | Pod lifecycle | Per-user pod, **scale-to-zero** when idle |
| 3 | Tenant isolation | Tenant↔tenant → standard **pod + namespace + RBAC + NetworkPolicy** (no microVM) |
| 4 | State model | **Hybrid** — live transcript on per-user RWX PV; metadata in central **Postgres** |
| 5 | Run resilience | **Accept loss** on rare mid-run pod death (v1); transcript survives to last write |
| 6 | Approvals | A pending approval **pins the pod awake**; the `asyncio.Future` stays in-pod |
| 7 | Coordination | **Redis**, split into **persistent (AOF)** + **ephemeral cache** tiers |
| 8 | Identity | One **central account**; browser/WeCom/Feishu/OpenClaw are **linked identities** |
| 9 | Scheduling | **Central scheduler** owns all jobs; **wakes the pod** at fire-time |
| 10 | Control panel | **Full ops** — observe + lifecycle + quotas + spend caps + user mgmt + **impersonation** |
| 11 | LLM / spend | Central org keys behind a **metering proxy** (LiteLLM-style); per-user **virtual keys + budgets** |
| 12 | Provisioning | **Custom operator + CRD** (`AgentTenant`) |
| 13 | Live storage | **Networked RWX filesystem** per user (zone-agnostic) |
| 14 | Build approach | **Fork** to new repo/branch; no live migration; OSS Priva untouched |
| 15 | Core unit | The **session** (persisted JSONL); a user has many |
| 16 | WebUI role | **Session browser** — list / resume / recover any session |
| 17 | IM binding | Each channel **pinned to one session**, user-rebindable; mapping in central store |
| 18 | Session writer | **One active run per session** (in-pod authoritative lock); 2nd surface attaches read-only |
| 19 | Pod concurrency | **Concurrent sessions, capped per pod** (tied to quota) |
| 20 | WebUI serving | Built once, **served centrally** (gateway/CDN); pod is **API/WS only** |
| 21 | Tools/skills/MCP | **Shared baked-in image + per-user PV overlay** |
| 22 | Cold-start UX | **Fast wake + instant IM ack** ("on it…"); browser skeleton |
| 23 | Secrets | **Encrypted in central DB** — envelope encryption, per-account DEK wrapped by a KMS master KEK |
| 24 | Data lifecycle | Offboard = **disable now + retention window + scheduled purge**; audit retained separately |
| 25 | Pod idle policy | **`maxIdle`** (idle timer, resets on activity) **+ `minAliveAfterWake`** floor; **pin-on-approval overrides**; both per-tenant on the CRD |
| 26 | Sandbox runtime | **Hardened `runc`** (non-root, drop caps, RO-rootfs, seccomp, scoped mounts); **no microVM in v1**; **gVisor/Kata/Kuasar/Cube reserved as a `runtimeClassName` escalation** on the CRD |

---

## 2. System architecture

```
   EXTERNAL SURFACES
 ┌──────────┬──────────────┬───────────┬────────────┬───────────────┐
 │ Browser  │   WeCom      │  Feishu   │  Discord   │  OpenClaw     │
 │ (JWT/WS) │ (signed cb)  │ (evt-sub) │ (gw sock)  │ (Ed25519/mTLS)│
 └────┬─────┴──────┬───────┴─────┬─────┴─────┬──────┴──────┬────────┘
      │            │             │           │             │
      │  static SPA served centrally (built once; pods serve ZERO static)
      ▼            ▼             ▼           ▼             ▼
 ╔══════════════════════════════════════════════════════════════════╗
 ║                  AGENT GATEWAY  (N replicas, L4/sticky LB)         ║
 ║  • terminates all WS/SSE + holds all IM channel sockets           ║
 ║  • identity resolution (surface → account_id)                     ║
 ║  • session targeting (channel_binding → session_uuid)             ║
 ║  • buffer+ack+wake / outbound fan-out / permission RELAY          ║
 ║  • PRE-GATE: account-status + cached hard-cap BEFORE ack+wake     ║
 ║  HOLDS NO AGENT STATE — no Future, no JSONL, no coordinator       ║
 ╚══╦═════════╦═══════════╦══════════╦═══════════╦═══════════╦═══════╝
    ║         ║           ║          ║           ║           ║
    ▼         ▼           ▼          ▼           ▼           ▼
 ┌──────┐ ┌────────┐ ┌──────────┐ ┌─────────┐ ┌─────────┐ ┌──────────┐
 │REDIS │ │POSTGRES│ │LLM PROXY │ │CENTRAL  │ │ TENANT  │ │ CONTROL  │
 │      │ │(SoR)   │ │(LiteLLM) │ │SCHEDULER│ │OPERATOR │ │ PANEL    │
 │tier1 │ │accounts│ │org keys  │ │owns all │ │AgentTen │ │admin SPA │
 │persist│ │identity│ │virt keys │ │cron/jobs│ │ant CRD  │ │/admin/v2 │
 │ inbox │ │bindings│ │meter+cap │ │fire→wake│ │reconcile│ │observe + │
 │ approv│ │audit   │ │RPM/TPM   │ │→dispatch│ │scale 1↔0│ │lifecycle │
 │ spend │ │metrics │ │   ▲      │ │   │     │ │   │     │ │impersonate│
 │tier2  │ │quota   │ │   │meter  │ │   │CR   │ │   │CR   │ │          │
 │cache  │ │budget  │ │   │       │ │   │patch│ │   │patch│ │ routes   │
 │ route │ │secret  │ │   │       │ │   ▼     │ │   ▼     │ │ through  │
 │ awake │ │session_│ │   │     ┌─┴────────────┴────────┐ │ │ operator │
 │ locks*│ │ index  │ │   │     │    K8s API server     │ │ │ + proxy  │
 └───┬──┘ └───┬────┘ └───┼─┘    │  (per-tenant objects) │ │ └────┬─────┘
     │        │          │      └──────────┬────────────┘ │      │
     │ wake nudge        │  ONE wake transport: CR patch   │      │
     │ (optimization)    │  Redis wake = nudge only        │      │
     ▼        ▼          ▼                 ▼               ▼      ▼
 ╔══════════════════════════════════════════════════════════════════╗
 ║              PER-USER AGENT PODS   (tenant-scoped)                ║
 ║  ┌────────────────────────────────────────────────────────────┐  ║
 ║  │ slim FastAPI  ── spawns ──▶ claude CLI subprocess (per run) │  ║
 ║  │  • in-pod PermissionCoordinator (asyncio.Future stays HERE) │  ║
 ║  │  • AUTHORITATIVE single-writer = in-pod asyncio.Lock+fcntl  │  ║
 ║  │  • N concurrent runs ≤ min(mem-derived, quota)              │  ║
 ║  │  • secret bootstrap (DB→env) · virtual key · base_url guard │  ║
 ║  │  • hardened runc: non-root, drop caps, RO-rootfs, seccomp   │  ║
 ║  └───────────────────────┬─────────────────────────┬──────────┘  ║
 ║       ANTHROPIC_BASE_URL ─┘ → LLM PROXY              │            ║
 ╚═════════════════════════════════════════════════════╪════════════╝
                                                        ▼
                          ┌──────────────────────────────────────────┐
                          │  RWX networked FS (NFS/EFS/Filestore)     │
                          │  per-user subPath mount, uid:gid scoped:  │
                          │   /workspace/<project>/<cli_sid>.jsonl    │
                          │   + per-user skills/MCP overlay           │
                          │  zone-agnostic → pod schedules ANY node   │
                          └──────────────────────────────────────────┘

 LEGEND  ═ control/data flow   ── component edge   * locks/awake = REDIS STATUS MIRROR
         Locks are AUTHORITATIVE in-pod; Redis holds a status mirror only.
```

---

## 3. Plane breakdown

### Edge / Gateway plane
The **Agent Gateway** (N replicas behind an L4 LB) is the only outward-facing component. It
terminates browser WS/SSE and holds every IM channel socket (WeCom `WSClient` with its
SSL/CONNECT-proxy patches, Feishu, Discord, OpenClaw Ed25519 bridge). Per request it resolves
identity (surface → `account_id`), targets a session (`channel_binding` → `session_uuid`), and
routes bytes to the pod — buffering + waking a sleeping pod, fanning out streamed replies per
surface, and **relaying** permission decisions back to the pod that holds the live `Future`.
**It holds no agent state** — no Future, no JSONL, no coordinator — which is what dissolves
today's documented split-brain (separate channel-daemon vs API bridge registries +
coordinators) into one routing tier whose only durable state lives in Redis + Postgres.

Two hardenings: **(1)** a **pre-gate before ack+wake** — a disabled account gets "access
revoked" and a hard-capped account gets "usage limit reached, resets `<date>`" with **no
wake**; only active, under-cap accounts are buffered+acked+woken. **(2)** IM **channel-socket
ownership is an explicit monitored role**: one replica owns a bot socket via a Redis lease
(`channelconn:lease:{channel}:{bot_id}`, 10s TTL / 3s heartbeat). On owner death there's a
**bounded, observable inbound stall** (surfaced as a control-panel channel-health condition —
*not* hidden under "HA") until another replica takes the lease and re-handshakes before
declaring ready. Cross-replica reply delivery uses a Redis `in:reply:{request_id}` relay, so LB
stickiness is best-effort (<50ms one-hop budget).

### Control plane
- **Tenant Operator + `AgentTenant` CRD** — the single declarative authority over each user's
  runtime footprint (Deployment + RWX PVC + Service + NetworkPolicy + minimal RBAC +
  `runtimeClassName` + idle knobs). **The only component allowed to scale a tenant 1↔0**;
  everyone else influences a tenant by *mutating the CR*. Each reconcile reads pin-state
  (in-flight runs + pending-approval index) from Redis; a pinned tenant never sleeps. Drives the
  offboard→retain→purge state machine behind a `priva.io/tenant-purge` finalizer (the PVC is
  never owner-ref-deleted; only purged on terminal). Start with **kopf**; controller-runtime is
  the named escape hatch at **>800 CRs or p95 reconcile >5s**.
- **Central Scheduler** — owns 100% of cron/interval/one-shot jobs (in **SQLite**, M1 — not Postgres). Fire →
  **active-check** (account enabled? session not busy? — **M6: no budget pre-check**, the metering proxy is gone)
  → wake (CR-patch) → **dispatch into the pod's `inbox`; the pod executes** (the scheduler executes nothing —
  `components/scheduler.md`). Exactly-one-fire via `(job_id, fire_epoch)` unique constraint + Redis claim lock.
  Originates proactive IM only through the gateway (`PushToChannel`), only to the user's own bound channels.
- **LLM Metering Proxy — DROPPED (M6).** BYOK + token-count-only **collapses this component.** Every user brings
  their **own real LLM key** (no shared org key → no virtual-key indirection needed to protect it), and spend
  tracking is **deferred** — no reserve-before-call, no `402 budget_exceeded`, no ledger. Token usage is
  **pod-self-reported** (SDK `result` → data-plane, observability-only). The BYOK key is just an
  **operator-injected secret** (envelope-encrypted in the data-plane, KMS-unwrapped by the operator at spawn —
  `components/agent-pod.md` §7). *Reversible: the token counts keep flowing, so `$ = tokens × price card` + caps +
  a metering proxy can return later.*
- **Control Panel (`ops-console`)** — admin-only SPA + `/admin/v2/*`, RBAC-gated, every mutating
  call audits before returning. Reads a **cached fleet-projector** (operator informer cache +
  periodic meter pull into DB) so `GET /tenants` is one DB read. Lifecycle verbs route through
  the operator (CR patch) or gateway (Redis stop signal), never to a pod directly. Quota
  dual-write is proxy-first (reversible) then CRD, with a drift badge. Impersonation is a scoped,
  time-boxed, hash-chain-audited grant. Must follow the project's WebUI design-spec (GitHub-dark,
  CSS-vars only, 2px status left-border, skeleton loaders).

### Data plane
- **SQLite = system of record (M1 — not Postgres; one single-writer data-plane service fronts it).** Domains:
  `account`, `identity_link`, `channel_binding` (+`first_run_done`), `quota`, `secret`/`account_dek` (envelope
  encryption — per-account DEK wrapped by a KMS master KEK, **unwrapped by the operator** at spawn, replacing
  today's single hardcoded Fernet key), `scheduled_job`/`job_fire`/`job_run_record`, MCP/hook config, temp index,
  `retention_state`. **No `session_index` (M5 — the JSONL is the session store).** **No `budget_ledger` and no
  `usage_rollup` (C3 / M6 — spend deferred; token usage is pod-self-reported, observability-only).** `audit_log`
  is **per-user JSONL on the audit volume**, not a DB table (C1). **Authoritative on conflict with Redis.**
  *(Full DDL: `components/data-spine.md`.)*
- **Redis = coordination substrate, two tiers with different durability contracts** (the
  critical fix): **Tier 1 (persistent/AOF)** holds the inbound buffer, pending-approval index, and
  binding cache (spend reservations are **deferred — M6**) — losing these is a correctness bug, so the "on it…" ack
  fires *only after a confirmed durable write*. **Tier 2 (ephemeral cache, reconstructible)**
  holds routing/awake-state and the session-lock **status mirror**.
- **RWX networked filesystem** — per-user subdir holds live JSONL transcripts
  (`projects/<cwd-hash>/<cli_session_id>.jsonl`), subagent sidechains, the per-user skills/MCP
  overlay, and scratch. The PV holds *bytes the agent reads/writes*; Postgres holds *facts
  queried across users*. Transcript content is **never duplicated into Postgres** — only a
  cheap-to-rebuild index row. The **in-pod `asyncio.Lock` is authoritative**; the `.lock` file is
  best-effort, with **no dependence on cross-host NFS lock correctness** (one pod per user = the
  sole writer host).

### Tenant plane
The **Agent Pod** is a fork-and-strip of today's `priva/api` uvicorn process: the agent/run
loop, `PermissionCoordinator`, options builder, hooks, MCP, PTY are reused; the user DB, static
SPA, channel daemon, and APScheduler are removed and replaced by calls to central services. It's
the only process touching a user's JSONL. It runs the in-pod permission coordinator (Future
never serializes), enforces single-writer per session via the in-pod lock, and runs up to
`min(mem-derived, quota.max_concurrent_sessions)` concurrent `claude` subprocesses. At boot it
**loads operator-injected secrets — including the user's own BYOK LLM key — from a tmpfs mount** (the pod
decrypts nothing and holds no KMS key — the operator unwraps at spawn; see `components/agent-pod.md` §7; **M6:
no virtual key, no metering proxy**), and **purges any orphaned approval-index entries**
(post-accept-loss) so the operator un-pins. It exports `IdleState` to Redis.

### Isolation, sandbox & storage model (decisions 3, 21, 23, 25, 26)
**Tenant isolation (the primary boundary) = the pod, not a sandbox.** Each user's pod +
per-user PVC + NetworkPolicy + RBAC keeps tenants apart. A microVM sandbox would only harden
against **kernel-escape between co-located pods on a shared node** — a low-likelihood, accepted
risk for semi-trusted internal users.

- **Runtime (decision 26):** **hardened `runc`** — `runAsNonRoot`, **drop ALL capabilities**,
  `readOnlyRootFilesystem`, `seccompProfile: RuntimeDefault`, no `privileged`/`hostPath`/
  host-namespaces. This closes essentially every *misconfiguration* escape vector; only a genuine
  kernel 0-day remains. **No microVM in v1.** The `AgentTenant` CRD carries `runtimeClassName`,
  so flipping to **gVisor** (cheap host-escape hardening) or **Kata/Kuasar/Cube** (microVM, for
  the day you open to untrusted users) is a per-tenant config change — *not* a redesign.
  **Cube/E2B sandbox-as-a-service is explicitly out**: it solves per-execution isolation for the
  *shared-host* model, which the pod-per-user model already supersedes, and it would fight the
  CLI's local Bash.
- **Network (decision 3, refined by `components/agent-pod.md` §9.6/§13-2; egress gateway DEFERRED — M6):**
  default-deny **pod-to-pod** still holds. The **egress security gateway is deferred** "for the moment": all HTTP
  outbound (the BYOK LLM call, MCP HTTP/SSE, hooks, tool web) goes **direct** — no allow-list, no forced
  traversal, no credential injection at egress (all creds, including the BYOK key, ride the operator-injected
  bundle). The default-deny-internet rule is relaxed. Pods still make **no** IM calls — the gateway owns IM.
  *Reversible: re-add the forward proxy + allow-lists + deny-direct-internet to restore controlled egress (and,
  with M6 lifted, metering).*
- **Filesystem separation = scoped mounts + POSIX, not a sandbox.** Each pod mounts **only its
  own user's subdir** (`subPath` / per-user PV), with per-user `uid:gid` ownership and non-root —
  so a pod literally cannot path-traverse into another user's data. **Never mount the whole RWX
  export into every pod.**
- **Storage granularity:** **PVC = the user. Subdir = the project. File = the session.**
  Multi-project support is **subdir-per-project inside the one per-user volume** — adding a
  project is a `mkdir`, **no new PVC/pod/operator action**. A PVC-per-project would be an
  object-sprawl mistake (projects are the same tenant — they need organization, not isolation).
- **Idle policy (decision 25):** the operator sleeps a pod only when
  `now − lastActivity > maxIdle` **AND** `now − startedAt > minAliveAfterWake` **AND** no
  in-flight run **AND** no pending approval **AND** no live PTY/attached reader.
  `minAliveAfterWake` prevents wake/sleep thrash for closely-spaced turns; **pin-on-approval
  overrides everything**. Both knobs are per-tenant on the CRD.

---

## 4. Key request flows

**1 · Cold-start IM (asleep → streamed reply).** Gateway verifies the WeCom signature →
resolves userid → `account_id` → bound `session_uuid` → checks Redis route (asleep). **Pre-gate**
(active + under hard cap) runs first; a disabled/capped account is answered with no wake.
Otherwise: durable Tier-1 `inbox` write → "on it…" ack (*only after* the write confirms) → **CR
patch `spec.wake.requestedAt`** (+ optional Redis nudge) under the per-account `awake:lock`.
Operator scales 0→1, re-attaches the existing PVC/Service/NetworkPolicy; pod schedules on any
node, mounts the RWX PV, **loads operator-injected secrets — including the user's own BYOK LLM key — (no in-pod
decrypt; M6: no virtual key)**, registers in Redis, drains the inbox FIFO. Pod acquires the in-pod single-writer
lock on `session_uuid`, resolves the JSONL path directly from the immutable `session_uuid` (no
`cli_session_id` — M2), spawns the `claude` subprocess (→ **the user's LLM provider directly**, M6), streams
tokens back; gateway chunks them to WeCom, replacing the ack. On completion: flush, release lock, **self-report
token usage** (M6 — no ledger to settle), become idle-eligible; operator sleeps it after the idle policy clears.

**2 · Permission rendezvous (reply hours later).** Mid-run the permission gate mints
`request_id`, creates the in-pod `future`, and the pod **pins itself** (`SADD
pin:approval:{account}` + persists `request_id → session_uuid` into the Tier-1 approval HASH).
Pod emits `permission_request` → gateway renders an interactive card + plain-text `-> yes/no`
fallback to the bound surface (carrying `request_id`), sets the Redis index TTL =
`approval_window + 1h`. The operator sees a non-empty pin set and refuses scale-to-zero; **no tokens are consumed**
while parked. Hours later the reply hits *any* gateway replica → `HGETALL` the entry →
idempotently mark resolved → route the decision **to the exact pod instance** (by `pod` field,
re-establishing the WS via the Service if needed) → pod maps `request_id → session_uuid →
coordinator`, calls `resolve()`, the parked `await` returns, run continues, pin clears.
**Failure mode (pod died):** the late reply finds no live pin → "this request expired because the
session was interrupted; please re-ask," and the next pod boot **affirmatively purges** the stale
entry so the operator un-pins (no zombie pod).

**3 · Scheduled wake.** Scheduler claims a due fire (`(job_id, fire_epoch)` — exactly-once) → resolves
`account_id` + `target_session_uuid` → **active-check** (account enabled? — **M6: no budget pre-check**, the
metering proxy is gone). If asleep: durable `inbox` write → CR-patch wake (+ nudge) → operator scales up → pod
**drains and executes** (the scheduler executes nothing — `components/scheduler.md`). **If the target session's
in-pod writer lock is held → `skipped(already_running)`, rely on next fire** (SKIP, not queue — matches the
single-writer invariant). Wake failure → bounded retry (**5×, 2s→60s + jitter**) then misfire policy.
Cron-aligned storms are spread by per-fire jitter + the operator's wake-concurrency limiter.

**4 · WebUI attach.** Browser loads the centrally-served SPA (skeleton) → `GET /api/sessions`
with JWT → gateway lists from `session_index` enriched with Redis liveness, waking the pod only
on resume. **(A) Idle session:** pod acquires the in-pod single-writer lock, replays the JSONL,
enables the composer. **(B) IM-running session:** the browser **attaches read-only/live** —
replays the JSONL prefix then tails appends from the IM run; composer disabled, marked "owned by
IM channel X"; it may **take over only once the session goes idle** and the lock frees. The Redis
lock key is a **status mirror only**, never an acquisition gate — which is what kills the
dual-authority race.

---

## 5. Resolved design risks (adversarial pass)

| Sev | Issue | Resolution |
|---|---|---|
| **Blocker** | `claude` CLI was believed to **mutate its own `session_id` mid-run** → locks/routes/bindings keyed on the start-id orphan | **Resolved as M2 no-remap (data-spine drill).** Platform-minted **`session_uuid`** is the PK + key for every Redis structure, lock, route, binding, approval entry — *and is itself the immutable `--session-id`*. The mid-run mutation was traced to code that set `options.resume` on CREATE; splitting CREATE (`--session-id`) from RESUME (`--resume`) removes it. **No `cli_session_id` column, no remap hook.** A per-run consistency assertion quarantines any unsanctioned id drift |
| **Blocker** | Late approval reply unresolvable; post-accept-loss pod has no Future yet Redis still pins it → **zombie pod awake forever** | Pod persists `request_id → session_uuid` in the Tier-1 HASH; on resolve maps `request_id → session_uuid → coordinator`. **Boot step purges** orphan entries (DEL + SREM pin) so the operator un-pins |
| **Blocker** | IM channel socket = per-bot SPOF; fast TTL risks WeCom double-connect split-brain | Explicit lease (10s TTL/3s heartbeat), channel-ownership a monitored role, re-handshake before ready; **bounded inbound stall documented** as a channel-health condition |
| **Major** | Per-pod concurrency is genuinely NEW (today's daemon is serial); singletons assume one run | Run-context made **multi-instance** (per-run coordinator+queue, no module singletons); cap enforced in **one place — the pod** (gateway/scheduler get 429); effective cap = `min(mem-derived, quota)` |
| **Major** | Single Redis tier mixes non-reconstructible state with a fail-closed spend hot-path | **Two tiers**: persistent/AOF for inbox + approval-index + binding-cache + spend reservations; ephemeral cache for routing/awake. Ack only after durable write; degraded-spend reconciles against the proxy event log |
| **Major** | Impersonation vs the `asker_id` approval gate | ACT_AS **does** spend the target's budget + use their secrets/tools (inherent), spend tagged `impersonator_account_id` at the proxy, **audited** — **but may NOT answer approvals** (locked `components/agent-pod.md` §13-6, reversing the earlier "may answer, audited"): a permission request is answerable only by the user, via their WebUI session or bound IM channel. *(Secrets are operator-injected, not pod-decrypted — §7.)* |
| **Major** | Two wake transports (CR patch + Redis pub/sub) with no serialization point | **CR patch is the only scale-up trigger**; Redis `wake:pod` is a reconcile **nudge only**. `awake:lock` guards the CR patch across all wakers |
| **Major** | Scheduler SKIP vs flow QUEUE contradiction for a busy session | **SKIP** adopted (matches single-writer invariant): `skipped(already_running)`, rely on next fire |
| **Major** | `channel_binding` defined three incompatible ways | One schema keyed off `identity_id`, referencing stable `session_uuid`; **identity-auth writes, gateway reads** |
| **Major** | Per-tenant namespace/object sprawl + kopf single-process under wake-storms | **Wake-concurrency limiter (~20 in-flight)** + per-fire jitter; **shared `priva-tenants` namespace** with per-tenant podSelector NetworkPolicies; base-image DaemonSet pre-pull; controller-runtime trigger named |
| **Major** | Conflicting LLM auth env var + env-inspection base_url guard (insufficient) | **One standardized auth env var**; base_url guard is an **active probe** (signed-sentinel round-trip, connection-level); ship sanitized `~/.claude.json` and **re-assert every boot** |
| **Major** | Single-writer lock described as both Redis distributed lock and in-pod lock — dual authority | **In-pod `asyncio.Lock` (+fcntl best-effort) authoritative**; Redis `lock:session:` demoted to a **status mirror** (TTL/heartbeat to clear a dead pod's "running") |
| Minor | Instant ack fires before budget/quota check | Gateway **pre-gate before ack+wake** (revoked / capped answered without wake) |
| Minor | Unbounded inbound buffer → flood/overflow | **~50 msgs/account cap**, **~1h per-entry TTL**, duplicate-coalescing, atomic drain-then-attach |
| Minor | Egress allow-list would break tool use | **Locked (agent-pod §13-2):** all HTTP outbound routes through an **egress gateway** that allow-lists, injects credentials, and meters; direct internet + pod-to-pod **denied**. Stdio-MCP/env-reading tools use operator-injected secrets. |
| Minor | Three inconsistent views of spend | **Proxy Redis counter = single enforcement authority**; the proxy event log is the spend system-of-record; the control panel reads usage/spend **straight from the proxy API**. *(`usage_rollup` is dropped — residual default #1; no SQLite cache of token/cost rows.)* |

---

## 6. Phased build plan (fork-first, incremental)

- **Phase 0 — Fork + central spine.** New branch/repo; **SQLite full schema (`session_uuid` PK,
  no `cli_session_id` column)** owned by the single `priva-dataplane` writer (M1); two-tier Redis;
  thin data-plane client; envelope-encryption KEK in KMS + one-time secret re-encrypt. **Exit:**
  data-plane + Redis HA validated (failover, snapshot/restore); and the **M2 empirical verification**
  (residual default #4) passes against a real `claude-agent-sdk` run — bare `--resume`-of-missing-file
  behavior + autocompact id-preservation confirmed, with the per-run consistency assertion + CI
  version-pin as backstop.
- **Phase 1 — Single pod + gateway + identity.** Stripped pod image (no static/channels/
  APScheduler); in-pod authoritative lock + multi-instance run-context; secret/virtual-key boot +
  active base_url probe + sanitized `.claude.json`; gateway terminating browser WS/SSE + JWT
  identity → manually-run pod; proxy minting virtual keys with reserve-before-call metering.
  **Exit:** a browser user runs a metered session; `session_uuid` keys everything; base_url guard
  fails readiness on a seeded `.claude.json`.
- **Phase 2 — Operator + scale-to-zero.** `AgentTenant` CRD + kopf operator (Deployment/PVC/
  Service/NetworkPolicy, shared `priva-tenants` namespace, default-deny pod-to-pod + general
  egress, `runtimeClassName`, idle knobs); CR-patch-only wake with `awake:lock`; pin-from-Redis
  scale guard; base-image warmer; wake-concurrency limiter. **Exit:** cold-start p95 within
  target; pod sleeps per the idle policy; a pinned pod never sleeps; object-count +
  reconcile-latency alerts wired.
- **Phase 3 — Channels → gateway.** WeCom/Feishu/Discord/OpenClaw moved into the gateway with the
  lease role + monitored failover; `InboundMessage` normalizer; pre-gate; durable-buffer-then-ack;
  outbound fan-out + chunker; `/link` flow. **Exit:** WeCom/Feishu cold-start streams a reply;
  permission rendezvous resolves a reply hours later to the exact pod; killing the channel-owner
  replica shows a bounded, observable stall then recovery.
- **Phase 4 — Central scheduler.** Jobs migrated out of `.priva.user.yml`; `(job_id, fire_epoch)`
  idempotency + claim lock; live-counter budget pre-check; CR-patch wake + buffer dispatch;
  SKIP-if-busy; bounded retry + misfire policy; proactive IM via gateway only; offboarding-hooked
  deactivation. **Exit:** a daily cron wakes a sleeping pod and runs; a storm spreads under the
  limiter; over-budget fire skipped without a wake.
- **Phase 5 — Control panel.** Admin SPA (separate bundle, shared design-spec lib, served
  centrally) + `/admin/v2/*`; cached fleet-projector; lifecycle via operator/gateway with
  confirmation dialogs; quota dual-write (proxy-first, drift badge); hash-chained per-account
  audit; scoped time-boxed impersonation. **Exit:** admin observes the fleet from one DB read,
  wake/sleep/restart/stop-run, sets quotas/caps, impersonates with full audit + peer-visible
  active list.
- **Phase 6 — Hardening / quotas / offboarding.** Operator offboard→retain→purge state machine +
  finalizer; retention defaults; spend-cap defaults + monthly reset; bounded buffer; degraded-mode
  spend + reconciliation sweep; channel-health/object-count/reconcile dashboards; chaos tests for
  pod-death-mid-approval + Redis failover. **Exit:** offboarding disables immediately, purges on
  schedule with in-window recovery; Redis failover loses no inbox/approval entries;
  pod-death-mid-approval yields a clean "expired, re-ask" + un-pinned operator.

---

## 7. Residual decisions (proposed defaults — confirm or adjust)

| Item | Proposed default |
|---|---|
| `maxIdle` / idle grace | **180s**, per quota tier; must exceed buffer-drain + first-token window |
| `minAliveAfterWake` floor | **180s** (≈ matches idle grace; prevents wake/sleep thrash) |
| Approval window vs in-pod Future timeout | One knob **`approval_window` = 24h**; Future timeout = window; Redis index TTL = window + 1h slack |
| `/link` code | **10-char base32 (50-bit), 300s TTL, single-use, 5/min/channel lockout, account-name echo** |
| Per-pod concurrent-session cap | **3**, hard-bounded: `min(quota, floor((mem_limit − base)/per_subprocess_budget))` |
| Monthly spend cap + reset | **150 USD/mo per tier; calendar-month UTC reset** |
| Offboarding retention | **PV/sessions purge 30d; audit retained ≥1y, separate** |
| Scheduler wake-failure retry | **5 attempts, 2s→60s + jitter**, then misfire policy |
| Admin SPA vs user session-browser | **Two build targets, both served centrally**, sharing the design-spec component lib |
| Reply relay / LB stickiness | **Best-effort sticky L4 + Redis `in:reply` fallback**, <50ms one-hop ceiling |
| fcntl-over-NFS | **In-pod lock authoritative; `.lock` best-effort; no NFS-lock dependence** |
| Sandbox runtime (decision 26) | **Hardened `runc` v1**; `runtimeClassName` left settable on the CRD for gVisor/Kata escalation |

---

## Component drill-downs

> Implementation-depth specs are added below as each component is drilled. See sibling files
> under `docs/architecture/components/` once split out.
