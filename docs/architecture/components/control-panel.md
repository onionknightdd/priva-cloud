---
Status: Draft · Date: 2026-06-18 · Branch: multi-tenant-platform
Parent: ../multi-tenant-platform.md · Component: Control Panel (the unified front-tier app — brain + two faces + admin — behind agentgateway)
Consumes: ./agent-gateway.md (the routing-brain mechanics it re-homes), ./data-spine.md (identity/binding RPCs + two-tier Redis), ./agent-pod.md (the pod run/stream/permission seams) as binding contracts; depends on **agentgateway.dev** (external OSS L7 proxy) as the edge
---

# Priva Control Panel — Component Specification

**Scope.** The **Control Panel** is the **single front-tier application** of the platform — `N` stateless replicas — packaged per the user's locked **option (a)**: one deployable that wears **three hats at once**:

1. **The routing brain** — a gRPC **`ext_proc`** service that **agentgateway** consults per request: resolve identity (`surface → account_id`), target/mint the `session_uuid`, **wake** a sleeping pod, and **steer** the connection to it.
2. **The two web faces** — it serves **`/ui`** (the existing `priva/web` user SPA) and **`/admin`** (the new admin-console SPA).
3. **The platform-management API** — behind `/admin`: fleet, pod lifecycle, budgets, identity/`/link`, audit, and **agentgateway policy** (CRD writes + Prometheus reads).

It is built by **lifting `priva/api`'s admin surface (`routers/admin.py`, 745 lines, prefix `/api/admin`) and its static-SPA serving (`main.py:75,79`) and merging them with the Agent-Gateway routing brain** (`agent-gateway.md`) — but with the brain **re-homed as an `ext_proc` callout behind agentgateway** rather than the client-terminating server it was in `agent-gateway.md`.

> **Decision banner (locked 2026-06-18).** Three platform-shaping decisions stand behind this drill: **(FE) full edge adoption** — `agentgateway.dev` is *the* L7 edge; **(a) one app** — the routing brain and the Control Panel are the same deployable (validating "my gateway service *is* the control panel"); **(L) Ops-overview admin layout**. This document **absorbs `agent-gateway.md`'s brain mechanics by reference** (cited, not duplicated). Because full edge adoption splits the old "stateless front door" into *agentgateway-edge + this app's ext_proc brain + the channel-connector*, **`agent-gateway.md` needs a reconciliation pass** (tracked at the end of §0); until then, where the two conflict, **this document wins** for the edge/packaging shape and `agent-gateway.md` remains authoritative for the brain's internal mechanics (identity, minting, relay, fan-out, lease). *(Update: the `agent-gateway.md` reconciliation pass is **done** — 2026-06-18.)*

