---
Status: Draft · Date: 2026-06-18 · Branch: multi-tenant-platform
Parent: ../multi-tenant-platform.md · Component: AgentTenant Operator (the sole scaler; kopf controller, one shared `priva-tenants` namespace) — realizes blueprint **decision 12**
Consumes: ./agent-runner.md (the pod it provisions/wakes/sleeps — securityContext, mounts, drain, boot, idle export, pin gate), ./data-spine.md (account/quota/secret config + the Redis pin/route/awake/lock catalog), ./control-panel.md (admin terminate/wake verbs land here; the fleet view reads the operator informer cache), ./agent-gateway.md (the brain's CR-patch wake) as binding contracts
---

# Priva AgentTenant Operator — Component Specification

**Scope.** The **AgentTenant Operator** is the **single declarative authority** over each user's runtime footprint and **the only component allowed to scale a tenant `1↔0`** (blueprint decision 12, §188). Every other component influences a tenant **by mutating its `AgentTenant` CR**, never by touching a pod (blueprint §189, §208). It is an **entirely new component** — there is **no current-code equivalent** (today "lifecycle" is a single uvicorn process spawning a `claude` subprocess; `grep` confirms zero `kopf`/CRD/K8s-controller code in the repo). So this drill is grounded not in a fork-and-strip of existing code but in **making good on every `the operator …` promise scattered across the four locked component specs** — collected and cited in §0. Its job: own the `AgentTenant` CRD (§1), reconcile each tenant's managed objects (§2), perform the **CR-patch-triggered wake** with **secret injection at spawn** (§3, §6), enforce the **pinned-aware idle predicate** for scale-to-zero (§4), reap zombie pins (§5), provision **per-user scoped storage** (§7), and drive the **offboard→retain→purge** state machine behind a finalizer (§8) — all from **kopf**, in **one shared namespace**, surviving wake-storms (§9).

> **Terminology guard — "operator" is THIS, and only this.** (1) **the operator** (this doc) — the `AgentTenant` controller; the **sole scaler** of tenant pods. (2) **agentgateway** (agentgateway.dev) — the external L7 **edge proxy** (control-panel.md §1); it has *its own* control-plane (controller + Deployer + xDS — control-panel.md §1.3) that provisions **proxy** Deployments, but it **knows nothing of tenants/pods/scale-to-zero** and never scales an Agent Runner. Do not conflate agentgateway's Deployer with this operator. (3) **the brain** (the ext_proc face of the Control Panel) — **mutates** the CR (`spec.wake.requestedAt`) to *request* a wake but **never scales**; the operator is the one that acts on the patch (agent-gateway.md §6.3). (4) **the Control Panel app** — issues admin terminate/wake **as CR patches**, executed by the operator (control-panel.md §7.4). The rule everything stands on: **only the operator changes a Deployment's replica count.**

> **Verification note.** Every operator responsibility below is cited `file:line` to the doc that *requires* it. Where a requirement is under-specified by its source (e.g. the exact metering-proxy virtual-key-mint API), it is flagged and deferred to that component's drill. One **cross-doc inconsistency** found during this drill is surfaced as a concrete owed fix (§1.3 / §13: `data-spine quota.idle_grace_seconds DEFAULT 180` contradicts the locked 30-min default).

> **M6 banner (BYOK + metering deferred, locked 2026-06-18 — supersedes this doc's virtual-key text).** This doc was drafted assuming a metering proxy + per-spawn **virtual keys**. That is **withdrawn** (blueprint **M6**): every user brings their **own real LLM key** (BYOK), so the virtual-key indirection is unnecessary; **spend tracking is deferred** (token-count-only, pod-self-reported); the **metering proxy is dropped**; the **egress security gateway is deferred**. §0 O5, §1.1, §2.4, §3.2, §6, §12 OP8 and §13 are reconciled inline below; read "virtual key" anywhere it survives as "the user's own BYOK key."

Section map: §0 the contract surface (scattered promises → operator, cited) · §1 the `AgentTenant` CRD (spec + status) · §2 the reconcile loop & managed objects · §3 scale-up / wake · §4 scale-down / the idle predicate · §5 the pin reaper (zombie-pod bound) · §6 secret injection at spawn · §7 per-user storage & identity · §8 the offboard→retain→purge state machine · §9 operator HA, scale & wake-storms · §10 control-plane verbs that land here · §11 new artifacts (code deltas) · §12 resolved risks · §13 resolved decisions & open items.

> **As-built increment — runner type & per-account resource specs (2026-06-24).** Shipped on the minikube slice and verified E2E. The CRD `spec` gained `agentRunnerType` (`auto_scale` | `persistent`, default `auto_scale`), `resources` (`cpu` cores, `memoryMb`) and `storageGb`. (1) **Runner type:** `ensure` (on.create+on.resume) scales a `persistent` tenant to 1 and materializes its creds (always-on); the idle sweep (§4) now returns early for `persistent` (podIP still self-heals, but never scales 1→0). `auto_scale` keeps the existing wake/idle behavior. (2) **Resource specs:** `_deployment_body` templates the container `resources` (requests==limits = Guaranteed QoS) and `_pvc_body` parametrizes `storage` + an expandable `storageClassName` (`csi-hostpath-sc`), both resolved from the CR with settings fallback. (3) **Live edits:** three `@kopf.on.field` handlers (each skips the create event, `old is None`) — `spec.agentRunnerType` (toggle scale/idle), `spec.resources` (patch the Deployment template → `Recreate` restart), `spec.storageGb` (grow-only PVC patch; shrink and non-expandable-SC errors are surfaced to `status.storageWarning` without crash-looping). control-panel's `update_tenant_runtime`/`ensure_tenant` are the only CR writers. The richer §1 blueprint fields (per-spawn KMS, CSI access-points, etc.) remain the target; this increment is the alpha slice that actually runs.

---

## 0. The contract surface (what the other specs already require of the operator)

The operator exists to satisfy promises the other four specs make. This table is the drill's spine — each row is a requirement **cited to where it was locked**, and the §ref is where this doc delivers it.

| # | Required behaviour | Promised in (cited) | Delivered |
|---|---|---|---|
| O1 | **Sole scaler `1↔0`**; everyone else mutates the CR | blueprint §188-189; agent-runner §8 (`:605` "the AgentTenant operator is the **sole** scaler via CR patch"); control-panel §7.4 ("the Control Panel never scales directly") | §2, §3, §4 |
| O2 | **`AgentTenant` CRD = the single declarative authority** over Deployment + RWX PVC + Service + NetworkPolicy + minimal RBAC + `runtimeClassName` + idle knobs | blueprint §186-188; agent-runner §10 (`:797` "AgentTenant CRD / operator (NEW manifests)") | §1, §2 |
| O3 | **CR-patch `spec.wake.requestedAt` is the ONLY scale-up trigger**; `wake:pod` pub/sub is a reconcile **nudge** only; `awake:lock` serializes patchers | agent-runner §8.5 (`:647-649`, `:819`); agent-gateway §6.3; data-spine §4 #10 `awake:lock` / #12 `wake:pod` (`:572,574`); blueprint §342 | §3.1 |
| O4 | On wake, **scale 0→1 and re-attach the existing PVC/Service/NetworkPolicy** | blueprint §289-290; agent-runner §8.5 (`:649`) | §3.2 |
| O5 | **Inject secrets at spawn**: fetch the account's wrapped bundle from the data-plane, **unwrap via KMS (operator-scoped RBAC)**, inject a **tmpfs-backed projected K8s Secret** (the pod calls no KMS, holds no DEK). **M6: the bundle now includes the user's *own* LLM provider key (BYOK)** — no virtual key, no metering proxy | agent-runner §7 SB1 (`:568`), §13-3 (`:866`), §11 B2 (`:807`); blueprint §240-241, §290, **M6**; data-spine §2.9 `account_dek` (`:232`) | §6 |
| O6 | **Scale-to-zero only when** `SCARD pin:approval == 0` **AND** `lock:session` absent/expired **AND** `route.state=idle` past `idle_grace` **AND** no live PTY/attached reader; **pin>0 is a hard override** | agent-runner §6.5 (`:540`), §8 (`:605,668`); blueprint decision 25 §274-278; data-spine §4 #4 (`:561` "operator's hard refusal to scale-to-zero") | §4 |
| O7 | **Pin reaper**: periodically purge pins whose `pod` field maps to a 0-ready Deployment → un-pin (bounds the zombie window below 25h) | agent-runner §6.5 (`:538` "a periodic operator-side reaper…") | §5 |
| O8 | **Per-user scoped storage**: emit a **CSI access-point whose root *is* the account subdir** (parent unreachable; per-user quota); `subPath` fallback; own the **account→uid** mapping; **separate audit volume** | agent-runner §9.4 §13-5 (`:727`, `:870`), §9.2 (`:698`); data-spine §3.8 (`:528,546`) | §7 |
| O9 | **Per-tenant NetworkPolicy**; ingress from **operator probes** allowed; default-deny pod-to-pod | agent-runner §9.6 (`:738,750`); blueprint §345 (per-tenant podSelector NetworkPolicies, shared namespace) | §2.4 |
| O10 | **Pre-SIGTERM idle gate** (idle termination fires only when nothing is running) + honor the pod's **drain** (`terminationGracePeriodSeconds` ≥ `termination_drain_max_s`); **never scale-to-zero mid-run/mid-fork** | agent-runner §8.4 (`:640`), §3 (`:286`), R11 (`:837`) | §4.3 |
| O11 | **Offboard→retain→purge** state machine behind a **`priva.io/tenant-purge` finalizer**; the **PVC is never owner-ref-deleted**, only purged on terminal | blueprint §191-192, §392 | §8 |
| O12 | **Wake-storm safety**: wake-concurrency limiter (~20 in-flight) + per-fire jitter; **shared `priva-tenants` namespace**; base-image DaemonSet pre-pull; **kopf** start, controller-runtime escape hatch at **>800 CRs or p95 reconcile >5s** | blueprint §192-193, §345, §318 | §9 |
| O13 | **Admin terminate / wake land as CR patches** (Control Panel → operator); **stop-a-turn does NOT** (gateway Redis signal) | control-panel §7.4, §6.2; agent-gateway §9.4; blueprint §208 | §10 |
| O14 | The operator's **informer cache + CR/status** is a **read source** for the fleet view; T2 Redis is reconstructible from "operator-CR" | blueprint §206; data-spine §4 (`:552` "reconstructible from SQLite/operator-CR/proxy-log") | §1.2, §9.4 |
| O15 | **`runtimeClassName` settable per tenant** (runc default; gVisor/Kata escalation) | blueprint decision 26 §91, §254; agent-runner §9.2 (`:695,711`) | §1.1 |

**No current code is lifted.** §11 lists only net-new artifacts. The single thing this component "replaces" conceptually is the implicit lifecycle of a long-lived uvicorn process — but that process becomes the *pod* (agent-runner.md), and its **birth/wake/sleep/death** become this operator's reconcile.

---

## 1. The `AgentTenant` CRD

### 1.1 `spec` — the declarative footprint (field-accurate sketch)

`apiVersion: priva.io/v1alpha1`, `kind: AgentTenant`, one CR per account, name = `account_id`, in the shared `priva-tenants` namespace.

```yaml
spec:
  accountId: acct_7f3a                       # the tenant key (== CR name); immutable
  desiredState: active                        # active | offboarding | purge   (§8 state machine input)

  image: registry/priva-agent-runner@sha256:…    # pinned by digest (reproducible wake)
  runtimeClassName: runc                       # O15 — settable gvisor/kata per tenant (blueprint dec 26)
  resources: { requests: {cpu, memory}, limits: {cpu, memory} }

  storage:
    sessionVolume: { csiAccessPointRef: ap-acct_7f3a | subPath: acct_7f3a }   # O8 (§7)
    auditVolume:   { csiAccessPointRef: ap-audit-acct_7f3a }                  # SEPARATE (retention split, agent-runner §9.4 :727)
    uid: 21007                               # account→uid; runAsUser MUST equal export-dir owner (agent-runner §9.2 :698)
    gid: 21007

  # egress: DEFERRED (M6) — the egress security gateway is not built "for the moment"; pods call the
  #   provider/internet directly with their own BYOK key. Field reserved for when allow-listing returns (agent-runner §13-2)

  idle:                                       # O6 knobs — all per-tenant (blueprint §278; agent-runner §13-1)
    graceSeconds: 1800                        # ◀ LOCKED 30-min default (agent-runner §13-1). NOT 180 — see §1.3
    minAliveAfterWakeSeconds: 1800            # anti-thrash floor (blueprint §277 minAliveAfterWake)
    terminationDrainMaxSeconds: 300           # involuntary-termination drain bound (agent-runner §8.4 :640)
    checkpointBudgetMb: 2048                  # file-checkpoint cap (agent-runner §4 / R5)

  concurrency: { maxConcurrentSessions: 3 }   # effective = min(mem-derived, this); ENFORCED IN-POD (data-spine quota :210)

  secrets:                                    # what the operator unwraps + injects (§6); pod never decrypts
    dekGeneration: 4                          # account_dek.generation (data-spine §2.9 :234)
    wrappedSecretRef: secret/acct_7f3a        # data-plane handle to the wrapped bundle — M6: INCLUDES the
                                              #   user's own LLM provider key (BYOK) alongside MCP/env secrets
    # (M6) NO virtualKey field — BYOK means no virtual-key indirection and no metering proxy

  wake:
    requestedAt: "2026-06-18T14:02:11Z"       # ◀ THE ONLY scale-up trigger (O3). Patched by the brain / scheduler / admin
```

**Note — `idle`/`concurrency`/`runtimeClassName` have a config home in `quota` (SQLite) too.** The CRD `spec` is the operator's **declarative authority** (blueprint §186); the data-plane `quota` row (data-spine §2.7) is the admin-set config-of-record. They are kept in sync by the Control Panel's **quota dual-write** (blueprint §209: proxy-first then CRD, with a **drift badge**). The operator **acts on the CR**, not the SQLite row — but the two must agree, which is why §1.3's default mismatch matters.

### 1.2 `status` — what the operator publishes (O14)

```yaml
status:
  phase: Running        # Provisioning | Zero | Waking | Running | Idle | Draining | Offboarding | Purged
  readyReplicas: 1
  podName: agent-acct_7f3a-xxxx
  podIP: 10.4.2.17       # mirror of route:{account_id} (data-spine §4 #8) — convenience for the fleet view
  startedAt:  "2026-06-18T14:02:13Z"
  lastWakeAt: "2026-06-18T14:02:11Z"
  lastActivityAt: "2026-06-18T14:07:50Z"   # from the route heartbeat (data-spine §4 #8, ~30s)
  idleSince: null                           # set when route.state=idle observed; cleared on activity
  pinned: true                              # observed SCARD pin:approval:{account_id} > 0  (hard sleep-override, O6)
  wakeInFlight: false
  observedGeneration: 12
  conditions:                               # standard K8s conditions
    - { type: SecretsInjected,     status: "True" }
    - { type: MountsAttached,      status: "True" }
    - { type: NetworkPolicyApplied,status: "True" }
    - { type: PodReady,            status: "True" }
finalizers: [ priva.io/tenant-purge ]       # O11 — guards the PVC against owner-ref deletion (§8)
```

`status` is **derived, not authoritative** — `phase`/`pinned`/`lastActivityAt` are read each reconcile from K8s Deployment status + Redis (`route`/`pin:approval`/`lock:session`), never trusted as durable state. The fleet view reads `status` + the informer cache (§9.4), so it never has to query every pod.

### 1.3 The locked-default inconsistency (concrete owed fix)

`data-spine.md §2.7` declares `quota.idle_grace_seconds INTEGER NOT NULL DEFAULT 180` (`:213`, comment "maxIdle before scale-to-zero"). The **locked** idle grace is **30 minutes = 1800s** (agent-runner §13-1: "the single configurable `idle_grace_seconds` (30-min default, per-tenant CRD)"; agent-gateway §6.4). **`180 ≠ 1800`.** This drill sets the CRD `spec.idle.graceSeconds` default to **1800** (the locked value) and flags **`data-spine §2.7`'s `DEFAULT 180` as stale** — owed a correction so the SQLite config-of-record and the CRD agree. (Until fixed, a tenant provisioned from the SQLite default would sleep after 3 min, not 30 — a real behavioural bug, exactly the kind the drift badge in §1.1 would catch.)

---

## 2. The reconcile loop & managed objects

### 2.1 What the operator manages per tenant

One `AgentTenant` CR owns a set of namespaced objects, all labelled `priva.io/account=<account_id>`:

```
AgentTenant (CR)  ──reconcile──►  Deployment (replicas: 0|1, strategy: Recreate)   ◀ the ONLY replica-count writer
                                  PVC / CSI access-point (session)  ─┐
                                  PVC / CSI access-point (audit)     ├─ NEVER owner-ref-deleted (O11, §8)
                                  Service (per-tenant ClusterIP, headless ok)
                                  NetworkPolicy (per-tenant podSelector; O9)
                                  ServiceAccount + minimal RBAC (the pod's — near-zero K8s rights)
                                  (Secret = NOT pre-created; injected per-spawn into the pod, §6)
```

- **Deployment `replicas: 1`, `strategy: Recreate`** (agent-runner §10 `:797`) — never `RollingUpdate` (two pods writing one PVC would break the single-writer invariant, agent-runner §5). Scale-to-zero = `replicas: 0`.
- **PVC/CSI access-points are decoupled from the Deployment lifecycle** — they persist across every `1→0→1` cycle (the whole point of scale-to-zero: state survives on the PV — agent-runner §8 `:612`). The operator **re-attaches the existing volume**, never recreates it (O4).
- **The Secret is not a standing object** — it is materialized only while the pod runs, as a tmpfs-backed projected Secret injected at spawn and gone on scale-to-zero (§6); this minimizes the window plaintext secrets exist.

### 2.2 The reconcile triggers

kopf handlers fire on: CR create/update (provisioning, spec change), CR `spec.wake.requestedAt` change (wake — §3), the `wake:pod` pub/sub nudge (a reconcile accelerator only, droppable — data-spine §4 #12), a **periodic timer** (the idle sweep §4 + the pin reaper §5), and managed-object events (Deployment/pod status → `status` updates). **Idempotency is the contract**: every handler computes desired-vs-actual and converges; a duplicate wake patch, a dropped nudge, or a double-fire timer all converge to the same state (this is what makes predictive wake safe — §3.3).

### 2.3 Provisioning (CR birth → managed objects at `replicas: 0`)

A new account is **scale-to-zero from birth**:

```
account created (Control Panel /admin/v2/users → data-spine §1.7)
   → Control Panel WRITES the AgentTenant CR (it already holds K8s RBAC for Agentgateway* CRDs — control-panel §10)
   → operator reconcile: create PVC/CSI-AP (session+audit), Service, NetworkPolicy, SA+RBAC, Deployment(replicas:0)
   → status.phase = Zero    (no pod yet; first wake on first login/turn — §3)
```

The Control Panel is the CR writer (it owns user CRUD — control-panel §7.2); the operator is the reconciler. *(Alternative considered: the operator watches data-plane accounts and self-creates CRs. Rejected as primary — it couples the operator to the data-plane account schema and duplicates the Control Panel's existing K8s-write path. The CR-as-the-interface keeps "everyone mutates the CR" clean. Minor; revisitable.)*

### 2.4 The per-tenant NetworkPolicy (O9)

Emitted per tenant (podSelector `priva.io/account=<id>`), in the shared `priva-tenants` namespace (blueprint §345 — **not** a namespace-per-tenant, to bound object sprawl):

```
ingress:  from operator (probes) → allow            (agent-runner §9.6 :738)
          from agentgateway/mesh (steered run traffic + the brain's permission-relay POST) → allow
          pod-to-pod (other tenants) → DENY          (default-deny, blueprint §260)
egress:   to data-plane / Redis → allow
          direct general internet → ALLOW (M6: egress security DEFERRED — was DENY+force-traversal; re-add later)
```

The `state-reader`/`audit-reader` read the **volume**, never the pod, so they need **no** NetworkPolicy rule (data-spine §3.8 `:546`, agent-runner §9.6 `:750`).

---

## 3. Scale-up / wake

### 3.1 `spec.wake.requestedAt` is the only door (O3)

```
ANY waker (brain ext_proc §2.2-cp / scheduler / admin) :
   SET awake:lock:{account_id} NX PX~10s      (data-spine §4 #10 — serialize, self-heal a crashed waker)
   └─won? → CR PATCH spec.wake.requestedAt = now()    ◀ the ONLY scale-up trigger
            (optional) PUBLISH wake:pod:{account_id}   (data-spine §4 #12 — reconcile NUDGE, droppable)

operator reconcile (on the patch OR the nudge OR periodic):
   if Deployment.replicas == 1 and pod Ready  → no-op (already awake; idempotent)
   else  → enter the wake-concurrency limiter (§9.2) → scale 0→1 (§3.2)
```

The operator **trusts only the CR field**, not the pub/sub message — a dropped nudge just defers the same wake to the next periodic reconcile (data-spine §4 #12). `awake:lock` is held by the *waker* (not the operator) so two wakers can't both patch; the operator's own scale action is additionally guarded by the limiter (§9.2).

### 3.2 The scale-up sequence (re-attach, inject, ready)

```
[1] wake-concurrency slot acquired (§9.2)
[2] inject secrets: unwrap account DEK via KMS → materialize the tmpfs projected Secret incl. the user's
        own LLM key (M6: BYOK — no virtual key, no proxy call) (§6)        ◀ BEFORE the pod can boot
[3] scale Deployment 0→1  (replicas:1, Recreate)
[4] re-attach the EXISTING session PVC + audit PVC + Service + NetworkPolicy   (O4 — never recreate)
[5] pod schedules on any node, mounts the RWX PV, runs its §11 boot (agent-runner):
        loads the tmpfs secret bundle (no KMS call) → writes sanitized .claude.json
        → base_url probe → Redis T1/T2 register → orphan-pin purge → Readyz green
[6] operator observes pod Ready → status.phase = Running, lastWakeAt, startedAt, clear status.idleSince
```

The operator does **not** stream or proxy the turn — once the pod is Ready the brain steers the run to it (control-panel §2.3 EPP) and the operator steps out until the next idle sweep or pin change. **Secret injection precedes pod boot** (step 2 before 3) because the pod fail-closes if the bundle is missing (agent-runner §11 B2 `:807`).

### 3.3 Predictive wake is just a wake (idempotency)

Predictive wake (agent-gateway §6.4: CR-patch on WebUI login, before any prompt) is **not a special path** — it is an ordinary `spec.wake.requestedAt` patch. Because §3.1/§3.2 are idempotent, the later turn's wake finds the pod already Ready and no-ops. The anti-thrash floor is `idle.minAliveAfterWakeSeconds` (§1.1): a browse-only login that never sends a turn still holds the pod for the normal idle window, then sleeps (a single `idle_grace`, per the locked decision that **declined** a separate speculative knob — agent-gateway §6.4 / §13).

---

## 4. Scale-down / the idle predicate

### 4.1 The full predicate (O6) — pin is a hard override

The operator sleeps a pod **only** when **all** hold (agent-runner §6.5 `:540`; blueprint §274-278), evaluated each periodic reconcile against Redis + K8s:

```
sleep(account_id) ⇐  SCARD pin:approval:{account_id} == 0            (T1 #4 — pending approvals)   ◀ HARD OVERRIDE
                 AND lock:session:{*} for this account absent/expired (T2 #11 — no in-flight run)
                 AND route.state == idle                              (T2 #8 — pod self-reported idle)
                 AND now − lastActivityAt > idle.graceSeconds
                 AND now − startedAt      > idle.minAliveAfterWakeSeconds   (anti-thrash)
                 AND no live PTY / attached reader                    (route hint; blueprint §276)
```

`pin:approval` non-empty is a **hard refusal**, not one term among equals (agent-runner §6.5 `:540`, blueprint §303): a run parked on a human approval (which can sit for ~24h, the `approval_window`) keeps the pod alive at ~zero spend. The pin is **minted in-pod before the `permission_request` is ever emitted** (agent-runner §6.2), so the operator can never mis-sleep an about-to-park run — the race is closed by construction.

### 4.2 The sleep action

```
predicate true → scale Deployment 1→0 (replicas:0)  → SIGTERM to the pod (§4.3 drain) → pod exits
              → PVC/Service/NetworkPolicy REMAIN (state persists)  → status.phase = Zero
              → the tmpfs Secret vanishes with the pod (§6 — minimal plaintext window)
```

The operator **reads** Redis to decide, but the pod **owns** the lifecycle facts: the pod writes `route.state`/`lock:session` mirrors (~30s heartbeat, agent-runner §8 `:620`) and the `pin:approval` Tier-1 set (§6). A stale/missing `route` is treated as "asleep already" — safe (data-spine §4 #8).

### 4.3 Drain coordination — never kill a working pod (O10)

Two cases, sharply different:

- **Idle-triggered sleep (the operator's own action):** the predicate (§4.1) is a **hard pre-SIGTERM gate** — by construction nothing is running, so the SIGTERM hits an idle pod and the drain is trivial (agent-runner §8.4 `:640`: "Idle-triggered termination is safe by construction"). **The operator never scales-to-zero mid-run or mid-fork** (a fork counts as an in-flight `SessionRun` that pins the pod — agent-runner §3 `:286`).
- **Involuntary termination (node drain / evict / redeploy — not the operator choosing to sleep):** the pod runs its **drain-aware SIGTERM** (let the in-flight turn reach its next `ResultMessage`, fsync, exit), bounded by `terminationDrainMaxSeconds` (~300s). The operator sets **`terminationGracePeriodSeconds` ≥ that bound** on the Deployment (agent-runner §8.4 `:640`, R11 `:837`) so K8s does not SIGKILL mid-drain; an overrun is accept-loss. Pins are deliberately **left untouched** on graceful SIGTERM (a parked approval can't reach a turn boundary in the grace window) — they survive via the 25h index TTL + boot-purge + the reaper (§5; agent-runner §6.6 `:548`).

---

## 5. The pin reaper (the zombie-pod bound, O7)

The pin (`pin:approval`) is the operator's hard sleep-override (§4.1). Its failure mode: a pod **dies after** minting a pin but **before** resolving it (accept-loss) → the pin persists with no live Future → the operator would **refuse to sleep forever** → a zombie pod (blueprint §337 "zombie pod awake forever"). Three layers close it; the reaper is the operator's:

```
periodic reaper (operator):
  for each pin:approval:{account_id} member request_id:
     HGET approval:index:{request_id} .pod  → pod_name
     if Deployment(account_id).readyReplicas == 0 (the pinning pod is gone):
        DEL approval:index:{request_id} + SREM pin:approval (atomic, Lua/MULTI)   → un-pin
  (bounds the zombie window FAR below the 25h index TTL — agent-runner §6.5 :538)
```

The other two layers are the **pod's own boot-purge** (a freshly-booted pod holds zero live Futures, so it unconditionally purges every pin at boot step B6c — agent-runner §6.5 `:538`) and the **25h index TTL** (the final backstop if a pod never reboots). The reaper is what makes the worst case **minutes, not 25h**, when a dead tenant never wakes again. *(The reaper only un-pins pods that are **already gone** — `readyReplicas==0`; it never races a live pod's Future.)*

---

## 6. Secret injection at spawn (O5) — BYOK (M6)

### 6.1 Why the operator (and only the operator) holds KMS

The pod calls **no KMS** and holds **no DEK** (agent-runner §13-3): KMS RBAC is scoped to **the operator only** (M6 dropped the metering proxy — the other former KMS holder). The operator converts the account's wrapped-at-rest secrets into a running pod's plaintext bundle. **Under M6 (BYOK)** that bundle includes the user's **own real LLM provider key** — there is no shared org key and no virtual-key indirection, so the very thing the virtual key existed to protect doesn't exist.

### 6.2 The injection sequence (runs as step [2] of every wake, §3.2)

```
operator.inject_secrets(account_id):
  1. fetch the account's wrapped bundle + wrapped DEK from the data-plane  (data-spine §2.9 account_dek :232)
  2. KMS Decrypt(wrapped_dek, kek_id) → plaintext DEK         (operator-scoped KMS RBAC — agent-runner §13-3)
  3. decrypt the bundle with the DEK → { the user's OWN LLM key (BYOK), stdio-MCP tokens, user env vars }
        → { ANTHROPIC_AUTH_TOKEN = the user's real key, ANTHROPIC_BASE_URL = provider (or user-set), ANTHROPIC_MODEL }
  4. materialize a tmpfs-backed projected K8s Secret, mounted at the pod's SECRET_MOUNT (never on the PVC)
  5. (on scale-to-zero / offboard) the Secret vanishes with the pod — nothing to revoke (the key is the user's own)
```

**One secret class now** (M6 collapses agent-runner SB2's two-class split): every secret — the BYOK LLM key included — is operator-decrypted and injected the same way. **Blast radius = one account** — the operator injects only that account's secrets into that account's pod (agent-runner SB1 `:568`); and because each key is the **user's own**, leaking it to the user's own pod is **not a cross-tenant risk at all** — which is precisely *why* BYOK lets the virtual key go (§12 OP8).

### 6.3 What's deferred / open

- **No metering-proxy seam** (M6) — the former operator→proxy virtual-key mint/revoke call is gone.
- **Token counting is pod-self-reported** (M6): the pod reports the SDK `result` usage to the data-plane each turn (observability-only — nothing is enforced, so a self-reported count is acceptable); the operator is not involved.
- The **org-wide KMS backend** (Vault Transit vs. cloud KMS) remains **open** (data-spine §7-Q1) — now **purely the operator's concern** (agent-runner §13-3 `:866`). DEK rotation: `spec.secrets.dekGeneration` bumps; the operator re-injects on the next wake (old generations kept to decrypt legacy ciphertext — data-spine §2.9 `:234`).

---

## 7. Per-user storage & identity (O8)

### 7.1 The CSI access-point (primary) — root *is* the subdir

The operator emits, per tenant, a **per-user PV / CSI access-point whose root is the account subdir** — so the pod's mount namespace gives it **no handle to siblings** (parent unreachable by construction; natural per-user quota) — agent-runner §9.4 §13-5 (`:727`, `:870`), data-spine §3.8 (`:528`). `subPath:<account_id>` on one shared RWX PVC is the **adequate fallback**. The chosen CSI driver **must support per-user uid + `root_squash`** (NFS export uses `root_squash`, never `no_root_squash` — agent-runner §9.2 `:698`); CSI driver choice is an open infra item (§13).

### 7.2 The account→uid mapping (the operator owns it)

`runAsUser` **must equal** the export-dir owner uid, so POSIX `0700` + foreign-uid is a real backstop (agent-runner §9.2 `:698`). The **operator owns the `account_id → uid:gid` mapping** (`spec.storage.uid/gid`, §1.1) and stamps it into both the securityContext and the volume ownership — they cannot disagree, or the pod can't write its own JSONL.

### 7.3 Asymmetric isolation & the separate audit volume

Isolation is **asymmetric by design** (agent-runner §9.4 `:727`, data-spine §3.8 `:546`): pods get **narrow RW** to their own subdir; the trusted **`state-reader`/`audit-reader` get broad whole-tree RO** across all users (the only whole-tree mounts) to serve the Control Panel's wake-free cross-tenant reads (control-panel §7.5). The operator provisions both: the per-tenant RW access-point **and** (once, platform-level) the readers' RO whole-tree mount. The **audit volume is separate** from the session PVC (retention split: PVC 30d, audit ≥1y — blueprint decision 24; agent-runner §9.4 `:727`).

---

## 8. The offboard → retain → purge state machine (O11)

`desiredState` (§1.1) drives a finalizer-guarded lifecycle so a user's data is **never** deleted by an accidental CR delete:

```
active ──(admin offboard)──► offboarding ──(retention elapsed / admin purge)──► purge ──► (finalizer runs) ──► Purged
   │                              │                                                │
   │  pod scaled to 0;            │  pod stays 0; data RETAINED on the PVC;         │  finalizer deletes PVCs + access-points
   │  CR + PVC kept               │  pre-gate blocks all new turns (data-spine)     │  + Service + NP + SA; THEN removes
   │                              │  (agent-gateway §6.1 status=offboarding)        │  priva.io/tenant-purge → CR GC'd
```

- The **`priva.io/tenant-purge` finalizer** (blueprint §191-192) means deleting the CR **blocks** until the operator has run the purge — the **PVC is never owner-ref-deleted** (it has no ownerReference to the CR; it is reclaimed only by the explicit `purge` step). This makes "retain for N days, then purge" auditable and reversible up to the purge.
- **Offboarding ≠ purge:** an offboarded account's pod is asleep and its turns are pre-gate-blocked (a disabled/offboarding account is answered with no wake — agent-gateway §6.1 `:291`), but its transcripts/audit remain readable by the reader plane until retention elapses.
- **Purge** deletes the managed objects + both volumes, writes a terminal audit record, then drops the finalizer so K8s garbage-collects the CR. *(M6: no virtual-key / spend-reservation to settle — BYOK + metering deferred.)* The offboarding purge walks the **filesystem** (M5 — no `session_index` rows to delete; agent-runner §3.5 `:269`).

---

## 9. Operator HA, scale & wake-storms (O12)

### 9.1 One controller, leader-elected, shared namespace

Start with **kopf** (blueprint §192). A **single active operator** with **leader election** (a standby takes over on crash) reconciles **all** tenants in the shared **`priva-tenants`** namespace (per-tenant namespaces would multiply objects and RBAC — blueprint §345). The base pod image is **pre-pulled by a DaemonSet** so a cold wake doesn't pay an image-pull (blueprint §345).

### 9.2 The wake-concurrency limiter (the storm shield)

A cron-aligned scheduler burst (every tenant's 9am job fires at once) could ask the operator to wake hundreds of pods simultaneously and melt a single-process kopf (blueprint §345). Two defenses:

```
wake-concurrency limiter:  ~20 in-flight scale-ups max (blueprint §345); excess QUEUES (the turn is already
                           buffered in inbox:{account_id} T1, so a deferred wake loses nothing — agent-gateway §6.2)
per-fire jitter:           wakers (scheduler) spread CR patches with jitter (blueprint §318) so the patch storm
                           itself is smeared, not just the operator's response
```

A queued wake is **safe** because the turn is durably buffered (the pod drains the inbox FIFO on boot — agent-gateway §6.2), so wake latency degrades gracefully under a storm instead of dropping turns.

### 9.3 The escape hatch (named, not taken)

kopf is the v1 choice; **controller-runtime (Go) is the named escape hatch at >800 CRs or p95 reconcile >5s** (blueprint §192-193). The reconcile logic in this doc is framework-agnostic (CRD schema + the predicates + the managed-object set don't change), so the migration is a rewrite of the *runtime*, not the *design* — and the trigger is a measured SLO, not a guess.

### 9.4 The informer cache as a read source (O14)

The operator's **informer cache** (all `AgentTenant` `status` + Deployment state, in memory) is what the Control Panel's **cached fleet-projector** reads (blueprint §206) — so `GET /admin/v2/fleet` is a cache read, not a fan-out to every pod. T2 Redis routing state is itself **reconstructible from the operator CR** (data-spine §4 `:552`), so the operator's view is a durable-enough backstop for the ephemeral cache.

---

## 10. Control-plane verbs that land here (O13)

| Verb | Source | Operator action | NOT the operator |
|---|---|---|---|
| **Wake (predictive / on-turn)** | brain ext_proc, scheduler | CR-patch `spec.wake.requestedAt` → scale 0→1 (§3) | — |
| **Terminate a user's pod** | Control Panel `/admin/v2/pods/{a}/terminate` (control-panel §7.4) | scale 1→0 with graceful drain (§4.3) or forced; confirm + audit at the Panel (control-panel §6.2) | — |
| **Wake a user's pod (admin)** | Control Panel `/admin/v2/pods/{a}/wake` | same idempotent wake path (§3) | — |
| **Offboard / purge an account** | Control Panel (desiredState) | the §8 state machine | — |
| **Quota / idle / runtimeClassName change** | Control Panel quota dual-write (blueprint §209) | reconcile the new `spec` (resize, re-label, next-wake honors it) | — |
| **Stop a running turn (no pod kill)** | gateway/brain | — | **NOT the operator** — a Redis cancel signal to the live run (agent-gateway §9.4; blueprint §208) |

The bright line (blueprint §208): **lifecycle (scale/terminate/offboard) = the operator via CR patch; stop-a-turn = the gateway via a Redis signal; never a direct call to a pod.** A pod terminated under an in-flight turn is handled by the gateway/brain as a mid-stream death (clean "session interrupted, re-ask" — agent-gateway §9.2), so the operator owes the run path nothing extra.

---

## 11. New artifacts (code deltas)

No current code is lifted (§0). Everything here is net-new:

| Artifact (NEW) | Contents |
|---|---|
| **`operator/` (kopf controller)** | `crd.py` (the `AgentTenant` schema, §1); `reconcile.py` (the loop + managed-object templates, §2); `wake.py` (CR-patch handler + the concurrency limiter, §3/§9.2); `idle.py` (the sleep predicate + reaper timers, §4/§5); `secrets.py` (KMS unwrap + tmpfs Secret projection incl. the BYOK key, §6 — M6: no virtual-key mint); `storage.py` (CSI access-point + uid mapping, §7); `lifecycle.py` (offboard→retain→purge + the finalizer, §8). |
| **`AgentTenant` CRD manifest** | `priva.io/v1alpha1` (§1); `spec`/`status`/`finalizers`; OpenAPI validation + defaults (**`idle.graceSeconds: 1800`** — §1.3). |
| **Managed-object templates** | Deployment (`replicas:0|1`, `strategy:Recreate`, securityContext per agent-runner §9.2, `terminationGracePeriodSeconds` ≥ drain max, `runtimeClassName`), PVC/CSI access-point (session + audit), per-tenant Service + NetworkPolicy, pod ServiceAccount + minimal RBAC. |
| **Operator Deployment + RBAC** | the operator's own Deployment (leader-elected), KMS-scoped RBAC (the *only* tenant-secret KMS access besides the proxy), K8s RBAC to CRUD `AgentTenant` + the per-tenant objects in `priva-tenants`, watch/patch only. Base-image pre-pull DaemonSet. |
| **data-spine fix (owed)** | correct `quota.idle_grace_seconds DEFAULT 180 → 1800` (§1.3) so the SQLite config-of-record matches the locked CRD default. |

---

## 12. Resolved risks (adversarial pass)

| # | Risk | Severity | Resolution (folded into spec) |
|---|---|---|---|
| OP1 | A zombie pin (pod died post-accept-loss) makes the operator **refuse to sleep forever** | blocker | Three layers: pod boot-purge + the operator **pin reaper** (un-pins pods with `readyReplicas==0`) + the 25h index TTL backstop — worst case is minutes, not 25h (§5; agent-runner §6.5). |
| OP2 | Two wakers double-patch / a nudge races the patch → double scale-up | blocker | `awake:lock` serializes patchers; **CR patch is the sole trigger**, `wake:pod` is a droppable nudge; the operator's scale is idempotent + limiter-gated (§3.1, §9.2). |
| OP3 | The operator scales-to-zero **mid-run or mid-fork** → lost/corrupt turn | blocker | The idle predicate is a **hard pre-SIGTERM gate** (no in-flight run / no pin / no live PTY); a fork pins the pod; only **involuntary** termination drains, bounded by `terminationGracePeriodSeconds ≥ drain max` (§4.3; agent-runner §8.4). |
| OP4 | Pod boots **before** secrets are injected → fail-closed boot loop | major | Injection is step [2] **before** scale 0→1 step [3]; the pod fail-closes only if the bundle is genuinely absent, which the ordering prevents (§3.2, §6.2). |
| OP5 | **kopf single-process** melts under a cron-aligned wake-storm | major | Wake-concurrency limiter (~20) + per-fire jitter; queued wakes are safe (turn buffered in `inbox`); controller-runtime escape hatch at >800 CRs / p95>5s (§9). |
| OP6 | **`idle_grace` default mismatch** (180 in SQLite vs 1800 locked) → pods sleep after 3 min | major | CRD default set to **1800**; `data-spine §2.7` flagged stale; drift badge on the dual-write catches divergence (§1.3). |
| OP7 | A CR delete **owner-ref-cascades** the PVC → user data destroyed | major | PVC has **no ownerReference** to the CR; the **`priva.io/tenant-purge` finalizer** gates deletion behind the explicit purge step (§8; blueprint §191-192). |
| OP8 | Operator KMS RBAC compromise = fleet-wide secret exposure | major | Blast radius per injection = **one account** (operator injects only that account's secrets into that account's pod); KMS scoped to **operator only** (M6 dropped the proxy); pod has no KMS path (§6; agent-runner §13-3). **M6 (BYOK) lowers the stakes**: each key is the *user's own* provider key — there is **no single shared org key** whose loss compromises the fleet. The operator key stays a high-value target — mitigated by RBAC scoping + audit. |
| OP9 | `runAsUser` ≠ export-dir owner uid → pod can't write its JSONL (or POSIX backstop is void) | major | The operator owns the single `account→uid` mapping, stamped into both securityContext and volume ownership (§7.2; agent-runner §9.2). |
| OP10 | Re-attaching the PVC on wake races a not-fully-detached prior pod | major | `strategy: Recreate` (never two replicas) + scale-to-zero completes (pod gone) before the next 0→1; RWX volume tolerates re-attach; single-writer is the in-pod lock, not the mount (§2.1; agent-runner §5). |
| OP11 | Object sprawl (namespace/PVC/NP per tenant) at hundreds of users | minor | Shared `priva-tenants` namespace; one CR + a small fixed object set per tenant; `>800 CRs` is the measured trigger to re-evaluate the runtime (§9.3; blueprint §345). |
| OP12 | Predictive wake holds a pod 30 min on a browse-only login | minor | Accepted — the locked single `idle_grace` (a separate speculative knob was declined, agent-gateway §13); `minAliveAfterWake` only prevents thrash, and admin terminate is the manual lever (§3.3). |
| OP13 | The operator's Deployer is confused with agentgateway's Deployer | minor | Terminology guard: agentgateway provisions **proxy** Deployments and never scales an Agent Runner; this operator scales tenant pods and never touches agentgateway (§ guard, control-panel §1.3). |

---

## 13. Resolved decisions & open items

**Locked (carried from the source specs, realized here):**
1. **Sole scaler via CR patch; CR is the only interface** (O1/O2; blueprint §188).
2. **`spec.wake.requestedAt` is the only scale-up trigger**, `awake:lock`-serialized, `wake:pod` a droppable nudge (O3; agent-runner §8.5).
3. **Idle predicate with pin as a hard override**; idle-sleep is a pre-SIGTERM gate; involuntary termination drains (O6/O10; agent-runner §6.5/§8.4).
4. **Pin reaper** bounds the zombie window below 25h (O7; agent-runner §6.5).
5. **Operator-only secret injection** (KMS unwrap → per-spawn tmpfs Secret); **M6: BYOK** — the bundle carries the user's own LLM key, **no virtual key, no metering proxy** (O5; agent-runner §13-3).
6. **Per-user CSI access-point (root = subdir)**, operator-owned uid mapping, separate audit volume (O8; agent-runner §13-5).
7. **Offboard→retain→purge behind the `priva.io/tenant-purge` finalizer**; PVC never owner-ref-deleted (O11; blueprint §191-192).
8. **kopf + leader election + shared namespace + wake-concurrency limiter**; controller-runtime escape hatch at >800 CRs / p95>5s (O12; blueprint §192-193, §345).
9. **`idle.graceSeconds` default = 1800** (corrects the 180 mismatch — §1.3).

**Open items (genuine, flagged not invented):**
- **CSI driver choice** — must support per-user uid + `root_squash` (§7.1); infra selection, not a design blocker.
- **KMS backend** (Vault Transit vs. cloud KMS) — open (data-spine §7-Q1), now scoped to the **operator only** (M6).
- **CR writer** — Control Panel writes the `AgentTenant` CR (chosen, §2.3); the operator-watches-data-plane alternative is revisitable.
- ~~Metering-proxy virtual-key mint/revoke API~~ — **removed (M6)**: BYOK means no virtual key and no metering proxy.

> **Owed next (revised by M6 + scheduler drill):** the **metering-proxy drill is dropped** — BYOK + token-count-only collapses it (blueprint M6). The **central scheduler** drill is **done** (`scheduler.md`) — it confirmed this operator is the sole scaler the scheduler patches (`spec.wake.requestedAt`, *active-checked* not budget-prechecked under M6) and resolved `PushToChannel`. The **`data-spine §2.7` idle-default fix** is **done** (corrected 180→1800). **All components are now drilled.** The **deep M6 body cleanup is DONE (2026-06-18)** — agent-runner, data-spine, agent-gateway, and blueprint §3/§4 are rewritten M6-correct (the blueprint §2 decisions table / system diagram / §5-7 stay under the supersession banner, as with M1/M2/M5). Remaining (not a drill): only the **channel-connector** sub-pass flagged in agent-gateway §4.4.
