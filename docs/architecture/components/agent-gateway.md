---
Status: Draft (reconciled with control-panel.md / full edge adoption, 2026-06-18) · Date: 2026-06-18 · Branch: multi-tenant-platform
Parent: ../multi-tenant-platform.md · Component: the Routing Brain — the `ext_proc` face of the Control Panel (formerly "Agent Gateway, stateless front door"); `N` stateless replicas behind agentgateway
Consumes: ./data-spine.md (identity/binding/session RPCs + the two-tier Redis catalog) and ./agent-pod.md (the pod's run/stream/permission/fork/rewind API seams) as binding contracts
Superseded-on-edge-by: ./control-panel.md (full edge adoption + option (a)) — agentgateway terminates the client transport / verifies JWT / owns TLS·CORS·rate-limit; this document remains authoritative for the brain's *internal* mechanics
---

# Priva Agent Gateway — Component Specification

> **Reconciliation banner (full edge adoption, locked 2026-06-18 — read first).** This document was written for a **hand-rolled, client-terminating front door.** The platform has since locked **full edge adoption** of **agentgateway.dev** + **option (a)** (one app = routing brain + Control Panel) — see `./control-panel.md`. Under that decision this tier is **no longer the thing clients connect to**; it is the **routing brain** — the **`ext_proc` callout** that the external **agentgateway** consults per request. Read this document with three global substitutions:
>
> 1. **"the gateway / the Agent Gateway" → "the brain"** (our per-request routing logic), *except* where the surrounding text clearly means the external edge. The brain is `N` stateless replicas, now packaged **inside the Control Panel app** (control-panel.md option (a)), not a standalone tier.
> 2. **The client transport (TLS, WS/SSE, HTTP/2), JWT *signature* verification, CORS, and coarse rate-limit are terminated/enforced by agentgateway at the edge** (control-panel.md §1, §3, §8) — *not* by this tier. This tier no longer "terminates the client transport"; it makes the `ext_proc` decision (resolve · mint · wake · **steer**) and steps off the byte path (control-panel.md §2).
> 3. **The channel-socket lease (§5) lives in the separate `channel-connector` Deployment** (agentgateway is inbound-only and cannot hold the outbound WeCom socket — control-panel.md §0.1). IM inbound therefore *bypasses* agentgateway and drives the brain from the connector (§4.4).
>
> **What is unchanged and remains authoritative here:** the brain's *internal* mechanics — identity resolution + `/link` (§2), `session_uuid` minting + `is_first_run` under M5 (§3), the pre-gate → buffer → ack → wake (§6), the cross-replica permission relay + no-impersonation gate (§7), the outbound fan-out *logic* (§8), and the lease *protocol* (§5). control-panel.md carries these "by reference" rather than duplicating them. **Where the two conflict: control-panel.md wins on the edge/packaging shape; this document wins on the brain's internal mechanics.**

**Scope:** the **routing brain** (formerly drilled as "the Agent Gateway") is the platform's **per-request routing logic** — `N` stateless replicas — that resolves every inbound turn. Under full edge adoption (banner above) it is **no longer the tier clients connect to** (that is **agentgateway**, the external L7 edge — control-panel.md §1); it is the **`ext_proc` callout agentgateway consults per request**, packaged inside the Control Panel app (control-panel.md option (a)). It is built by **lifting the channel daemon (`services/channels/*`) and the front-door slice of `priva/api` out of the single-machine process and making them stateless + multi-replica.** Its whole reason to exist is to **dissolve today's documented split-brain** (a standalone `python -m api.services.channels.daemon` process and the uvicorn API process, each with its *own* `PermissionCoordinator`, its *own* session map, and a *file-based* command queue between them) into **one routing tier whose only durable state lives in Redis + the data-plane.** The gateway **holds no agent state** — no `asyncio.Future`, no JSONL, no `PermissionCoordinator`, no `/export` mount (agent-pod §9.4, data-spine §3.8). It resolves identity (surface → `account_id`), targets a session (binding → `session_uuid`), **mints** the `session_uuid` (the client never invents one — agent-pod §13-7), pre-gates + buffers + acks + wakes a sleeping pod, fans streamed output back out per surface, and **relays** permission decisions to the exact pod holding the live Future. Every load-bearing claim is cited `file:line` against the installed code and verified; where today's code has no equivalent (channel→account mapping, cross-replica relay), the gap is named, not assumed.

> **Terminology guard — THREE different "gateways" (aligned with control-panel.md §0).** (1) **agentgateway** (agentgateway.dev) — the **external** Rust L7 proxy + Kubernetes infrastructure; the edge pipe clients connect to; **not our code**. (2) **the brain** — *this document's* subject: our per-request routing logic, now the **`ext_proc` face** of the Control Panel (formerly "the Agent Gateway, inbound front door"). (3) **the egress / token-count path** (agent-pod §9.6, §13-2) — the *outbound* path. **Under M6 this is deferred**: outbound goes **direct** with the user's **own BYOK key** (no allow-list, no credential injection at egress, no metering proxy); token usage is **pod-self-reported** for observability. The brain injects **identity** (a signed `account_id`) into pod calls; it never injects or holds LLM provider keys (the BYOK key is an operator-injected secret — agent-pod §7 SB2). Keep all three distinct.

This document is the executable contract for the gateway: §0 what is lifted / stripped / newly-built from `priva/api`, §1 topology & the "holds no state" invariant, §2 identity resolution & the `/link` flow, §3 session targeting & `session_uuid` minting (the `is_first_run` CAS), §4 the run/stream lifecycle (gateway↔pod), §5 channel-socket ownership (the split-brain killer), §6 pre-gate → buffer → ack → wake, §7 the cross-replica permission relay & the no-impersonation gate, §8 outbound fan-out & multi-surface attach, §9 pod routing & connection management, §10 auth verification & abuse controls per surface, §11 the consolidated code-delta table, §12 the resolved-risk register, §13 the resolved decisions (Q1–Q7 + M5 + predictive-wake + admin-termination, locked 2026-06-18).

> **Schema note (M5, locked 2026-06-18).** There is **no central session table** — the JSONL on the per-user volume is the session of record (decision 15). The gateway mints `session_uuid` **locally for WebUI** and via a **`channel_binding`** write **for IM**, and `is_first_run` is a binding CAS (IM) / gateway-mint + disk-existence guard (WebUI) — not a `session_index.status` CAS. M5 supersedes the data-spine + agent-pod session-table model where they conflict (those carry M5 revision notes pending a body rewrite). See §3 and §13.

---

## 0. What is lifted / stripped / newly-built from `priva/api`

The pod spec (agent-pod §0) **strips** channels, auth, static, and scheduler from the pod. This is the other half: most of what the pod strips is **lifted into the gateway** — but reshaped from single-process-with-files into stateless-with-Redis.

| Subsystem (current code) | Disposition | Where it goes / what changes |
|---|---|---|
| Channel daemon process (`services/channels/daemon.py`, started `python -m api.services.channels.daemon`, `:1624-1630`; `ChannelDaemon` `:292`; 100ms command-poll loop `:306-353`) | **LIFT + RESHAPE** | Becomes the **channel-connector's** socket workers (a **separate Deployment** under full edge adoption — control-panel.md §0.1; agentgateway is inbound-only and cannot hold the outbound WeCom socket). The **standalone process and its poll loop are deleted**; socket ownership becomes a Redis lease (§5). No second process, no split-brain. |
| File-based command queue (`shared.py:38-60` `write_command`; commands dir poll `daemon.py:1360-1410`: `connect`/`disconnect`/`update_config`/`shutdown`) | **DELETE** | Replaced by the Redis lease (§5) + CR-patch wake (§6) + live config reads from the data-plane. No file IPC anywhere. |
| Heartbeat / state files (`~/.channels/heartbeat`, `state.json`; `daemon.py:1539-1570`) | **DELETE** | Replaced by Redis `route`/`awake`/`channelconn:lease` (data-spine §4) + channel-health conditions surfaced to the control panel. |
| WeCom `WSClient` + SSL/CONNECT-proxy monkey-patches (`daemon.py:65-141`); frame normalizer (`:146-196`) | **LIFT** | The lease-owning replica holds the WeCom socket and runs the normalizer → `InboundMessage`. The patches move verbatim into the channel worker. |
| Per-chat session map (`conn.sessions` `daemon.py:285`; `.priva.wecom.sessions.json` load/save/cleanup `:1312-1356`; key = `chat_id or sender_id` `:664`) | **REPLACE** | Central `channel_binding` (one identity → one session, `ux_binding_identity`) + `wecom_session` table (data-spine §2.4/§2.12). Resolution is a data-plane RPC + Redis cache, never a per-process JSON file. |
| OpenClaw bridges dict + auto-connect-all-users loop (`openclaw_bridge.py`; `main.py:133-153`) | **LIFT + RESHAPE** | Bridge ownership becomes a Redis lease like WeCom; the **auto-connect-over-`list_users()` loop is deleted** (a cross-tenant boot violation) — the gateway connects one socket per *enabled* channel config read from the data-plane. |
| Both in-pod `PermissionCoordinator`s — the daemon's `conn.pending` (`daemon.py:288`) **and** the API's module-global `registry` (`permission_coordinator.py:133`) | **DELETE from the gateway** | The `asyncio.Future` rendezvous is **in-pod only** (decision 6; agent-pod §6.1). The gateway carries **no coordinator and no Future** — it only relays a decision via Redis `approval:index` + `in:reply` (§7). |
| `/permission/respond` `registry.get(session_id)` → 404 / `owner_username` 403 (`agent.py:479-500`, `:485,488-490`) | **RESHAPE** | Gateway ingress: `HGETALL approval:index:{request_id}` → `{session_uuid, account_id, pod}` → relay to the owning pod (§7). The 403 becomes the **hard no-impersonation gate** (§7.4, agent-pod §13-6). |
| Browser SSE termination (`routers/agent.py:346-369` `/run/stream`; `StreamingResponse media_type=text/event-stream` `:368`; `service.py:977` `agent_run_stream`; SSE framing `:433`) and the WS path (`agent.py:734-747`, `coordinator_out`) | **RESHAPE** | **agentgateway** terminates the client transport (control-panel.md §3); the **brain** only makes the `ext_proc` decision (resolve · mint · wake · steer) and steps off the byte path. The **serializer (`serialize_message`/`serialize_result_message`, agent-pod §0) stays in-pod** — pure functions producing the wire format; **agentgateway** streams the bytes through (browser) and the **channel-connector** re-frames for IM. Neither re-serializes. |
| Auth (`auth.py:47-95` `authenticate_raw_token`: JWT/api-key/global/anon; `get_current_user` `:98-115`; `routers/auth.py`, `routers/admin*.py`) | **LIFT + RESHAPE** | JWT **signature** verification moves to **agentgateway** at the edge (`jwtAuthentication` policy — control-panel.md §1.2); **identity resolution** (surface→`account_id`) stays in the **brain**, which reads the already-verified claims. The **user store becomes data-plane Accounts/Identities RPCs** (data-spine §1.7). The brain **mints the signed `account_id`** the pod trusts (§2.4) and injects it via `ext_proc` (control-panel.md §2.1). Admin routers → the Control Panel app (control-panel.md §0.2). |
| `wecom_access_allowed` per-channel-user gate (`daemon.py:199-229`); access modes `all`/`private`/`allowed_user_ids` | **KEEP logic, RESHAPE** | Runs in the gateway, backed by central `channel_config_wecom` + `identity_link` (data-spine §2.12/§2.2), not a per-process config dict. |
| Reply paths — `reply_stream` (short/<4000 chars/<5s, `daemon.py:844-846`), proactive `send_message` (chunked 3500, `:1041-1054`), permission/question cards (`wecom_feedback.py:274-324`), `asker_id` answer gate (`daemon.py:604-630`) | **LIFT** | Become the gateway's outbound fan-out + chunker + card renderer (§8). The pod makes **no** IM calls (agent-pod §9.6: IM egress denied). |
| Static SPA + `/static` + docs mounts (`main.py:74-79,187,221`) | **MOVE** | Served by the **Control Panel app's faces** (`/ui` user SPA + `/admin` admin SPA — control-panel.md §5), **path-routed by agentgateway** (`HTTPRoute`). Two build targets share the design-spec lib. The pod serves zero static. |
| Shared `.priva.user.yml` + fcntl config store (`channels/config_store.py`) | **DELETE** | Channel config is data-plane `channel_config_wecom`/`_openclaw` (data-spine §2.12); the gateway reads it via RPC, never an fcntl-locked YAML. |
| APScheduler + scheduler router (`services/scheduler/*`, `routers/scheduler.py`) | **NOT the gateway** | → central scheduler. But the scheduler **originates proactive IM through the gateway** (an internal push seam, §8.4, §13-7) — never touching an IM endpoint directly. |
| `CORSMiddleware allow_origins=["*"]` (`main.py:180`) | **MOVE to the edge** | CORS/TLS/origin policy moves to the **edge** as an **agentgateway** `AgentgatewayPolicy.frontend`/`cors` (control-panel.md §1.2) — *not* app middleware. (The pod's CORS narrows to the mesh origin — agent-pod §1.2.) |

---

## 1. Topology & the "holds no state" invariant

### 1.1 What the gateway *is*

`N` identical, stateless replicas. **The LB + client-transport-termination role belongs to agentgateway now** (control-panel.md §1); a brain replica is reached by agentgateway over `ext_proc` (browser) or by the channel-connector (IM, §4.4). Any replica can resolve any turn, drive any wake, accept any relayed permission reply — because **nothing durable lives in a replica's memory.** The single rule everything else stands on: **the gateway holds no agent state.** Concretely it holds **no `asyncio.Future`** (the permission rendezvous is in-pod — decision 6), **no JSONL / no `/export` mount** (only the `state-reader` mounts the volume — data-spine §3.8/§3.9), **no `PermissionCoordinator`**, and **no per-session lock** (the in-pod `asyncio.Lock` is authoritative — agent-pod §5.3). The only state a replica owns transiently is (a) the **client-facing transport socket** it terminates (a browser WS/SSE or an inbound IM frame in flight) and (b) any **channel sockets it currently leases** (§5) — and both are reconstructible: kill a replica and the client reconnects + another replica re-leases the channel.

```
 EXTERNAL SURFACES
 ┌─────────┬─────────┬─────────┬──────────┬───────────┐
 │ Browser │ WeCom   │ Feishu  │ Discord  │ OpenClaw  │   ← v1 = WeCom ONLY (locked §13-2);
 │ JWT/WS  │ ws sock │ evt-sub │ gw sock  │ Ed25519   │     OpenClaw coded-but-deferred; Feishu/Discord blueprint-only
 └────┬────┴────┬────┴─────────┴──────────┴─────┬─────┘
      │ HTTP/WS/SSE                       socket │  (IM bypasses agentgateway — it is inbound-only)
      ▼                                          ▼
 ┌────────────────────────────┐    ┌──────────────────────────────┐
 │  agentgateway  (THE EDGE)   │    │  channel-connector            │
 │  external L7 proxy; TLS ·   │    │  (SEPARATE Deployment, §5)    │
 │  JWT-verify · route · stream│    │  socket LEASE · sender-valid  │
 │  · CORS · coarse rate-limit │    │  · outbound FAN-OUT (§8)      │
 │  (control-panel.md §1/§3/§8)│    └───────────────┬──────────────┘
 └─────────────┬──────────────┘                    │ invoke brain (resolve/wake/steer)
       ext_proc │ (per request)                     │ + relay pod stream, fan out
                ▼                                    ▼
 ╔══════════════════════════════════════════════════════════╗
 ║   ROUTING BRAIN — the ext_proc face of the Control Panel   ║
 ║   (N stateless replicas; control-panel.md option a)        ║
 ║  identity resolve · session target+MINT · pre-gate ·      ║
 ║  buffer+ack+wake · permission RELAY · STEER (EPP, §2.3-cp) ║
 ║  ── HOLDS NO Future · NO JSONL · NO coordinator · NO mount ║
 ╚═╦══════════╦═══════════╦═══════════╦══════════╦═══════════╝
   ║ gRPC/mTLS║ Redis T1  ║ Redis T2  ║ K8s API  ║ pod (steer target / relay; mTLS + signed id)
   ▼          ▼           ▼           ▼          ▼          (browser bytes: agentgateway⇄pod, NOT via brain)
 data-plane  inbox·       route·      CR patch   per-tenant Agent Pod
 (identity/  approval·    awake·      spec.wake  (run/stream/fork/rewind/
  binding/   spend·       lease·      .requested  permission-respond)
  session)   in:reply     lock-mirror  At
```

### 1.2 Why every replica is interchangeable (the state externalization map)

| State that *was* in-process (single-machine) | Now lives in | Consequence for the gateway |
|---|---|---|
| Daemon `conn.sessions` per-chat map (`daemon.py:285`) | `channel_binding` + `wecom_session` (data-plane) + `binding:cache:{session_uuid}` (Redis T1 #6) | Any replica resolves a channel → session with one cache read. |
| API `registry` session→coordinator (`permission_coordinator.py:133`) | **in-pod** `RunContext.coordinators` (agent-pod §5.2) | The gateway never holds a coordinator; it relays to the pod (§7). |
| Daemon `conn.pending` permission state (`daemon.py:288`) | **in-pod** Future + `approval:index`/`pin:approval` (Redis T1 #3/#4) | A reply on *any* replica resolves via the index (§7.3). |
| Channel connection ownership (the daemon *was* the sole owner) | `channelconn:lease:{channel}:{bot_id}` (Redis T2 #13) | Exactly one replica owns a socket; failover re-leases (§5). |
| The "on it…" promise (daemon replied inline) | `inbox:{account_id}` (Redis T1 #1, durable) | Ack fires only after the durable RPUSH; survives replica death (§6). |
| File command queue (`shared.py` + commands dir) | Redis lease + CR patch + data-plane config | No file IPC; no second process to coordinate with. |

A replica is therefore a pure function of `(Redis, data-plane, K8s API, the pod)` plus whatever sockets it momentarily holds. This is exactly the property that kills the split-brain: there is **one** tier and **one** copy of each piece of coordination state, in a store every replica shares.

### 1.3 Load balancing & stickiness

- **Browser transport:** terminated by **agentgateway** (TLS/WS/SSE); any session affinity is agentgateway's concern. The **brain's `ext_proc` call is per-request and stateless** — it needs no stickiness (it resolves everything from Redis/data-plane on each call).
- **IM (socket surfaces):** the **channel-connector** owns the inbound WeCom `WSClient`/OpenClaw bridge under the lease (§5); inbound IM does **not** traverse agentgateway (§4.4). Validated frames drive the brain.
- **Permission replies:** may land on any replica (browser reply via agentgateway, IM reply via the connector); resolved via `approval:index` + (if the owning replica is elsewhere) the `in:reply:{request_id}` relay, under a **<50ms one-hop budget** (blueprint residual default).

---

## 2. Identity resolution (surface → `account_id`) & the `/link` flow

### 2.1 The gap this closes

The survey confirmed the single biggest current-code gap: **there is no mapping from a channel user to a backend account.** Today one backend user *owns* a bot, and `wecom_access_allowed` (`daemon.py:199-229`) gates *which channel users may talk to that one owner's bot* via `all`/`private`/`allowed_user_ids`. There is no notion of "WeCom userid X *is* Priva account Y." The blueprint's `identity_link` + `channel_binding` + `/link` flow (decisions 8/17) is what fills this, and **the gateway is where it is resolved and enforced.**

### 2.2 The resolution pipeline (inbound)

Every inbound turn — from any surface — resolves to `(account_id, session_uuid, is_first_run)` before anything else happens:

```
inbound (surface_type, surface_user_id, channel_id?, bot_id?, payload)
   │
   ├─[2.3] verify surface authenticity (signature / token / mTLS — §10)
   │
   ├─ ResolveIdentity(surface_type, surface_user_id) ──▶ account_id      (data-spine §1.7; Redis-cached)
   │     └─ miss / unverified identity ──▶ /link challenge (§2.5), NOT a wake
   │
   ├─ account status pre-gate (§6.1): active? under hard cap? ──▶ else reject, NO wake
   │
   ├─ session target:
   │     • IM:      validate sender (access mode) → GetBinding(identity_id) ──▶ fixed session_uuid  (binding:cache T1 #6; no chat_id key)
   │     • Browser: body carries session_uuid (resume) OR empty (new ⇒ gateway mints, §3)
   │
   └─ is_first_run := first_run_done CAS on channel_binding (IM) | mint-on-empty + disk-guard (WebUI)   (§3.3, M5)
         ──▶ forward to pod (§4) with signed {account_id, session_uuid, is_first_run}
```

`ResolveIdentity(surface_type, surface_user_id) → account_id` is a data-plane RPC (data-spine §1.7), cached in Redis (DB on miss). It reads `identity_link` (`ux_identity_surface` UNIQUE on `(surface_type, surface_user_id)`, data-spine §2.2) — **one `(surface, user)` maps to exactly one identity → one account.**

### 2.3 Per-surface identity sources

| Surface | Identity source | Verified-in-code? | Resolution |
|---|---|---|---|
| **Browser** | JWT `sub` (today `auth.py:47-95` decode_jwt → `store.get_user(payload.sub)`); per-user/global API key fallback | Yes (`auth.py`) | `sub` → `account_id` directly (the account is the JWT subject); no `identity_link` hop needed for the browser surface (it *is* the account login). |
| **WeCom** | `sender_id` (+ `chat_id` for groups) from the normalized frame (`daemon.py:154-196`) | Yes (`daemon.py`) | `ResolveIdentity('wecom', sender_id) → account_id`; unlinked ⇒ `/link`. |
| **OpenClaw** | Ed25519-identified bridge peer (`openclaw_bridge.py`) | Yes | `ResolveIdentity('openclaw', peer_id)`. |
| **Feishu** | event-sub `open_id`/`union_id` | **No** — blueprint-named, not in code (§13-2) | Same pattern once built. |
| **Discord** | interaction `user.id` | **No** — blueprint-named (§13-2) | Same pattern once built. |

### 2.4 The signed `account_id` the pod trusts

The pod **strips all auth** (agent-pod §0: "the gateway injects a signed `account_id`") and **blindly trusts** the identity asserted on each call (agent-pod §10 `/permission/respond` "gateway-asserted actor"). The gateway is therefore the **sole trust boundary** that converts a surface credential into an internal identity. On every pod call it injects a signed, short-TTL assertion of `{account_id, session_uuid, is_first_run, actor_account_id}` — **locked (§13-1): service-mesh mTLS + a short-TTL signed JWT** the pod verifies via JWKS (plain trusted-header and shared-HMAC were rejected). The pod's NetworkPolicy already admits ingress **only** from the gateway (agent-pod §9.6), so the signed claim + the network edge together prevent a compromised tenant pod from forging another tenant's identity.

> *(Reconciliation: the brain injects this assertion as an **`ext_proc`-set request header** on the browser path — control-panel.md §2.1 — and on the **connector→pod** call for IM. It is distinct from the **platform JWT** the client presents and **agentgateway** verifies at the edge: that JWT authenticates the **human to the platform**; this signed `account_id` authenticates the **brain to the pod**. Two tokens, two trust edges — see §10.3.)*

### 2.5 The `/link` flow (channel identity → account)

A channel user with no verified `identity_link` cannot run a turn (it would have no account, no budget, no session). The gateway issues a one-time link challenge (blueprint residual default, now located here):

```
unlinked inbound (surface_type, surface_user_id)
  → gateway replies on the channel: "reply /link <CODE> from your Priva account to connect"
  → user, logged into the browser SPA (already an account via JWT), enters CODE
  → gateway: validate code → INSERT identity_link(account_id, surface_type, surface_user_id, verified=1)
            (Link RPC, data-spine §1.7) → invalidate the ResolveIdentity cache
  → subsequent inbound from that surface_user_id resolves to account_id
```

- **Code:** 10-char base32 (50-bit), **300s TTL, single-use, 5/min/channel lockout, account-name echo** (blueprint residual default). Stored in Redis with TTL; consumed atomically.
- **Direction:** the code is minted **in the authenticated browser session** (which already knows `account_id`) and **redeemed from the channel** (which proves control of `surface_user_id`) — binding the two. This is the only safe direction (a code minted on the channel side could be social-engineered).
- **Cardinality:** `ux_identity_surface` enforces one `(surface, user)` → one account. Re-linking to a different account is an explicit `Unlink` + `Link` (audited), never silent.

### 2.6 Group handling — validate the sender, route by identity (locked §13-5, refined 2026-06-18)

A WeCom **group** chat lets any member @-trigger the bot. **There is no `chat_id`-keyed shared session** (no-chat_id-key): for **any group** message the only group-specific step is **validating the sender** against the access modes — **ALL / whitelist / private** (`wecom_access_allowed`, `daemon.py:199-229`, §10.2). A validated message then routes via the **sender's `identity_link`** → the sender's `channel_binding` → the sender's **own session, under their own account/budget**; `chat_id` is used only to address the reply (from the inbound frame), never as a resolution key. The sender is recorded for audit + the `asker_id` answer gate (§7.4). An ALL-mode bot still requires the sender to be a `/link`-ed identity to have a session (an unlinked sender gets the `/link` prompt, §2.5). *(This refines the earlier "shared group session under the bot-owner" framing, which assumed `chat_id` keying — withdrawn; `wecom_session` is dropped, data-spine §2.12.)*

---

## 3. Session targeting & `session_uuid` minting (the `is_first_run` CAS)

### 3.1 The locked contract (agent-pod §13-7)

**The gateway mints the canonical `session_uuid`; the client never invents one.** A client-chosen id could violate PK-uniqueness / the no-remap invariant (M2). New-vs-resume is decided *at the surface*; minting happens *at the gateway*.

| Surface | New session | Resume |
|---|---|---|
| **Browser/WebUI** | request body carries an **empty** `session_id` → gateway mints a lowercase UUIDv4 **locally** → returns it on `stream_init`; the SPA reuses it every later turn | body carries a populated `session_id` → RESUME (with the §3.3 disk-existence guard) |
| **IM channel** | bound to a **fixed** `session_uuid` in `channel_binding`; a user **reset** (`/reset`, today `daemon.py:632-643`) is the only way to mint a new one (`RebindChannel` → fresh uuid, `first_run_done=0`) | every normal message RESUMEs the fixed bound session |

### 3.2 Mint point (M5 — no session table)

Under **M5** the central DB has **no `session_index`** — the JSONL *is* the session (decision 15). So minting is minimal:

- **WebUI:** the gateway mints `str(uuid4()).lower()` **locally** — no data-plane round-trip, no row to commit. The uuid is valid the instant it's minted (every Redis key is rooted on it + `account_id`); if the pod dies before the first JSONL write, the session simply doesn't exist yet and the client retries as a fresh create.
- **IM:** the **`channel_binding`** row is the durable record — `BindChannel` writes a fresh `session_uuid` + `first_run_done=0` (the *only* place a mint touches the DB, because the binding must persist to route the channel).

No `cwd_hash`/`config_home` is persisted (M5): the **in-pod SDK** derives the JSONL path (one pod = one account, so `os.environ["CLAUDE_CONFIG_DIR"]` is correct — agent-pod §1.3), and the reader plane locates a file by globbing `projects/*/<uuid>.jsonl`.

### 3.3 `is_first_run` without a session table (M5)

`is_first_run` still picks the CLI shape (CREATE `--session-id` vs RESUME `--resume`, agent-pod §2.3) and is still **server-authoritative** — but its source moves off the (now-absent) `session_index.status` onto the binding (IM) and the gateway-mint/disk (WebUI):

```
IM   : first-run = CAS on the binding row
         UPDATE channel_binding SET first_run_done=1
           WHERE binding_id=? AND first_run_done=0
         rows-affected==1 ⇒ is_first_run=True (CREATE)   ;   ==0 ⇒ RESUME
       (single-writer data-plane ⇒ race-free; this IS the old "ClaimFirstRun," relocated)

WebUI: empty session_id      ⇒ gateway just minted it ⇒ is_first_run=True (CREATE)
       populated session_id   ⇒ RESUME, guarded by an NFS-safe stat of the derived
                                JSONL path; absent ⇒ reconcile-as-create (agent-pod §2.5)
```

The one residual edge — a sub-second WebUI double-send where the first turn's JSONL isn't yet visible under NFS attribute-cache lag — is the **already-flagged Phase-0 item** (data-spine §7-Q3), backstopped by agent-pod §2.5 reconcile-as-create. IM has no such edge: the binding CAS is strongly consistent. **No `ClaimFirstRun` RPC and no `session_index` are needed** (this withdraws the earlier §13-4 seam).

### 3.4 Ephemeral / subagent runs

Per agent-pod §5.7 invariant 7, **every** CREATE — including ephemeral and subagent runs — must carry a platform-minted `session_uuid` (never `None`). When the gateway dispatches a run that will spawn subagents, the *pod* mints child session ids only via the sanctioned fork path (agent-pod §3); for a top-level ephemeral run the **gateway** mints. The gateway never forwards a `None` session id.

---

## 4. The run/stream lifecycle (gateway ↔ pod)

### 4.1 The inversion

Today a chat turn is fully in-process: `POST /api/agent/run/stream` (`agent.py:346`) → `agent_run_stream` (`service.py:977`) spawns the CLI and streams SSE back from the *same* process. Under full edge adoption, **agentgateway terminates the client transport** (control-panel.md §3); the **brain** makes the per-request `ext_proc` decision (resolve · mint · wake · **steer**) and then **steps off the byte path**; the **pod runs the turn**. The byte stream flows **client ⇄ agentgateway ⇄ pod** (browser) or **WeCom ⇄ channel-connector ⇄ pod** (IM, §4.4) — the brain relays *coordination* (wake, permission), not bytes.

```
client (browser WS/SSE via agentgateway | IM frame via channel-connector)
   │  turn text + (resume) session_uuid | (new) empty
   ▼
EDGE: agentgateway (browser) ── ext_proc ──▶  |  channel-connector (IM) ── internal call ──▶
   ▼                                                                                          ▼
BRAIN (ext_proc decision — once per turn)
   │  resolve identity+session+is_first_run (§2,§3) · pre-gate (§6.1)
   │  route:{account_id} (T2 #8) → pod awake?  ── asleep ─▶ buffer+ack+wake (§6) ─▶ on boot the POD drains inbox
   │                                            ── awake ─▶ STEER: return pod_ip (EPP, control-panel.md §2.3)
   ▼  (brain steps OFF the byte path here)
AGENT POD  (run/stream — agent-pod §4; serialize_message in-pod)
   │  emits: stream_init · assistant · permission_request · result   (the wire format, agent-pod §0)
   ▼
DELIVERY: agentgateway streams bytes ⇄ client (browser)  |  channel-connector relays + fans out (IM, §8)
   │  permission_request ─▶ render card / inline approval to the bound surface (§7), carry request_id
   │  result            ─▶ final delivery; release the client transport (held by agentgateway/connector)
```

### 4.2 What crosses the gateway↔pod edge

- **Downstream (→pod):** the turn text, the signed `{account_id, session_uuid, is_first_run, actor_account_id}` assertion (**brain-injected via `ext_proc` header** / on the connector→pod call — §2.4), `permission_mode`, model override, and (for fork/rewind) the typed request. These map onto the pod's existing `AgentRunRequest`/`WsInitFrame` (`models/agent.py:24,209`) plus the new `is_first_run` field (agent-pod §2.2).
- **Upstream (pod→):** the pod's event stream — `stream_init`/`assistant`/`permission_request`/`result` — already serialized by the in-pod `serialize_message`/`serialize_result_message` (agent-pod §0, kept verbatim). **agentgateway streams the bytes through (browser) / the channel-connector re-frames (IM); neither re-serializes** (no duplicate serialization logic; the wire format is owned by the pod).
- **Cap/drain backpressure:** the pod returns **429** over its concurrency cap (`X-Cap`/`X-Inflight`, agent-pod §5.5) and **503** while draining (agent-pod §8.4). On 503 the gateway **re-buffers to the inbox** (the turn is not lost); on 429 it surfaces "at capacity, retry" to the client (or queues per surface policy).

### 4.3 Transport choice (browser)

Today both exist: SSE `/run/stream` (`agent.py:346`) and a WS path (`agent.py:734-747`, which today bypasses the registry via `coordinator_out`). **Locked (§13-3): WS is the primary client transport, SSE the fallback.** WS is bidirectional — stream output, mid-stream user messages, *and* permission replies ride one socket, collapsing today's separate `/permission/respond` POST. *(Under full edge adoption it is **agentgateway** that terminates this WS — control-panel.md §3 — and routes it; the brain stays off the byte path.)*

### 4.4 The two surface paths under full edge adoption (reconciliation)

The split into *agentgateway-edge + brain + channel-connector* gives an inbound turn one of **two** paths to the brain. The brain's *logic* is identical on both; only **who terminates the inbound transport** and **who fans output back** differs:

```
BROWSER   client ─(TLS/WS)→ agentgateway ─(ext_proc)→ BRAIN (resolve·mint·wake·STEER)
                                   └─(connects to steered pod_ip)→ POD ⇄ agentgateway ⇄ client
                            (brain off the byte path; agentgateway streams — control-panel.md §2/§3)

IM(WeCom) WeCom ─(leased socket)→ channel-connector ─(internal RPC)→ BRAIN (resolve·mint·wake·steer)
                                   └─ connector opens pod transport, relays stream, FANS OUT (chunk/cards, §8)
                            (bypasses agentgateway — it is inbound-only; connector owns the socket, §5)
```

- **Browser** rides agentgateway's `ext_proc` (control-panel.md §2): the brain returns the steered endpoint (EPP, control-panel.md §2.3) and agentgateway streams bytes pod⇄client.
- **IM** never reaches agentgateway inbound (agentgateway cannot hold the outbound WeCom socket — control-panel.md §0.1); the **channel-connector** terminates the bot socket, validates the sender (§2.6/§10.2), invokes the brain's resolve/wake/steer, then relays the pod stream and runs the fan-out (§8).
- **Resolved (locked 2026-06-19) — A: internal RPC, not synthetic re-inject.** The connector calls the brain's **internal routing seam** (`:8081`, mTLS — control-panel.md §0.1): `RouteTurn(surface_type, surface_user_id, text) → {session_uuid, account_id, pod_endpoint}` (resolve·mint·wake·steer; a **warm** pod returns its `pod_endpoint`, a **cold** pod falls to buffer+ack+wake, §6). The connector then **dials the pod itself** (per-tenant Service DNS, mTLS + the brain-minted signed `account_id`, §9.1), relays the stream, and runs the fan-out (§8). agentgateway stays **out of the IM byte path** entirely. **Why A over re-injecting a synthetic `/v1` through agentgateway:** (i) owning the *outbound* WeCom socket is the whole reason the connector is a separate Deployment (agentgateway is inbound-only — control-panel.md §0.1); routing IM *back into* the browser edge would partially un-do that split and add two byte hops (pod→agentgateway→connector) the IM push gains nothing from. (ii) B's one real win — uniform agentgateway edge policy (rate-limit / a token cap) blanketing IM for free — **doesn't pay off under M6** (the edge `rateLimit`/token-cap is deferred, §10.2); the finer per-surface limits that *do* exist already live in the brain/connector, not the edge. (iii) B needs the connector to **forge a platform JWT** to pass agentgateway's `jwtAuthentication` (or a connector-bypass) — an impersonation surface A avoids, since the connector already verified the socket credential at its own edge (§10.1) and calls the brain over mTLS.
- **A's one cost, paid down explicitly:** two callers of the routing logic (`ext_proc` `:9000` for browser, internal RPC `:8081` for IM) must not drift. The brain therefore factors **resolve·mint·wake·steer into a single internal function** that both the `ext_proc` handler and the internal-RPC handler call — **one authority, two thin transport adapters**, not two implementations. (The connector also gains a small pod-dialer — mTLS + signed-id — but §9.1 already assigns that to the IM path, so it is not new surface.)
- **When B would win (reversible, but not flag-cheap):** if a post-M6 edge token-cap should blanket IM for free, or if many more socket surfaces make one central pod-dialer worth it. Flipping later changes the connector's byte-ownership shape (it stops dialing pods), so it is a re-pass, not a config toggle.

---

## 5. Channel-socket ownership (the split-brain killer)

> **Reconciliation:** under full edge adoption the lease + socket workers live in the **separate `channel-connector` Deployment** (control-panel.md §0.1), not "inside the gateway." Read "replica" below as a **channel-connector replica**. The lease *protocol* is unchanged and remains authoritative here.

### 5.1 The problem being dissolved

The survey confirmed the split-brain precisely: the channel daemon is a **separate process** (`daemon.py:1624-1630`) with its **own** event loop, **own** `conn.sessions`, **own** `conn.pending` coordinator, talking to the API only through a **file-based command queue** (`shared.py:38-60` + poll `daemon.py:1360-1410`) and **state files**. A permission Future created in one process is invisible to the other (`permission_coordinator.py:133` is a module-global singleton). There is no second process in the fork — but a new problem appears: an inbound **socket** surface (WeCom `WSClient`, OpenClaw bridge) is a long-lived connection that exactly one replica must own, or `N` replicas would all connect the same bot (WeCom double-connect → frame split-brain).

### 5.2 The lease (one owner per bot socket)

`channelconn:lease:{channel}:{bot_id}` (Redis T2 #13, `SET NX PX`, value=`replica_id`, **10s TTL / 3s heartbeat**, data-spine §4). Exactly one replica holds a given bot's socket. **Channel-socket ownership is an explicit, monitored role — not hidden under "HA."**

```
 replica A                         replica B                         replica C
   │ SET NX channelconn:lease:        │ SET NX … → FAILS (A owns)        │ SET NX … → FAILS
   │   wecom:botX  PX 10s → WON       │ (stands by; watches the lease)   │ (stands by)
   │ open WeCom WSClient (patches     │                                  │
   │   daemon.py:65-141), handshake   │                                  │
   │ heartbeat (re-PEXPIRE) every 3s  │                                  │
   ▼                                  ▼                                  ▼
 ─── A dies (no heartbeat) ──────────────────────────────────────────────────────
                          lease TTL expires (≤10s) ─▶ B/C race SET NX ─▶ B WINS
                          B re-handshakes the WeCom socket ─▶ declares channel READY
   ⟵ bounded, OBSERVABLE inbound stall (≤ ~10s) surfaced as a control-panel
     channel-health condition (NOT silently "highly available")
```

- **Bounded stall, surfaced honestly:** on owner death there is a bounded inbound stall (≤ lease TTL + re-handshake) during which that bot receives no messages. It is reported as a channel-health condition to the control panel (blueprint §3 "a bounded, observable inbound stall … *not* hidden under HA"), never papered over.
- **Re-handshake before ready:** the new owner must complete the WeCom/OpenClaw handshake **before** declaring the channel ready, so a half-open socket never silently drops frames.
- **No double-connect:** `SET NX` + fast TTL is the mutual exclusion; the brief window where two replicas might both believe they won is closed by the WeCom side rejecting the second connection and the loser backing off on the next heartbeat-fail.

### 5.3 Webhook surfaces vs socket surfaces

Not every surface needs a lease. **Socket surfaces** (WeCom `WSClient`, OpenClaw bridge) are long-lived inbound connections → lease-owned. **Webhook surfaces** (Feishu event-sub, Discord interactions — if/when built, §13-2) are stateless HTTP callbacks → *any* replica handles the POST; only the **outbound** reply may need the lease owner if the channel requires a persistent send socket (WeCom does; webhook replies do not). The lease scope is therefore "who holds the persistent socket," not "who may receive a callback."

### 5.4 What the lease owner does not hold

The lease owner holds the **socket**, not the **session/permission state**. Inbound frames it receives are resolved against Redis/data-plane like any other replica (§2), and a permission reply for a session whose IM socket it owns is still routed via `approval:index` (§7) — so even socket ownership does not re-introduce per-replica agent state. The lease is purely about *who terminates the bot connection*.

---

## 6. Pre-gate → buffer → ack → wake

This is blueprint flow 1 (cold-start IM) + the wake half of flow 3 (scheduled), located in the gateway.

### 6.1 The pre-gate (before ack, before wake)

The gateway checks account standing **before** promising anything or spending a wake:

```
pre-gate(account_id):
  status  := account.status        (Redis-cached; GetAccount on miss — data-spine §2.1)
  if status == 'disabled'/'offboarding'/'purged' → reply "access revoked"  ── NO wake
  else                                            → proceed to buffer+ack+wake
  # (M6) NO spend/cap check — spend enforcement is DEFERRED (BYOK + token-count-only, blueprint M6).
  #      `spend:reserve` + monthly_spend_cap_usd are not built "for the moment"; a token cap could
  #      return later via agentgateway rateLimit. Pre-gate = status check only.
```

Only **active** accounts are buffered + acked + woken (**M6: the under-cap check is deferred** — no spend enforcement for now). A revoked account gets an immediate honest answer and the pod stays asleep (no cold-start cost). *(When spend returns, the cap check slots back in here — blueprint M6.)*

### 6.2 Durable buffer, then ack

The "on it…" promise must be **durable before it is made**, or a replica death between ack and wake silently drops a promised message:

```
  RPUSH inbox:{account_id} <msg>   (T1 #1, AOF; LTRIM cap ~50; coalesce via inbox:dedup #2)
      └─ confirmed? ── no ─▶ do NOT ack (fail honestly)
                     ── yes ─▶ "on it…" ack to the surface   ◀ ack fires ONLY after the durable write
```

The inbox is **~50 msgs/account, ~1h per-entry TTL, duplicate-coalescing** (data-spine §4 #1/#2; blueprint minor "unbounded buffer" fix). The **gateway is the only inbox writer**; the **pod drains it on boot** (agent-pod §11 B7c, non-destructive peek / ack-on-completion).

### 6.3 The wake (CR patch is the sole authority)

```
  SET awake:lock:{account_id} NX PX~10s     (T2 #10 — serializes the one scale-up trigger)
      └─ won? ─▶ CR PATCH AgentTenant spec.wake.requestedAt   ◀ THE ONLY scale-up trigger (agent-pod §8.5)
                 PUBLISH wake:pod:{account_id}                 (T2 #12 — reconcile NUDGE only, droppable)
  operator scales 0→1 → re-attach PVC/Service/NetworkPolicy → POD BOOT (agent-pod §11) → drains inbox FIFO
```

- **CR patch only:** the gateway patches the `AgentTenant` `spec.wake.requestedAt` field (narrow K8s RBAC — wake subresource only, never create/delete/scale). The operator is the **sole** scaler (agent-pod §8.5, blueprint decision 12); `wake:pod` pub/sub is a nudge a dropped message just defers to periodic reconcile.
- **`awake:lock`** stops IM + scheduler + a second gateway replica from double-patching the CR. `PX~10s` self-heals a crashed waker.
- If the pod is **already awake** (`route:{account_id}` T2 #8 present), the gateway skips buffer+wake and opens the pod transport directly (§4). A `route` miss falls through to "treat asleep → wake," which is always safe (the pod drains an empty inbox).

### 6.4 Predictive wake on login (locked §13)

Cold-start latency is removed from the critical path by waking **speculatively, on login** rather than on first prompt (enhances decision 22):

```
WebUI login success → gateway pre-gates (active + under cap)
   → CR-patch wake IMMEDIATELY (background)             ◀ not waiting for a prompt
   user reads session list / types   ║ pod boots in parallel (§11, agent-pod)
   → user hits send → pod already READY → ZERO cold-start on the first turn
```

The speculatively-woken pod boots **account-scoped** (login supplies `account_id`) and **session-agnostic** — session resolution + `is_first_run` happen on the turn (§3), not at prewarm. It is race-free: the speculative wake and the turn's eventual wake are idempotent (`awake:lock` + idempotent CR patch, §6.3), so a warm pod just receives the dispatch.

**Idle handling.** The idle-grace stays the **single configurable `idle_grace_seconds` (30-min default, per-tenant CRD, agent-pod §13-1)** — a browse-only login therefore holds the pod for the normal idle window, and an admin may terminate it manually (§9.4). A separate shorter speculative grace was considered and **declined** (kept simple); it remains a trivial later add if speculative-wake waste ever bites. *(Optional cheaper trigger, not adopted: wake on composer-focus / first-keystroke instead of bare login — stronger intent, ~same latency.)*

---

## 7. Cross-replica permission relay & the no-impersonation gate

This is blueprint flow 2 (permission rendezvous, reply hours later) — the gateway's hardest job, and the one that most depends on holding **no** state.

### 7.1 Division of labor (the gateway holds no Future)

| Step | Owner | What |
|---|---|---|
| Mint `request_id`, create the Future, **pin the pod** | **Pod** | `pending[request_id]=future`; `HSET approval:index:{request_id} {session_uuid,account_id,pod,status=pending} EXPIRE 25h`; `SADD pin:approval:{account_id}` — all **before** the await (agent-pod §6.2) |
| Render the card to the bound surface | **Gateway** | translate `permission_request` (carrying `request_id`) → interactive card + plaintext `-> yes/no` fallback (lifted from `wecom_feedback.py:274-324`) |
| Receive the human reply (hours later, any replica) | **Gateway** | resolve via `approval:index`, enforce the no-impersonation gate, relay to the exact pod (§7.3/§7.4) |
| Resolve the Future, continue the run, clear the pin | **Pod** | `coordinator.resolve(request_id, …)` (agent-pod §6.3) |

The gateway never holds the Future and never holds a coordinator — it is a stateless courier between the human and the pod that owns the parked `await`.

### 7.2 Rendering the request

When the pod emits `permission_request`, it is rendered to the **session's bound surface** (from `binding:cache:{session_uuid}` T1 #6) — for **IM**, the **channel-connector** renders+sends a WeCom interactive card + a plaintext `-> yes/no` fallback (today `daemon.py:953-957`); for **browser**, the SPA's inline approval (served by the faces) over the **agentgateway**-terminated WS. The `request_id` is carried in the card payload so the eventual reply self-identifies. The Redis index TTL is `approval_window + 1h = 25h` (set by the pod, §7.1).

### 7.3 The late reply (cross-replica)

```
reply (card tap | "-> yes" text | browser click) lands on ANY gateway replica Rx
  │ extract request_id (from card payload / reply context)
  ├─ HGETALL approval:index:{request_id}  → {session_uuid, account_id, pod, status}
  │     • absent  ─▶ "this request expired because the session was interrupted; please re-ask" (pod died — accept-loss)
  │     • status==resolved ─▶ idempotent no-op (a dup/retried tap — NOT "expired", agent-pod §6.3)
  │     • status==pending  ─▶ continue
  ├─[7.4] NO-IMPERSONATION GATE: assert answering actor == account (else 403)
  └─ route the decision to the EXACT pod instance (by the `pod` field):
        • POST /permission/respond DIRECTLY to the pod (Rx → pod, mTLS; the `pod` field gives the endpoint)
          — there is no longer a "held" byte connection to reuse (agentgateway/connector hold it, control-panel.md §3)
        • in:reply:{request_id} (T1 #7, ~60s) retained as a FALLBACK only — largely vestigial under full edge
          adoption, since ANY brain replica can address the pod directly (NetworkPolicy permitting)
```

- **Exact-pod routing:** the decision must reach the *specific* pod instance holding the Future (the `pod` field in the index), re-establishing the transport via the per-tenant Service if needed (blueprint flow 2). A wrong pod has no matching `request_id`.
- **`in:reply` relay (now a fallback):** in the original design sticky-L4 was best-effort and the receiving replica might not own the pod transport, so `in:reply:{request_id}` (T1 #7, ~60s) handed the decision to the replica that did. **Under full edge adoption no brain replica holds the pod's byte stream** (agentgateway/connector do — control-panel.md §3), so the receiving replica simply POSTs the pod directly; `in:reply` survives only as a fallback (e.g. a replica without a route to the pod). Still Tier-1/AOF where used, since a dropped relay re-creates the zombie-pin.
- **Idempotency:** the index is kept at `status=resolved` (not `DEL`-ed) on resolve (agent-pod §6.3), so a duplicate tap from two replicas resolves **exactly once** and the second is a clean no-op, never a wrong "expired."
- **Pod died:** an absent pin → honest "expired, re-ask"; the next pod boot affirmatively purges the stale entry (agent-pod §6.5). The gateway never wakes a pod to answer a late reply (answering needs the live in-pod Future, which a fresh pod does not have).

### 7.4 The no-impersonation gate (locked, agent-pod §13-6)

The gateway is where decision-10's reversal is enforced at ingress: **a permission request is answerable only by the user — via their authenticated WebUI session (their own key) or their bound IM channel.** Before relaying any decision the gateway asserts the **answering actor's `account_id` == the session's owning `account_id`** (from the index). An impersonator (an admin acting-as the user, blueprint decision 10) is **rejected (403)** — *even though* the same impersonator may still spend the target's budget and use their tools under audit. The two enforcement points are layered: the gateway rejects an impersonator's *answer* at ingress, and the pod independently re-asserts the same hard account-match on `/permission/respond` (agent-pod §6.4/§10) — defense in depth, since the pod trusts the gateway-asserted actor. For IM, the existing `asker_id` gate (only the original asker may answer, `daemon.py:604-630`) is preserved as a *second*, narrower check inside the owning account.

---

## 8. Outbound fan-out & multi-surface attach

### 8.1 Who owns output (reconciled)

The pod makes **no** IM calls (agent-pod §9.6: IM egress denied). Output reaches the user two ways under full edge adoption: **browser** output is streamed **pod ⇄ agentgateway ⇄ client** (agentgateway re-frames bytes; **no app-side fan-out** — control-panel.md §3); **IM** output runs through the **channel-connector's** fan-out (it owns the outbound socket, §5), lifted from the daemon's reply machinery and made surface-aware. The fan-out *logic* below is unchanged; its home for IM is the channel-connector:

| Path | Lifted from | When |
|---|---|---|
| **Stream reply** (re-use the inbound frame's `stream_id`) | `reply_stream` (`daemon.py:844-846`) | short result, < ~4000 chars, within the inbound frame's live window (~5s) |
| **Proactive push** (decoupled from the inbound frame) | `send_message` chunked to ~3500-char blocks (`daemon.py:1041-1054`) | long result, or any reply *after* a human-feedback wait (the inbound frame's stream is long gone) |
| **Card render** | `wecom_feedback.py:274-324` | permission/ask-user requests (§7.2) |
| **Browser stream** | **agentgateway** streams pod⇄client (control-panel.md §3) | the WebUI composer, token-by-token — *not* app-side fan-out |

The 5s/4000-char split and the markdown/`template_card` formatting are WeCom transport limits (memory: *WeCom aibot is push-only, no history API*) — they belong in the gateway's WeCom adapter, not the pod.

### 8.2 Multi-surface attach (blueprint flow 4B)

A session has **one read-write binding** + **N read-only attachers** (data-spine §2.4). When a browser opens a session that an IM channel is actively running:

```
browser GET session (IM-running)
  → state-reader replays the JSONL prefix (wake-free, data-spine §3.9)
  → gateway tails the live appends from the IM run (read-only)
  → composer DISABLED, marked "owned by IM channel X"
  → take-over only once the session goes idle and the in-pod lock frees (lock:session mirror, T2 #11, display-only)
```

The gateway reads `lock:session:{session_uuid}` (T2 #11) **for display only** — it is a status mirror, never an acquisition gate (the in-pod `asyncio.Lock` is authoritative, agent-pod §5.3). This is what kills the dual-authority race: the gateway shows "owned by IM," but never *enforces* the lock.

### 8.3 Session-browser reads are wake-free

`GET /api/sessions` and transcript reads go through the data-plane (`ListSessionsByAccount`) + the `state-reader` (transcript bytes), **never waking the pod** (data-spine §3.9). The gateway holds no `/export` mount; it reaches transcript content *through* the `state-reader`. Only *running a new turn* wakes the pod.

### 8.4 Proactive / scheduled origination

The central scheduler originates proactive IM **only through the gateway, only to the user's own bound channels** (blueprint §3). **Locked (§13-7):** the gateway exposes an internal `PushToChannel(account_id, session_uuid, payload)` seam (scheduler→gateway, mTLS) that runs through the same fan-out + chunker + lease owner — the scheduler never touches an IM endpoint directly. **The scheduler↔gateway orchestration is now RESOLVED (scheduler.md §6):** delivery rides the scheduler's dispatch frame (a `notify` flag), the **pod** emits the result, and because a scheduler-origin run has no live client socket the gateway delivers it proactively through this `PushToChannel` path — so the literal caller is the gateway's own outbound path, the binding is gateway-resolved from `account_id` (no impersonation), and the scheduler stays fire-and-forget.

---

## 9. Pod routing & connection management

### 9.1 Finding the pod

```
route:{account_id} (T2 #8, ~30s sliding, pod heartbeat) → {pod_ip, pod_name, state}
  • present + state busy/idle ─▶ open transport to the per-tenant Service DNS (mTLS + signed id)
  • absent / expired          ─▶ treat asleep → buffer+ack+wake (§6)   ◀ a miss is always SAFE (wake is idempotent)
```

`route` is a pure optimization (T2, reconstructible); a miss never causes incorrectness — it just triggers a (possibly redundant) wake, which the operator's `awake:lock` + idempotent CR patch absorb.

> **Reconciliation:** for the **browser** run the brain does **not** "open the transport" — it **returns the endpoint** (EPP steer, control-panel.md §2.3) and **agentgateway** connects. "Open transport (Service DNS, mTLS + signed id)" now applies to the **IM connector path** (§4.4) and to the discrete **permission-relay POST** (§7.3) — both still on-demand, no persistent connection (§9.3).

### 9.2 Mid-stream pod death

If the pod dies mid-turn (node evict, OOM), the gateway's pod transport drops. Per accept-loss (decision 5), the in-flight turn's tool may not have run and the parked Future is gone; the gateway surfaces a clean "the session was interrupted, please re-ask" to the client and does **not** auto-retry a partially-executed turn (which could double-run a side-effecting tool). The transcript survives to its last write on the PVC; the next turn RESUMEs the same immutable `session_uuid`.

### 9.3 Connection model (locked §13-6)

The gateway opens a pod transport **on-demand per turn**; it holds **no persistent per-session connection**. The *pod* is the long-lived holder of the Future and the lock, so the gateway transport need not outlive the turn; the late-permission-reply path re-establishes via the Service (§7.3). This keeps the "holds no state" invariant clean — persistent connections would add per-replica fan-in state.

### 9.4 Admin pod termination (locked requirement)

An admin can **terminate a user's pod from the admin dashboard** (decision-10 lifecycle). The action is **not** a gateway feature — it is a **Control Panel** call (`/admin/v2/*` + a confirmation dialog + audit-before-return; a dangerous op per the design spec) executed by the **Operator** (the sole scaler — CR patch to scale 1→0, graceful drain per agent-pod §8.4 or forced). **Gateway impact is none new:** a pod terminated under an in-flight turn drops its transport, which the gateway already handles as a mid-stream death (§9.2 — clean "session interrupted, re-ask," no auto-retry). Full UI + operator mechanics will be specified in the Control Panel + Operator drills (not yet drilled). *(Related sibling, same treatment: stop a running turn without killing the pod — a Redis cancel signal to the live run; also a Control-Panel verb.)*

---

## 10. Auth verification & abuse controls per surface

> **Reconciliation:** surface-credential *verification* moves to the **edge** — **agentgateway** for HTTP/JWT, the **channel-connector** for socket surfaces. The **brain** retains **identity *resolution*** (verified principal → `account_id`, §2) and **minting the pod-trusted assertion** (§2.4). CORS/TLS/coarse-rate-limit are agentgateway policies (control-panel.md §1.2/§8).

### 10.1 Surface authentication (the trust conversion)

| Surface | Verification | Verified-in-code? |
|---|---|---|
| **Browser** | JWT (`auth.py:47-95` decode_jwt; per-user/global API key fallback; `enable_anonymous` config) | Yes |
| **WeCom** | signed callback / `WSClient` channel auth (the SSL/CONNECT-proxy patches, `daemon.py:65-141`) | Yes |
| **OpenClaw** | Ed25519 / mTLS bridge identity | Yes |
| **Feishu** | event-sub verification token + URL challenge | **No** (blueprint-named, §13-2) |
| **Discord** | interaction Ed25519 signature header | **No** (blueprint-named, §13-2) |

Each surface's raw credential is verified **at its edge** — **browser JWT signature/issuer/audience at agentgateway** (control-panel.md §1.2 `jwtAuthentication`); **WeCom/OpenClaw socket credentials at the channel-connector** (which holds the socket, §5; these never traverse agentgateway). The **brain** then converts the verified principal into the internal signed `account_id` (§2.4). The pod never sees a surface credential.

### 10.2 Abuse controls

- **Pre-gate as a wake-shield (§6.1):** disabled/capped accounts never trigger a cold-start — the cheapest abuse mitigation (no pod spin-up for a revoked actor).
- **`/link` lockout:** 5/min/channel, single-use 300s codes (§2.5) — bounds link-guessing.
- **Inbound buffer cap:** ~50/account + coalescing (§6.2) — bounds a flood from one account.
- **Rate limits:** the coarse-429-backstop **`backendRef`→metering-ratelimit seam is deferred (M6** — no spend/metering service for now). Finer per-surface/business limits (e.g. the `/link` lockout) stay in the brain/connector (today's `rate_limiter` in `auth.py:170`, lifted + Redis-backed, holding across replicas). A token cap could later return as a plain agentgateway edge `rateLimit` policy (control-panel.md §8).
- **Channel access modes (locked §13-5):** `wecom_access_allowed` (`daemon.py:199-229`) — **ALL / whitelist / private** — preserved (§0), now backed by central config. For **any group** the gate validates the **sender**; a validated message then routes via the sender's `identity_link` → the sender's binding → the sender's **own session/account** (no `chat_id` key, §2.6). `chat_id` is reply-addressing only.
- **CORS:** an **agentgateway** `frontend`/`cors` policy now (control-panel.md §1.2), not app middleware (narrowed from today's `allow_origins=["*"]`, `main.py:180`).

### 10.3 The brain as the sole *pod-trust* boundary (reconciled)

There are now **two** trust edges, not one. **(edge)** agentgateway verifies the **platform JWT** the human presents — authenticating the human to the platform. **(internal)** because the pod strips auth and trusts the **brain**-asserted `account_id` (§2.4), the **brain's signing key** is the crown jewel of the *tenant* boundary. That assertion is **locked (§13-1): service-mesh mTLS + a short-TTL signed JWT** verified via JWKS, **injected by the brain via `ext_proc`** (control-panel.md §2.1). The pod's NetworkPolicy admitting ingress only from agentgateway/the mesh (agent-pod §9.6) is the network half; the signed `account_id` is the application half. **The platform JWT and the `account_id` assertion are distinct tokens** (§2.4).

---

## 11. Code deltas (current → Agent Gateway)

> **Reconciliation:** the **NEW `gateway/*` files below are the brain modules, now homed in the Control Panel app** (control-panel.md §10 lists them as `controlpanel/extproc.py` / `controlpanel/admin_api/` / `controlpanel/agentgw/`) — one codebase, not a separate tier. Rows that said "the gateway terminates / verifies JWT / serves static / sets CORS" are **superseded by agentgateway** (control-panel.md §1/§3/§8); they remain here to map the *original* code, corrected inline below.

| File:line (current) | Change |
|---|---|
| `services/channels/daemon.py:1624-1630,292,306-353` | Delete the standalone process + the 100ms command-poll loop; the WeCom worker becomes a lease-owned (§5) component inside the gateway. |
| `services/channels/daemon.py:65-141,146-196` | **Lift** the `WSClient` SSL/CONNECT-proxy patches + frame normalizer into the gateway channel worker (verbatim logic). |
| `services/channels/daemon.py:285,1312-1356,664` | Delete `conn.sessions` + `.priva.wecom.sessions.json` load/save/cleanup; resolve **sender identity → session** via `channel_binding` (data-plane, identity-keyed) + `binding:cache` (Redis). **No `wecom_session`, no chat_id→session resolution** (§2.6). |
| `services/channels/daemon.py:288,892-959,604-630,1122-1148` | Delete the daemon's in-process `PermissionCoordinator`/`conn.pending`; permission lives in-pod; the gateway only renders cards (§7.2) + relays (§7.3). Keep the `asker_id` answer gate (§7.4). |
| `services/channels/daemon.py:844-846,1041-1054` | **Lift** `reply_stream` + chunked `send_message` (3500) into the gateway outbound fan-out (§8.1). |
| `services/channels/daemon.py:199-229,632-643` | **Lift** `wecom_access_allowed` + `/reset` into the gateway, backed by central config; `/reset` → `RebindChannel` (fresh uuid + `first_run_done=0`, M5 §3.2). |
| `services/channels/shared.py:38-60`; poll `daemon.py:1360-1410` | **Delete** the file command queue; replaced by Redis lease + CR-patch wake + data-plane config reads. |
| `services/channels/daemon.py:1539-1570` | **Delete** heartbeat/state files; Redis `route`/`awake`/`channelconn:lease` + channel-health conditions. |
| `services/channels/config_store.py` | **Delete** the fcntl YAML store; channel config via data-plane `channel_config_wecom`/`_openclaw` RPCs. |
| `openclaw_bridge.py`; `main.py:133-153` | **Lift** bridge ownership to a Redis lease; **delete** the auto-connect-over-`list_users()` loop (cross-tenant boot violation). |
| `routers/agent.py:346-369`; `service.py:977,433` | **agentgateway** terminates the client SSE/WS (control-panel.md §3); the **brain** makes the `ext_proc` decision only; serialization stays in-pod (agentgateway/connector re-frame, not re-serialize). |
| `routers/agent.py:734-747` (WS path) | Becomes the gateway's primary browser transport candidate (§4.3, §13-3); permission replies ride it instead of a side-channel POST. |
| `routers/agent.py:479-500,485,488-490` | `/permission/respond` ingress: `HGETALL approval:index` → relay (§7.3); the `owner_username` 403 becomes the hard no-impersonation gate (§7.4). |
| `auth.py:47-115` (`authenticate_raw_token`, `get_current_user`) | JWT **signature** verification → **agentgateway** edge (control-panel.md §1.2); **identity resolution** + signed-`account_id` mint stay in the **brain** (reads verified claims); user store → data-plane Accounts/Identities RPCs (§2.4). |
| `auth.py:170` (`rate_limiter`) | **Lift** + Redis-backed for finer brain/connector limits; the **coarse 429 is an agentgateway edge policy → our ratelimit service** (control-panel.md §8) (§10.2). |
| `routers/auth.py`, `routers/admin*.py`, `routers/channels.py`, `routers/scheduler.py` | Admin → control panel; channel admin → gateway internal API; scheduler → central scheduler (proactive IM via §8.4 push seam). |
| `main.py:74-79,187,221,180` | Static SPA served by the **Control Panel app faces** (`/ui`,`/admin`), **path-routed by agentgateway** (control-panel.md §5); CORS → **agentgateway** `frontend`/`cors` policy (control-panel.md §1.2), not app middleware. |
| **`gateway/identity.py` (NEW)** | `ResolveIdentity` cache + `/link` challenge mint/redeem (§2.5); JWT verify; signed-`account_id` minting. |
| **`gateway/sessions.py` (NEW)** | local `uuid4().lower()` mint (WebUI) + `BindChannel`/`first_run_done` CAS (IM) + binding resolution; the empty/populated `session_id` surface contract (§3.1, M5). |
| **`gateway/channels/` (NEW)** | per-surface workers (WeCom socket, OpenClaw bridge; Feishu/Discord stubs — §13-2), the `channelconn:lease` owner role (§5), the fan-out + chunker (§8). |
| **`gateway/relay.py` (NEW)** | the pre-gate (§6.1), buffer+ack+wake (§6), the `approval:index`/`in:reply` permission relay (§7), the no-impersonation gate (§7.4). |
| **`gateway/podconn.py` (NEW)** | `route` lookup, per-tenant Service transport (mTLS + signed id), 429/503 handling, mid-stream-death handling (§9). |
| **Control Panel K8s manifests (NEW — control-panel.md §10)** | `Deployment` (N replicas; brain `ext_proc` + faces + internal ports), narrow RBAC to **patch `AgentTenant spec.wake.requestedAt` only** (§6.3), NetworkPolicy (**ingress: agentgateway/mesh only**; egress to data-plane/Redis/K8s-API/pods). The L4/sticky Service + TLS listener are **agentgateway's** Gateway/HTTPRoute now (control-panel.md §1.2). No such manifests exist today. |

---

## 12. Resolved risks (adversarial pass)

> **Reconciliation:** "gateway" below = the **brain** unless the edge is meant. **G10**'s trust boundary is now split — agentgateway verifies the **platform JWT**; the brain is the sole minter of the **pod-trusted `account_id`** (§10.3). **G15**'s sticky-LB is **agentgateway's** concern now (the brain's `ext_proc` is per-request stateless, §1.3). The remaining risks are brain-internal and unchanged. Full edge adoption also adds *new* edge risks (ext_proc "experimental", scale-to-zero wake not native, new edge SPOF) — tracked in **control-panel.md §11 (C1–C12)**, not duplicated here.

| # | Risk | Severity | Resolution (folded into spec) |
|---|---|---|---|
| G1 | Channel socket = per-bot SPOF; fast TTL risks WeCom **double-connect** split-brain | blocker | `channelconn:lease` (10s/3s), monitored role, re-handshake **before** ready; bounded inbound stall surfaced as a channel-health condition, not hidden under HA (§5). |
| G2 | A permission reply lands on a replica that doesn't own the pod transport → unresolvable / zombie pin | blocker | `approval:index` (exact `pod` field) + `in:reply:{request_id}` T1 relay; resolve on any replica, route to the exact pod (§7.3). |
| G3 | Gateway holding the Future / coordinator would re-create the split-brain across replicas | blocker | The gateway holds **no** Future/coordinator/lock — all in-pod; it only relays via Redis (§1, §7.1). |
| G4 | Impersonator answers an approval (blueprint decision-10) | major | Hard no-impersonation gate at gateway ingress (403 if actor ≠ owning account), re-asserted in-pod (§7.4, agent-pod §13-6). |
| G5 | "On it…" ack fires before the buffer is durable → replica death drops a promised message | major | Ack fires **only after** the Tier-1 `inbox` RPUSH confirms (§6.2). |
| G6 | Instant ack/wake for a disabled or hard-capped account (wasted cold-start, spend leak) | major | Pre-gate **before** ack+wake; revoked/capped answered with no wake (§6.1). |
| G7 | Two surfaces both CREATE over one JSONL (mint TOCTOU) | major | **M5:** IM = `first_run_done` CAS on `channel_binding` (race-free via single-writer data-plane); WebUI = gateway-mint-on-empty + disk-existence guard, backstopped by reconcile-as-create (§3.3). No `session_index`/`ClaimFirstRun`. |
| G8 | Client invents/collides a `session_uuid` (cross-tenant resume / no-remap) | major | Gateway mints (locally for WebUI, binding-write for IM); client-supplied id is a RESUME selector only, never a mint source, and resolves only within the requesting account's path subtree (§3.1, agent-pod §13-7). |
| G9 | No channel→account mapping today → who runs/pays for an IM turn is undefined | major | `identity_link` + `/link` flow (one `(surface,user)`→one account); group-chat turns run under the binding-owner account, audited (§2.5, §2.6). |
| G10 | Compromised pod forges another tenant's `account_id` (the pod blindly trusts it) | major | Gateway is the sole trust boundary; signed short-TTL assertion + pod NetworkPolicy admits only the gateway (§2.4, §10.3); signing mechanism = §13-1. |
| G11 | Two wake transports (CR patch + Redis) with no serialization → double scale-up | major | CR patch is the **only** trigger, guarded by `awake:lock`; `wake:pod` is a droppable nudge (§6.3, agent-pod §8.5). |
| G12 | Duplicate / retried permission tap → wrong "expired, re-ask" for an answered request | minor | Index kept at `status=resolved` (not DEL); dup taps idempotent no-op (§7.3, agent-pod §6.3). |
| G13 | Mid-stream pod death auto-retried → double-runs a side-effecting tool | minor | No auto-retry; clean "interrupted, re-ask"; transcript survives to last write; next turn RESUMEs same uuid (§9.2). |
| G14 | Unbounded inbound buffer → flood/overflow | minor | ~50/account cap + ~1h TTL + dedup-coalescing (§6.2). |
| G15 | Sticky-LB miss adds latency to the reply relay | minor | Best-effort sticky L4 + `in:reply` T1 fallback under a <50ms one-hop budget (§1.3, §7.3). |
| G16 | Auto-connect-all-users boot loop (`main.py:133-153`) is a cross-tenant violation in a multi-replica world | minor | Deleted; the gateway connects one socket per *enabled* channel config from the data-plane, lease-owned (§0, §5). |

---

## 13. Resolved Decisions (was Open Questions — locked 2026-06-18)

All seven questions were answered by the user on 2026-06-18, plus two design changes that emerged in the same round — **M5** (a data-spine schema reversal) and the **predictive-wake** UX requirement — and one new control-panel requirement (**admin pod termination**). Each is folded into the cited body section; this list is the decision record.

1. **Gateway↔pod identity = mTLS + short-TTL signed-JWT claim (§2.4, §10.3) — RESOLVED.** The pod blindly trusts a gateway-asserted identity, so the edge is signed two ways: service-mesh **mTLS** + a **short-TTL signed JWT** claim of `{account_id, session_uuid, is_first_run, actor_account_id}` the pod verifies via JWKS. A compromised pod cannot forge another tenant (the claim is signed by the gateway key; the pod's NetworkPolicy already admits ingress only from the gateway). Plain trusted-header and shared-HMAC were rejected (forgeable / poor rotation). *(Reconciled: this is the **brain→pod** assertion, injected via `ext_proc` — control-panel.md §2.1 — and is **distinct** from the **platform JWT** that agentgateway verifies at the edge. See §2.4/§10.3.)*

2. **v1 ships WeCom only (§1.1, §5, §10.1) — RESOLVED.** v1 stands up **only the WeCom socket-lease worker**. **OpenClaw** is already coded but **deferred**; **Feishu/Discord** stay blueprint-only. The §5 lease pattern is written to generalize, so adding a surface later is instantiating a worker, not a redesign.

3. **Browser transport = WS primary, SSE fallback (§4.3) — RESOLVED.** The primary browser transport is **WebSocket** — stream output, permission replies, and mid-turn user messages ride one bidirectional socket (collapsing today's separate `/permission/respond` POST). SSE is kept as a fallback. *(Reconciled: **agentgateway** terminates this WS at the edge — control-panel.md §3 — and routes it; the brain stays off the byte path.)*

4. **`is_first_run` lives on the binding / disk, not a session table (§3.2, §3.3) — RESOLVED by M5.** The original plan (an atomic `ClaimFirstRun` CAS on `session_index.status`) is **withdrawn** — there is no `session_index` (M5). Instead: **IM** uses a `first_run_done` boolean **CAS on `channel_binding`**; **WebUI** uses **gateway-mints-on-empty** (inherently first-run) + a **disk-existence guard** on resume-by-id (backstopped by agent-pod §2.5 reconcile-as-create). No new data-plane RPC needed.

5. **One identity → one account; route by the validated sender, no `chat_id` key (§2.6, §10.2) — RESOLVED (refined 2026-06-18).** A given `(surface, user)` maps to exactly **one** account (`ux_identity_surface`). Bindings resolve by **`identity_id`, never `chat_id`**: for **any group** message the only group-specific step is **validating the sender** (the three access modes ALL / whitelist / private, `wecom_access_allowed`, `daemon.py:199-229`); a validated message routes via the **sender's identity** → the sender's binding → the sender's **own session/account/budget**. `chat_id` is reply-addressing only. *(This refines the earlier "shared group session, bot-owner pays" framing — that assumed `chat_id` keying, now withdrawn along with `wecom_session`, data-spine §2.12. An ALL-mode bot requires the sender to be `/link`-ed.)*

6. **Gateway↔pod connection = on-demand per turn (§9.3) — RESOLVED.** The gateway opens a pod transport per turn and holds no persistent per-session connection — the *pod* is the long-lived holder of the Future + lock, so the gateway transport needn't outlive the turn; the late-permission-reply path re-establishes via the Service. Keeps the "holds no state" invariant clean. *(Reconciled: under full edge adoption the **byte** connection is agentgateway⇄pod (browser) / connector⇄pod (IM); the brain's only direct pod call is the discrete **permission-relay POST** — §7.3 — still on-demand, no persistent connection.)*

7. **`PushToChannel` is a gateway hook for proactive IM (§8.4) — RESOLVED.** The gateway exposes an internal `PushToChannel(account_id, session_uuid, payload)` seam for **server-initiated** messages (scheduled-job output, system notices), routed through the same fan-out + chunker + lease owner the interactive replies use. It generalizes the daemon's proactive push (`send_message`, `daemon.py:1041-1054`). The scheduler↔gateway orchestration is now **resolved in `scheduler.md` §6** (delivery rides the dispatch frame; the pod emits; the gateway pushes proactively; the scheduler is fire-and-forget) — the gateway exposes the hook, scheduler.md drives it.

**M5 — filesystem is the session store; no `session_index` table (ripples to data-spine + agent-pod).** The central DB carries **no session table**. The JSONL on the per-user volume is the source of truth (decision 15); the dropped `session_index` was only a non-authoritative index, so removing it is reversible/additive later. Gateway consequences: minting is **gateway-local for WebUI** (`uuid4().lower()`, no DB round-trip) and a **`channel_binding` write for IM** (fresh uuid + `first_run_done=0`); session **listing** is the SDK list when the pod is awake and a **wake-free per-account filesystem glob** (`projects/*/<uuid>.jsonl`, via the state-reader) when asleep (§8.2/§8.3); `cwd_hash`/`config_home` persistence is gone (the in-pod SDK derives paths — one pod = one account). Minimal central schema = **`account` + `identity_link` + `channel_binding`(+`first_run_done`)**. *Full body-rewrite of data-spine (§2.3/§2.7/§3.3/§3.6) and agent-pod (§2.2/§2.7/§3.4) is tracked under their M5 revision notes.*

**Predictive wake on login (§6.4) — locked.** On a successful WebUI login the gateway runs the pre-gate and, if the account is active + under cap, fires the **CR-patch wake immediately** — so the pod boots *while the user reads their session list / types a prompt* and is READY by send-time (zero cold-start on the first turn). Idempotent with the turn's eventual wake (`awake:lock`, §6.3). The idle-grace stays the **single configurable `idle_grace_seconds` (30-min default, per-tenant CRD)** — a separate speculative knob was declined; the lever for an unused speculative wake is admin termination + the normal 30-min idle.

**Admin pod termination (Control Panel + Operator scope) — locked requirement.** An admin can **terminate a user's pod from the admin dashboard.** This is a **Control Panel** action (UI + `/admin/v2/*` + a confirmation dialog + audit-before-return — a dangerous op per the design spec and CLAUDE.md) executed by the **Operator** (the sole scaler — CR patch to scale 1→0, graceful drain per agent-pod §8.4 or forced). It is **decision-10 lifecycle scope** and **will be detailed in** the Control Panel + Operator drills (not yet drilled). **Gateway impact: none new** — a pod vanishing under the gateway is already handled by §9.2/§9.4 (mid-stream death → clean "session interrupted, re-ask").

**Channel-connector → brain call = internal RPC, not synthetic re-inject (§4.4) — locked 2026-06-19.** The IM path drives the brain through its **internal routing RPC** (`:8081`, mTLS — control-panel.md §0.1), *not* by re-injecting a synthetic `/v1` through agentgateway. The connector calls the brain (resolve·mint·wake·steer), then **dials the pod itself** (§9.1) and fans out (§8); agentgateway stays out of the IM byte path. The brain factors `resolve·mint·wake·steer` into **one internal function** shared by the `ext_proc` handler (browser) and the internal-RPC handler (IM) — one authority, two transport adapters. Re-inject (B) was declined because owning the outbound socket is *why* the connector is separate, B's edge-policy win is M6-deferred, and B would force the connector to forge a platform JWT (§4.4). Reversible post-M6, but it changes the connector's byte-ownership shape (a re-pass, not a toggle). *This closes the last open platform item; all components are drilled.*