> **M6 banner (BYOK + metering deferred, locked 2026-06-18 — supersedes §8's metering narrative).** After this drill, the platform locked **BYOK + token-count-only** (blueprint **M6**): users bring their **own real LLM keys** (no virtual keys), **spend tracking/enforcement is deferred** (no reserve-before, no `$`, no ledger, no `402`), token usage is **pod-self-reported**, the **metering-proxy component is dropped**, and the **egress security gateway is deferred**. This reworks **§8** (no proxy / ledger / `backendRef`), **§9** (token source = pod self-report, **not** `agentgateway_gen_ai_client_token_usage` — with BYOK the LLM call bypasses agentgateway), and the **Budgets** surfaces (§6.1 / §7.3 → deferred / display-only). The agentgateway rate-limit→ledger seam is **withdrawn for now** (re-addable). Reconciled inline below.

> **Terminology guard — THREE different "gateways," do not conflate.**
> 1. **agentgateway** (agentgateway.dev) — the **external** Rust L7 proxy; Kubernetes infrastructure (Helm + CRDs); **not our code**. The edge pipe. Knows nothing of "tenant/session/pod."
> 2. **the brain** — *our* per-request routing logic, this app's `ext_proc` face. Formerly drilled standalone as "the Agent Gateway" (`agent-gateway.md`).
> 3. **the egress / token-count path** — the *outbound* LLM/MCP path. **M6: no metering proxy** — BYOK pods call the provider directly; the platform only **counts tokens** (pod-self-reported); the egress security gateway is deferred (agent-pod §9.6/§13-2). Distinct from both.
>
> "**Control Panel**" = this whole app (brain + faces + admin). It is **not** agentgateway's built-in admin UI (`:15000/ui/`), which is **read-only in Kubernetes mode** and serves proxy inspection only (§9.4).

Section map: §0 packaging (option a) + lift/strip · §1 agentgateway in front (responsibility split + the CRD wiring) · §2 the `ext_proc` brain (protocol, wake-not-native, steer) · §3 run/stream through agentgateway · §4 permission relay + no-impersonation under the edge · §5 the two faces (serving + shell/admin API + auth gating) · §6 the admin dashboard (Ops-overview layout, sections, dangerous ops) · §7 admin API → platform mapping · §8 metering/MCP/auth responsibility split · §9 observability source for the dashboard · §10 code deltas · §11 resolved risks · §12 resolved decisions.

> **Verification note.** Priva code claims are cited `file:line` against the installed tree and verified. **agentgateway** claims are grounded in its official docs (v1.2.x "latest") via a 3-agent research pass on 2026-06-18, **not** by running it; load-bearing-but-unverified items are flagged inline (`ext_proc` is doc-flagged "very experimental"; MCP upstream credential injection is undocumented; "12+ providers" is unenumerated marketing).

---

## 0. Packaging (option a) & what is lifted / stripped

### 0.1 One app, three interfaces, `N` stateless replicas

The Control Panel is a single Deployment, `N` identical replicas, **stateless** (it coordinates through Redis + the data-plane exactly as `agent-gateway.md` §1 established — that property is unchanged and is *why* option (a) scales horizontally). It exposes three network interfaces on the pod, all fronted by agentgateway:

| Interface | Port (example) | Consumed by | Purpose |
|---|---|---|---|
| **gRPC `ext_proc`** | `:9000` | agentgateway (per request) | the routing **brain** — identity/session/wake/steer (§2) |
| **HTTP — faces** | `:8080` | agentgateway routes `/ui`, `/admin` | serve the user SPA + admin SPA + the shell/admin API (§5) |
| **HTTP — internal** | `:8081` | scheduler, operator, self | `PushToChannel` seam (agent-gateway §8.4), health, channel-connector control |

The **channel-connector** (the WeCom socket owner, agent-gateway §5) **stays a separate Deployment** — it cannot fold into this app because it dials/holds an **outbound** socket under a single-owner lease (agentgateway is inbound-only; §1.1, agent-gateway §5). It sits *behind* agentgateway as a backend and shares the same Redis/data-plane (agent-gateway §5 unchanged).

### 0.2 What this app lifts from `priva/api` (beyond the agent-gateway §0 table)

`agent-gateway.md` §0 already enumerates the channel-daemon / auth / static lift. Option (a) adds the **admin surface** lift, which `agent-gateway.md` deferred ("Admin routers → control panel"):

| Subsystem (current code) | Disposition | Where it goes / what changes |
|---|---|---|
| Admin API (`routers/admin.py:45-46` prefix `/api/admin`; `/users` CRUD `:52,58,88,157`; `/stats` `:193`; `/audit` `:242`; `/scheduler/*` `:293,308`; per-user skills/mcp/hooks `:326,362,418,435`; platform config `:480-711`) | **LIFT + RESHAPE** | Becomes the Control Panel admin API. User store → data-plane Accounts/Identities RPCs (data-spine §1.7). **New** multi-tenant verbs added (fleet, pod-terminate/wake, `/link` admin, budgets, agentgateway-policy, cross-user sessions — §7). Single-machine reads (local FS, APScheduler) → data-plane + Prometheus + state-reader + central scheduler. |
| `admin_files.py` (227 lines, file admin) | **RESHAPE** | Cross-user file/transcript browse goes through the **state-reader** (broad RO mount, data-spine §3.8) — never a local FS path; never wakes a pod (§7.5). |
| Static SPA + `/static` mount (`main.py:75,79` `StaticFiles(WEB_DIST, html=True)`) | **LIFT + SPLIT** | Two build targets from `priva/web`: the **user SPA** at `/ui` and the **admin SPA** at `/admin` (§5). The app serves the built assets (no object storage — C2); agentgateway path-routes to it. |
| `CORSMiddleware allow_origins=["*"]` (`main.py:181`) | **MOVE to the edge** | CORS/TLS/origin policy is now an **agentgateway** `AgentgatewayPolicy` (`frontend`/`cors`), not app middleware (§1). |
| Router fan-in (`main.py:203-219`: auth/admin/agent/skills/mcp/scheduler/channels/…) | **SPLIT** | *User-facing* runtime routers (agent run/stream) → reached via the **brain-steered `/v1`** to the **pod**, not this app (the app is off the agent hot path — option a, §3). *Admin/config* routers → this app's admin API. *scheduler/channels* → central scheduler / channel-connector. |

### 0.3 Reconciliation owed to `agent-gateway.md`

Full edge adoption changes `agent-gateway.md`'s framing (it assumed a hand-rolled client-terminating front door). The reconciliation pass (next drill step) must re-point it to: **(i)** the client transport is terminated by **agentgateway**, not the app (its §4 SSE/WS termination → §3 here); **(ii)** JWT verify / rate-limit / CORS / TLS move to agentgateway policies (its §10 → §1/§8 here); **(iii)** the brain (its §2/§3/§6/§7) is an `ext_proc` callout (this §2/§4); **(iv)** the channel-socket lease (its §5) stays, in the separate channel-connector. The brain's *internal* logic (resolution pipeline, mint rules, relay protocol, fan-out) is **carried over unchanged** and remains cited to `agent-gateway.md`.

---

## 1. agentgateway in front: the responsibility split & CRD wiring

### 1.1 Who owns what

agentgateway is the **edge pipe**; this app is the **brain + faces**. The split is exact:

| Concern | Owner | How (agentgateway CRD / app mechanism) |
|---|---|---|
| TLS termination, HTTP/2, WS/SSE streaming | **agentgateway** | `Gateway` listener + native upgrade bridge; **no default request timeout** (long turns safe — §3) |
| JWT signature/issuer/audience verification | **agentgateway** | `AgentgatewayPolicy.spec.traffic.jwtAuthentication` (`providers[].issuer/.audiences/.jwks`) |
| Path routing `/ui` `/admin` `/v1` | **agentgateway** | `HTTPRoute` rules (§1.2) |
| Edge authorization (admin-only `/admin`) | **agentgateway** | `AgentgatewayPolicy.spec.traffic.authorization` CEL: `jwt.role == "admin"` (`jwt.<claim>` accessor) |
| Coarse rate-limit / budget backstop (429) | **agentgateway** | `traffic.rateLimit.global` (`unit: Tokens`) → **our** ratelimit service (§8) |
| Per-request **business** decision (who/which/wake) | **this app (brain)** | `traffic.extProc` callout → `:9000` (§2) |
| `session_uuid` mint, `is_first_run`, identity/`/link` | **this app (brain)** | unchanged from agent-gateway §2/§3 (M5) |
| Serve SPAs + admin API | **this app (faces)** | `HTTPRoute` `/ui`,`/admin` → app `:8080` (§5) |
| ~~Authoritative spend ledger / virtual-key issuance / `$`~~ — **DEFERRED (M6)** | — | BYOK + token-count-only; no proxy / ledger / `$` for now (§8) |
| Outbound bot socket (WeCom) | **channel-connector** | agentgateway is inbound-only — separate Deployment (§0.1) |

**Load-bearing correction (restated):** even under full edge adoption, agentgateway does **not** absorb the brain — its only extensibility is **out-of-process callouts** (`ext_proc`, `ext-authz`, CEL transform, guardrail webhook); there are **no in-process plugins** (no Wasm/Rust/Lua). So the brain is *our* code that agentgateway *calls*, never code that runs *inside* agentgateway.

### 1.2 The CRD wiring (the "implement with agentgateway" core)

Field-accurate sketch (group `agentgateway.dev/v1alpha1` + Gateway API; not full YAML):

```
Gateway (gatewayClassName: agentgateway)
  listeners: [{ name: https, port: 443, protocol: HTTPS, tls: {…} }]
     │  Deployer auto-provisions the proxy Deployment+Service for this Gateway
     ▼
HTTPRoute  ui      : match PathPrefix /ui    → backendRef: control-panel-svc:8080
HTTPRoute  admin   : match PathPrefix /admin → backendRef: control-panel-svc:8080
HTTPRoute  runtime : match PathPrefix /v1    → backendRef: InferencePool agent-pods
                                                 (EPP = control-panel ext_proc, §2.3)

AgentgatewayPolicy  edge-auth   (targetRefs: Gateway)
  traffic.jwtAuthentication: { providers: [{ issuer: priva-cp, jwks.remote: {…} }] }   # verify platform JWT
  frontend.cors / tls / accessLog(OTLP) / tracing(OTLP) / metrics                      # was main.py:181 CORS

AgentgatewayPolicy  admin-gate  (targetRefs: HTTPRoute/admin)
  traffic.authorization: { action: Allow, policy.matchExpressions: ['jwt.role == "admin"'] }  # 403 otherwise

AgentgatewayPolicy  runtime     (targetRefs: HTTPRoute/runtime)
  traffic.extProc: { backendRef: control-panel-svc:9000 }     # ← THE BRAIN callout, per request
  traffic.rateLimit.global: { descriptors: [{ unit: Tokens,
        entries: [{ expression: 'jwt.sub' }] }],  backendRef: metering-ratelimit-svc }  # coarse 429 backstop (§8)

AgentgatewayBackend  agent-pods : InferencePool (group inference.networking.k8s.io)    # the steered target (§2.3)
AgentgatewayBackend  llm/mcp    : spec.ai.* / spec.mcp.*                               # egress — metering drill (§8)
```

Notes: the platform JWT is **minted by this app** (the brain, on login / per IM turn — agent-gateway §2.4) and **verified by agentgateway** at the edge; `jwt.role`/`jwt.sub`/`jwt.account_id` claims drive the admin gate and the rate-limit key. The `InferencePool` + EPP pattern is how the brain steers to a woken pod (§2.3) — the one production-proven `ext_proc`-driven dynamic-endpoint mechanism in agentgateway.

### 1.3 agentgateway control-plane vs data-plane (operational shape)

agentgateway is itself two pieces (research §5): a cluster **controller** (krt collections + xDS server + **Deployer**) in namespace `agentgateway-system`, and per-`Gateway` **proxy** Deployments it provisions, fed config over **xDS** (delta ADS). We install both via Helm OCI (`cr.agentgateway.dev/charts/agentgateway` + `-crds`). Proxy HA/scale is set via **`AgentgatewayParameters`** (`deployment` replicas, `horizontalPodAutoscaler`, `podDisruptionBudget`). The Control Panel **does not** run or fork agentgateway; it **configures** it (CRDs via the K8s API) and **observes** it (Prometheus) — §7/§9.

---

## 2. The `ext_proc` brain (per-request routing)

### 2.1 The callout protocol

For every `/v1` request, agentgateway opens a gRPC `ext_proc` stream to the app `:9000` (Envoy `ext_proc.proto`-compatible). The brain may read request headers + body, **mutate** headers (incl. the `:authority` pseudo-header), **short-circuit** with an immediate response, and emit dynamic metadata. The brain runs **exactly the agent-gateway §2 resolution pipeline**, unchanged in logic, now expressed as `ext_proc` operations:

```
ext_proc(request):
  ├─ read JWT claims (agentgateway already verified the signature) + body {session_id?, text}
  ├─ ResolveIdentity(surface, user) → account_id            (agent-gateway §2.2; data-spine §1.7; Redis-cached)
  │     └─ miss/unlinked → immediate-response: /link challenge (§2 a-g §2.5)  ── NO wake
  ├─ pre-gate(account_id): active? under hard cap?          (agent-gateway §6.1)
  │     └─ disabled/capped → immediate-response: honest reason            ── NO wake
  ├─ session target + MINT + is_first_run                   (agent-gateway §3, M5: local mint / binding CAS)
  ├─ WAKE if asleep, HOLD until ready                       (§2.2 — NOT native to agentgateway)
  └─ STEER: return the woken pod's endpoint                 (§2.3)
            + inject signed {account_id, session_uuid, is_first_run, actor} header  (agent-gateway §2.4)
```

The serialized assertion the pod trusts (agent-gateway §2.4/§13-1: mTLS + short-TTL signed JWT) is **injected by the brain as a request header**; agentgateway forwards it to the pod. *(Doc caveat: forwarding a derived claim downstream as a header is the documented `ext_proc`/transformation path; agentgateway's own JWT-claim→header passthrough is undocumented — we set it explicitly in `ext_proc`, §11-R5.)*

### 2.2 Wake is **not native** — the brain holds the stream

The single hardest consequence of full edge adoption (research §2): **agentgateway has no scale-to-zero wake.** A request to a pod at 0 replicas yields `NoHealthyEndpoints` → **immediate 503, not retried** (retries cover `UpstreamCallFailed/Timeout/DnsResolution`, not "no endpoints"). There is no activator, no request-holding queue. So the brain implements wake itself, **inside the `ext_proc` callout**, which can block the gRPC stream while it works:

```
brain.wake_and_hold(account_id):
  if route:{account_id} present (T2 #8) → pod awake → return pod_ip            (agent-gateway §9.1)
  else:
     SET awake:lock:{account_id} NX PX~10s → CR-PATCH AgentTenant spec.wake     (agent-gateway §6.3 — operator scales 0→1)
     BUFFER the turn to inbox:{account_id} (durable) so a hold-timeout never loses it  (agent-gateway §6.2)
     await readiness:  poll route:{account_id} / pod readiness  (bounded; predictive-wake §2.4 makes this usually instant)
     ready  → return pod_ip
     exceeds hold bound → immediate-response "waking, retry in a moment" (turn already buffered; pod drains on boot)
```

**Predictive wake (agent-gateway §6.4) is what keeps this off the critical path:** on WebUI login the brain pre-gates + CR-patches the wake immediately, so by the first turn the pod is already `route`-present and the `ext_proc` hold returns instantly. The cold path (IM first message, or a turn before the pod is warm) is the only one that actually holds — bounded, buffered, and degradable to a "retry" short-circuit.

### 2.3 Steer to the woken pod (EPP pattern)

agentgateway will **not** re-route a static backend on a mutated `:authority` (the `clear_route_cache` ext_proc op is **silently ignored**). Two supported mechanisms exist; we use the first:

- **EndpointPicker (EPP) / `InferencePool` — chosen.** The `/v1` `HTTPRoute` targets an `InferencePool` whose **EPP is this app's brain**. agentgateway calls the EPP over ext_proc; the brain returns the header **`x-gateway-destination-endpoint`** = the woken pod's `ip:port` (which it already holds in `route:{account_id}`); agentgateway connects there. This is the production-proven dynamic-endpoint path and needs **no per-tenant CRD** (one pool, brain picks the endpoint per request).
- **`dynamicForwardProxy` + `:authority` — alternative.** An `AgentgatewayBackend.spec.dynamicForwardProxy` resolves the upstream from `:authority` via DNS; the brain (PreRouting) sets `:authority` to the per-tenant Service DNS *after* readiness (a `Backend::Dynamic` is the one case where `:authority` mutation re-resolves). Rejected as primary: adds a per-tenant Service+DNS dependency and DNS-propagation timing on a freshly-woken pod.

The EPP return is the **same step** as "return pod_ip" in §2.2 — wake/hold and steer are one `ext_proc` exchange.

### 2.4 What carries over unchanged (cited, not duplicated)

Identity resolution + `/link` (agent-gateway §2), `session_uuid` minting + `is_first_run` under M5 (agent-gateway §3, §13-4), the pre-gate (agent-gateway §6.1), durable buffer + ack (agent-gateway §6.2), predictive wake (agent-gateway §6.4), group sender-validation/no-`chat_id`-key (agent-gateway §2.6/§13-5). These are the brain's internals and remain authoritative in `agent-gateway.md`; this app *is* their runtime home.

---

## 3. Run/stream through agentgateway

### 3.1 The path

Once the brain steers (§2.3), the turn streams **client ⇄ agentgateway ⇄ pod** — the Control Panel app is **not** on this path (option a keeps admin/serving off the agent hot path; the brain's involvement ended at the `ext_proc` decision):

```
client (WS primary / SSE fallback — agent-gateway §13-3)
   ⇄ agentgateway  (TLS · verified JWT · ext_proc-steered to pod_ip · streams bytes, no re-serialize)
   ⇄ Agent Pod     (run/stream; serialize_message in-pod — agent-pod §0/§4)
```

agentgateway supports **WebSocket** (native upgrade bridge), **SSE** (unbuffered by default), HTTP/2 and gRPC streaming, and — critically — **no default request timeout**, and its timeouts **do not kill an active stream** (research §3). So long agent turns (minutes of token streaming, a parked permission `await`) are safe once routed. The serializer stays in-pod (agent-gateway §4.2): agentgateway re-frames bytes, it does not re-serialize.

### 3.2 The first-byte caveat

The one timeout risk (research §3): a *configured* `request_timeout` aborts a backend slow to send **first headers** — relevant only if a **cold wake** runs long. Mitigations already in place: (i) predictive wake (§2.4) makes the warm path the norm; (ii) the cold path's hold lives in the **`ext_proc` stream** (its own message timeout, set generously), and on overrun degrades to a buffered "retry" short-circuit (§2.2) rather than a hung request; (iii) leave `request_timeout` unset on `/v1` (the default) so only the deliberate ext_proc bound governs. Cap/drain backpressure (pod **429** over cap, **503** draining — agent-pod §5.5/§8.4) is surfaced by agentgateway to the client; on 503 the brain re-buffers to the inbox (agent-gateway §4.2), unchanged.

---

## 4. Permission relay & the no-impersonation gate under the edge

The cross-replica permission relay (agent-gateway §7) is **unchanged in mechanism** and lives in this app, because it is *state coordination through Redis*, not something agentgateway can do (agentgateway has no notion of a parked, hours-later approval bound to a specific pod instance). Two edge deltas:

- **Reply transport.** With WS primary (agent-gateway §13-3), the human's approval rides the same agentgateway-terminated socket as the stream. agentgateway routes that WS message to the app/pod; the brain resolves it via `HGETALL approval:index:{request_id}` → exact `pod`, relaying cross-replica via `in:reply:{request_id}` if needed (agent-gateway §7.3, verbatim).
- **No-impersonation (agent-gateway §7.4 / agent-pod §13-6).** The gate — *answerable only by the owning account* — is enforced in **two** layers as before, now with agentgateway as the outer auth: agentgateway has already verified the answerer's JWT (`jwt.account_id`) at the edge; the brain asserts `answerer.account_id == index.account_id` (**403** on mismatch); the pod independently re-asserts on `/permission/respond`. An impersonating admin's *answer* is rejected even though their audited *spend/tool* impersonation survives (decision-10 reversal). The IM `asker_id` second check (agent-gateway §7.4) is preserved.

The relay's "holds no Future" invariant (agent-gateway §7.1) is intact: the Future is in-pod; agentgateway holds bytes; this app holds only Redis coordination.

---

## 5. The two faces (serving, shell/admin API, auth gating)

### 5.1 Build targets from `priva/web`

Two SPA build targets from the existing `priva/web` (Vite 6 · React 18 · Zustand 5 · lucide · react-markdown · tailwind 3.4), sharing the locked design-spec lib (`priva/web/design-spec.md`; CLAUDE.md design system):

| Route | Build target | Source | Auth |
|---|---|---|---|
| **`/ui`** | **user SPA** | the existing `priva/web` app (chat/canvas/sessions/settings — `src/components/{chat,canvas,settings,…}`); its API base points at the **edge** (agent runtime → `/v1` → brain → pod) | platform JWT (`jwt.role` any) |
| **`/admin`** | **admin SPA** | the existing `src/components/admin/*` promoted to its own build, **extended** for multi-tenant (fleet/lifecycle/budgets/policies — §6) | platform JWT **`jwt.role == "admin"`**, enforced at the edge (§1.2 `admin-gate`) + re-checked in the admin API |

The app serves the built assets (replacing `main.py:75,79`'s single-SPA mount; no object storage — C2). The existing user SPA is served **as-is** (it already conforms to the design-spec); only its API base changes (the repo already parameterizes this — `VITE_API_TARGET`, commit `0a6ef62`). agentgateway path-routes `/ui` and `/admin` to the app (`:8080`, §1.2).

### 5.2 The shell API vs the admin API

- **Shell API** (`/ui` runtime, this app `:8080`): login (mint the platform JWT — agent-gateway §2.4), account info, **wake-free** session list (M5: SDK when the pod is awake, per-account JSONL glob via state-reader when asleep — agent-gateway §8.3, data-spine §3.9). It does **not** carry agent turns (those go `/v1` → brain → pod). On login it triggers **predictive wake** (§2.4).
- **Admin API** (`/admin`, this app `:8080`, lifted from `routers/admin.py`): everything in §7. Double-gated (edge `jwt.role==admin` + app re-check), every state-changing call **audited before return** (CLAUDE.md dangerous-ops; data-spine audit JSONL).

---

## 6. The admin dashboard (Ops-overview layout)

Locked layout: **sidebar + Ops-overview landing.** Full design-spec compliance (GitHub-dark variables only, Noto Sans/JetBrains Mono, 240px sidebar, **status via 2px left-border** never dots, skeleton-shimmer load, copy-on-hover, no box-shadow, ≤4px radius, 200ms modals / 150ms hovers, 480px right detail drawers).

```
┌────────┬──────────────────────────────────────────────────────────┐
│ Priva  │  Overview                                      [⟳]         │
│ admin  │  ┌ RUN 12 ┐┌ IDLE 3 ┐┌ ZERO 27 ┐┌ $48.2 today ┐┌ ERR 1 ┐  │
│────────│  └────────┘└────────┘└─────────┘└─────────────┘└───────┘  │
│▎Fleet  │  ── Fleet ───────────────────────────────────────────────  │
│ Users  │  USER    STATE  SPEND   LAST     POD          ⋯            │
│ Budgets│ ▎alice   run    $2.10   2m       pod-alice    [term][imp]  │  ← 2px left-border = status
│ Sessions│  bob     idle   $0.40   1h       pod-bob      [term][imp]  │
│ Audit  │  carol   zero   $5.80   3d       —            [wake]       │
│Policies│  ── live activity ───────────────────────────────────────  │
│Settings│  14:02 alice wake(login)  · 14:01 bob 429  · 13:58 dele 0→1 │
└────────┴──────────────────────────────────────────────────────────┘
     row click → 480px detail drawer ▶  (sessions · spend graph · pod events · [Terminate] [Impersonate])
```

### 6.1 Sidebar taxonomy & each section

| Section | Shows | Backed by (§7) |
|---|---|---|
| **Fleet** (landing) | the stat row (run/idle/zero/spend/errors) + Fleet table + live-activity strip | Prometheus + K8s pod list + data-spine + audit tail |
| **Users** | identities, `identity_link` bindings, access modes (ALL/whitelist/private), API keys + **BYOK LLM key** (M6), `/link` admin | data-spine (lifts `admin.py:/users`) |
| **Budgets** | **(M6: deferred)** token usage **display-only** (pod-self-reported); no spend caps / `$` / enforcement yet | pod-self-reported token metric; spend machinery deferred (M6) |
| **Sessions** | cross-user session browser (admin-wide) | state-reader whole-tree scan (wake-free; rare/accepted-slower — agent-gateway §8.3) |
| **Audit** | the audit JSONL (lifts `admin.py:/audit:242`); filter by account/actor/action | data-spine audit (per-user JSONL, C1) |
| **Policies** | agentgateway config view/edit: routes, JWT/auth, guardrails/PII, MCP tool scope, rate-limits | K8s CRDs (`AgentgatewayPolicy`/`Backend`) |
| **Settings** | platform config (lifts `admin.py:/presetprompt,/clipath,/risky-tools,/sensitive-patterns,/system/plugin:480-711`) | data-plane config |

The landing stat row + live-activity is the **only new at-a-glance surface**; everything else mirrors the existing admin SPA's table/drawer patterns.

### 6.2 Dangerous operations (CLAUDE.md + agent-gateway §9.4)

Per CLAUDE.md's dangerous-ops table, each mutating action shows a confirmation dialog **before** executing and is **audited before return**:

| Action | Confirmation | Executes via |
|---|---|---|
| **Terminate a user's pod** | dialog + **type the username** to arm + red confirm button (shows current state: running turn? draining?) | Operator CR patch 1→0, graceful drain (agent-pod §8.4) or forced — §7.4 |
| **Stop a running turn** (no pod kill) | dialog + red confirm | Redis cancel signal to the live run (agent-gateway §9.4) |
| **Disable / offboard an account** | dialog + type-name | data-spine status → pre-gate blocks future turns (agent-gateway §6.1) |
| **Edit a guardrail / budget policy** | diff preview + confirm | `AgentgatewayPolicy` CRD write (eventual-consistency note, §11-R6) |
| **Impersonate (act-as)** | dialog naming the target + audit banner | audited; **cannot answer approvals** (§4) |

Pod terminate maps directly to the agent-gateway §9.4 locked requirement; **gateway/edge impact is none new** — a vanishing pod is handled as a mid-stream death (agent-gateway §9.2: clean "session interrupted, re-ask," no auto-retry).

---

## 7. Admin API → platform mapping

Every admin action resolves to one of four backends — **K8s API (CRDs)**, **the Operator (CR patch)**, **Prometheus**, or **the data-plane/state-reader** — never to agentgateway's (read-only) admin UI.

### 7.1 Fleet (the landing data)
`GET /admin/v2/fleet` composes: **K8s** (`AgentTenant`/pod list → state run/idle/zero), **Prometheus** (`agentgateway_gen_ai_client_token_usage_sum` by `user_id` → spend; `agentgateway_requests_total` → errors), **data-spine** (account roster), **Redis `route`** (live pod state). The stat row is PromQL aggregates; the table is the per-account join. (Replaces `admin.py:/stats:193`, now multi-tenant + pod-aware.)

### 7.2 Users & `/link` (identity admin)
Lifts `admin.py:/users` CRUD (`:52,58,88,157`) onto data-spine Accounts/Identities RPCs (data-spine §1.7). **New:** view/sever `identity_link` bindings, set per-channel access mode (ALL/whitelist/private — agent-gateway §10.2), **manage the user's BYOK LLM key** (M6 — store/rotate the user's *own* provider key in the data-plane `secret` table, envelope-encrypted; the operator injects it at spawn, operator §6; **no virtual keys**), and an admin-side `/link` view (codes are user-minted, agent-gateway §2.5).

### 7.3 Budgets — DEFERRED (M6)
There is **no spend cap, no `$`, no ledger, no reserve-before** "for the moment" (BYOK + token-count-only — blueprint M6). The Budgets screen is **display-only**: per-account/model **token usage** from the pod-self-reported metric (§9). The deferred-but-re-addable path (a `$` cap via price card, or a token cap enforced at the edge via `AgentgatewayPolicy.traffic.rateLimit.global unit:Tokens`) is described in §8; until then the screen shows usage, not enforcement.

### 7.4 Pod lifecycle (terminate / wake)
`POST /admin/v2/pods/{account}/terminate` and `/wake` → **Operator** CR patch (the sole scaler — agent-pod §8.5; the Control Panel never scales directly). Terminate = scale 1→0 with graceful drain (agent-pod §8.4) or forced; wake = `spec.wake.requestedAt` patch (same idempotent path as the brain's wake, agent-gateway §6.3). Confirmation + audit per §6.2. Operator mechanics are the **Operator drill**.

### 7.5 Sessions & Audit (wake-free, cross-user)
Cross-user session/transcript browse goes through the **state-reader** (broad RO mount across all PVCs — data-spine §3.8) by globbing `projects/*/<uuid>.jsonl` (M5) — **never waking a pod**, never a local FS path (replaces `admin_files.py`). Admin-wide scan is rare and accepted-slower (agent-gateway §8.3). Audit reads the per-user audit JSONL (C1), lifting `admin.py:/audit:242` filters.

### 7.6 Policies (agentgateway config)
`GET/PUT /admin/v2/policies/*` read/write `AgentgatewayPolicy`/`AgentgatewayBackend` via the **K8s API** (the only config surface agentgateway exposes — no config-write API; research §1). Edits show a diff + confirm (§6.2) and are subject to **reconcile lag** (xDS push is eventually-consistent — §11-R6). Guardrails/PII (`backend.ai.promptGuard`), MCP tool scope (`backend.mcp.authorization` CEL), routes, and rate-limits are all CRD edits here.

---

## 8. Metering / MCP / auth responsibility split

> **M6:** the metering-proxy half of this section is **withdrawn** (see the LLM bullet). MCP *scope/observe* (agentgateway) and *Auth* (agentgateway verifies; we mint the platform JWT) are **unchanged**.

- **LLM budgets — DEFERRED (M6).** There is **no metering proxy, no virtual key, no spend ledger, no reserve-before, no `$`**. Users **bring their own LLM keys** (BYOK — operator-injected, operator §6); the platform only **counts tokens**, **pod-self-reported** from the SDK `result` usage (observability-only — nothing enforced). agentgateway *could* later enforce a token-budget 429 (`traffic.rateLimit.global unit:Tokens`) and the old `backendRef`→ledger seam *could* return — both **deferred, not deleted**, switching on without re-plumbing once a price card / cap is wanted. *(Was: our metering-ledger as system-of-record with the rate-limit `backendRef` pointing at it — withdrawn under M6. Note: with BYOK + the deferred egress gateway, LLM calls go pod→provider **directly**, bypassing agentgateway, so even agentgateway's token counters don't see them — hence pod-self-report, §9.)*
- **MCP egress — PARTIAL.** agentgateway does tool-level **CEL authorization** (`backend.mcp.authorization`, per-tool, per-`jwt`-claim), federation, all transports, and tool-call counters — a genuine fit for *scope + observe*. **Gap:** injecting *per-tenant upstream credentials* into an MCP server is **undocumented** (generic `backend.auth`/`headerModifiers` may apply — unverified, §11-R7). The agent-pod §13-2 egress-gateway decision is **deferred (M6)** — the egress gateway is not built "for the moment"; MCP *scope+observe* stays agentgateway's, and **per-tenant MCP upstream credentials are operator-injected** (the BYOK-style secret bundle, operator §6), not proxy-injected.
- **Auth — STRONG.** JWT/OIDC/mTLS/API-key/ext-authz/RBAC(CEL) all native. We mint the platform JWT (brain), agentgateway verifies it; `jwt.role`/`jwt.account_id`/`jwt.sub` drive admin-gate, rate-limit key, and MCP scope. (OIDC *login* is standalone-only in agentgateway — not a K8s policy field — so **we** own login/JWT-mint regardless; §11-R8.)

---

## 9. Observability source for the dashboard

The dashboard reconstructs everything from **Prometheus + OTLP + ledger + data-spine** (agentgateway has **no config-state or budget-state read API**):

| Dashboard datum | Source | Exact name |
|---|---|---|
| Per-account/model token usage | **pod self-report (M6)** → data-plane / pod `/metrics` | the SDK `result` usage per turn — `agentgateway_gen_ai_*` does **not** see BYOK LLM calls (they go pod→provider directly, bypassing the edge) |
| Request volume / errors | Prometheus `:15020` | `agentgateway_requests_total{route,backend,status}` |
| MCP tool calls | Prometheus `:15020` | `tool_calls`, `tool_call_errors`, … |
| Control-plane health | Prometheus (`:9092/metrics`) | `agentgateway_controller_reconcile_*`, `agentgateway_xds_*` |
| Traces (per-turn) | OTLP (`frontend.tracing` → collector) | HTTP span attrs |
| **Spend in `$`** | **DEFERRED (M6)** | no `$` for now; switch on later as tokens × price card |
| Reserved/settled budget | **DEFERRED (M6)** | no ledger/reservation built for now |
| Live pod state | Redis `route` + K8s | agent-gateway §9.1 |

**Implication for the build plan (M6):** the Control Panel ships a **Prometheus client** + a **pod-self-reported token-usage** sink; the **price-card → cost** computation and any budget view are **deferred** (re-addable). It never expects agentgateway to report money, remaining budget, or BYOK LLM tokens.

---

## 10. Code deltas (current → Control Panel)

| File:line (current) | Change |
|---|---|
| `routers/admin.py:45-46` (prefix `/api/admin`) | **LIFT** as the admin API base; re-prefix `/admin/v2`; user store → data-spine RPCs. |
| `routers/admin.py:52,58,88,157` (`/users` CRUD) | RESHAPE onto Accounts/Identities (data-spine §1.7); **add** `identity_link` admin + access-mode + **BYOK-key management** (M6 — store/rotate the user's own key, §7.2). |
| `routers/admin.py:193` (`/stats`) | REPLACE with `/admin/v2/fleet` (Prometheus + K8s + data-spine join, pod-aware — §7.1). |
| `routers/admin.py:242` (`/audit`) | RESHAPE onto per-user audit JSONL (C1); keep filters (§7.5). |
| `routers/admin.py:293,308` (`/scheduler/*`) | RE-POINT at the central scheduler (scheduler drill). |
| `routers/admin.py:326-475` (per-user skills/mcp/hooks) | RESHAPE onto data-plane per-account reads (no local FS). |
| `routers/admin.py:480-711` (preset/clipath/risky-tools/sensitive-patterns/system-plugin) | **LIFT** as Settings (§6.1); store in data-plane config. |
| `admin_files.py` (227 lines) | RESHAPE → cross-user reads via **state-reader** glob (§7.5); delete local-FS paths. |
| `main.py:75,79` (`StaticFiles` single SPA) | **SPLIT** → serve `/ui` (user SPA) + `/admin` (admin SPA) build targets (§5.1). |
| `main.py:181` (CORS `["*"]`) | **DELETE** (moves to agentgateway `AgentgatewayPolicy.frontend`/`cors`, §1.2). |
| `main.py:203-219` (router fan-in) | **SPLIT**: runtime routers → reached via `/v1`→brain→pod (not this app); admin/config → admin API; scheduler/channels → their components. |
| **`controlpanel/extproc.py` (NEW)** | the gRPC `ext_proc` server (`:9000`): the brain pipeline (§2.1), wake-and-hold (§2.2), EPP steer returning `x-gateway-destination-endpoint` (§2.3), signed-claim header inject. |
| **`controlpanel/admin_api/` (NEW)** | `/admin/v2/*`: fleet, pods(terminate/wake), budgets, policies, sessions, audit, users/link (§7); audit-before-return on every mutation. |
| **`controlpanel/agentgw/` (NEW)** | K8s client that reads/writes `AgentgatewayPolicy`/`Backend`/`Parameters` + Gateway-API resources (§7.6); a Prometheus client + price-card cost computation (§9). |
| **`priva/web` admin build (NEW target)** | promote `src/components/admin/*` to the `/admin` SPA; add Fleet/Budgets/Policies (§6); user SPA `/ui` served as-is (API base → edge, `VITE_API_TARGET`). |
| **agentgateway CRDs (NEW, not app code)** | `Gateway` + `HTTPRoute`(ui/admin/runtime) + `AgentgatewayPolicy`(edge-auth/admin-gate/runtime-extproc+ratelimit) + `AgentgatewayBackend`(InferencePool/llm/mcp) + `AgentgatewayParameters` (§1.2/§1.3); Helm OCI install. |
| **Control Panel K8s manifests (NEW)** | Deployment (N replicas; ports 9000/8080/8081); Service; narrow RBAC (read pods/AgentTenant; **patch** `AgentTenant spec.wake` only — never scale/delete; write `Agentgateway*` CRDs); NetworkPolicy (ingress: agentgateway only; egress: data-plane/Redis/K8s-API/Prometheus). |

---

## 11. Resolved risks (adversarial pass)

| # | Risk | Severity | Resolution |
|---|---|---|---|
| C1 | `ext_proc` is doc-flagged **"very experimental"**; the brain rides on it for *every* turn | blocker | Accepted with eyes open (full-edge-adoption was chosen knowing this). Mitigation: the brain's logic is transport-agnostic (same code that ran inline in agent-gateway), so a future drop-back to "thin app terminates `/v1` directly" (option-b-style) is a deployment change, not a rewrite. Pin the agentgateway version; track the ext_proc API. |
| C2 | **Scale-to-zero wake not native** → 503 on a sleeping pod | blocker | Brain holds the `ext_proc` stream, CR-patch wakes, buffers the turn, returns on readiness; predictive-wake keeps the warm path the norm; overrun degrades to buffered "retry," never a lost turn (§2.2). |
| C3 | Wake hold exceeds the `ext_proc` message timeout (cold start runs long) | major | Generous ext_proc timeout + leave `/v1 request_timeout` unset (no first-byte abort); predictive-wake (§2.4) removes wake from the critical path; bounded hold → short-circuit fallback (§2.2/§3.2). |
| C4 | **Option (a): admin surface on every public hot-path replica** | major | agentgateway path-routes `/admin` and enforces `jwt.role=="admin"` **at the edge** before it reaches the app (§1.2); the app re-checks; the `ext_proc` port is internal (agentgateway→app only, NetworkPolicy). User chose (a) knowing the isolation trade-off (§ packaging discussion). |
| C5 | Derived identity claim not forwarded to the pod (agentgateway claim→header passthrough undocumented) | major | The brain sets the signed assertion **explicitly** as a request header in `ext_proc` (documented mutation path), not relying on agentgateway JWT passthrough (§2.1). |
| C6 | `AgentgatewayPolicy` edits are **eventually consistent** (xDS push lag) — admin sees stale enforcement | major | Policies screen shows applied-vs-desired status (CRD `status.ancestors`); confirm dialogs note "takes effect within ~seconds"; budget *authority* is the ledger (immediate), not the CRD backstop (§7.3/§8). |
| C7 | MCP **upstream credential injection** undocumented in agentgateway | major | Flagged open; `backend.auth`/`headerModifiers` candidate (unverified). MCP *scope+observe* adopted now; credential injection deferred to the metering/egress drill (§8). |
| C8 | OIDC login is standalone-only in agentgateway (not a K8s policy field) | minor | We own login + JWT minting regardless (brain, agent-gateway §2.4); agentgateway only *verifies* the JWT. No dependency on agentgateway login (§8). |
| C9 | agentgateway is a **new edge SPOF** + new operational surface (controller + proxy + xDS) | major | Multi-replica proxy via `AgentgatewayParameters` (HPA/PDB); any edge is a SPOF — mitigated by replicas. New surface is the cost of adoption; observability via `:9092`/`:15020` (§9). |
| C10 | "12+ providers" / semantic-cache assumed but absent | minor | Provider list is ~7 named + `openai-compatible`; **semantic caching is a commercial Solo.io feature, absent** — the dashboard/metering must not assume it (research §3). |
| C11 | agentgateway's own admin UI mistaken for our control panel | minor | Its `:15000/ui/` is **read-only in K8s mode** (inspection only); our Control Panel writes CRDs via the K8s API (§7.6/§9.4). Terminology guard + §1.3. |
| C12 | Channel-connector wrongly folded into this app (agentgateway can't hold outbound sockets) | minor | Kept a **separate Deployment** with the single-owner lease (§0.1, agent-gateway §5); it sits behind agentgateway as a backend. |

---

## 12. Resolved decisions (locked 2026-06-18)

1. **Full edge adoption (FE).** `agentgateway.dev` is the L7 edge for all surfaces; it owns TLS/JWT/routing/streaming/rate-limit/observability. It does **not** own business logic (no in-process plugins) — the brain is an `ext_proc` callout (§1, §2).
2. **Option (a) — one app.** The routing brain and the Control Panel are a single stateless Deployment, `N` replicas, three interfaces (ext_proc `:9000` + faces `:8080` + internal `:8081`). Scales horizontally (statelessness, unchanged from agent-gateway §1). Isolation trade-off accepted (C4). The channel-connector stays separate (§0.1).
3. **Brain as `ext_proc`; steer via EPP.** Per-request resolve/mint/wake/steer runs in the ext_proc callout; steering uses the `InferencePool`/EndpointPicker `x-gateway-destination-endpoint` mechanism (not `:authority` mutation, which agentgateway ignores for static backends) (§2).
4. **Wake stays ours, held in the callout.** agentgateway has no scale-to-zero wake; the brain CR-patches + holds + buffers + returns-on-ready; predictive-wake keeps it off the critical path (§2.2).
5. **Two faces, two build targets.** `/ui` = existing `priva/web` user SPA (served as-is, API base → edge); `/admin` = the admin SPA (promoted from `src/components/admin`, extended), edge-gated by `jwt.role=="admin"` (§5).
6. **Ops-overview admin layout (L).** Sidebar (Fleet·Users·Budgets·Sessions·Audit·Policies·Settings) + landing stat-row + Fleet table + live-activity; 480px detail drawer; full design-spec compliance; dangerous ops behind confirm-dialog + audit (§6).
7. **M6: BYOK + metering deferred.** No metering proxy / virtual key / spend ledger / `$` / reserve-before / `402`; users bring their **own LLM keys** (operator-injected); token usage is **pod-self-reported, display-only**. The agentgateway rate-limit→ledger seam is **deferred** (re-addable). MCP *scope+observe* stays agentgateway's; MCP upstream creds are **operator-injected**. Login/JWT-mint stays ours (§8). *(Supersedes the original decision 7.)*
8. **Dashboard reconstructs state from Prometheus + OTLP + ledger + data-spine.** agentgateway has no config/budget read API and emits no `$`; the Control Panel computes cost from a price card and drives config via K8s CRDs (§7/§9).
9. **Pod terminate (and stop-turn) = Control Panel UI → Operator.** Confirm-dialog + type-to-arm + audit-before-return; Operator is the sole scaler; gateway/edge impact is none new (mid-stream-death handling) (§6.2/§7.4, agent-gateway §9.4).

> **Owed next (revised):** the **`agent-gateway.md` reconciliation pass** (§0.3) is **done** (2026-06-18). The **Operator** drill is **done** (`operator.md`). The **central scheduler** drill is **done** (`scheduler.md` — the admin `/scheduler/*` re-point in §7.2/§10 lands there; `PushToChannel` resolved). The **metering proxy is DROPPED** (blueprint **M6** — BYOK + token-count-only). The `data-spine §2.7` idle-default fix is **done** (180→1800). **All components are now drilled.** The **deep M6 body cleanup is DONE (2026-06-18)** — agent-pod, data-spine, agent-gateway, and blueprint §3/§4 are rewritten M6-correct (the blueprint §2 decisions table / system diagram / §5-7 remain under the supersession banner, as with M1/M2/M5). Remaining (not a drill): only the **channel-connector** sub-pass (agent-gateway §4.4).
