# Byte path & components (as-built, minikube alpha)

The runtime topology of the per-account-pod slice (agentgateway + operator + EPP).
Detailed status: [`../migration_progress/phase-3-agentgateway-operator.md`](../migration_progress/phase-3-agentgateway-operator.md).
The EPP-over-TLS gotcha: memory `agentgateway-epp-tls`.

There are **two** byte paths into the `ar-<account>` pods: the **web** path (`browser ═
agentgateway ═ ar`, via the EPP) documented below, and the **IM** path (`Feishu ═
channel-connector ═ ar`) — see [IM channel byte path](#im-channel-byte-path-feishu--channel-connector),
whose ingress is *inverted* (an outbound WS long-connection, not a webhook).

## Diagram

```
 LEGEND   ═══ runtime byte path (streamed data)     ─── control path (decision / state)
          (P) plaintext HTTP   (G) gRPC plaintext   (T) gRPC over TLS   (K) Kubernetes API

                                  ┌─────────┐
                                  │ BROWSER │
                                  └────┬────┘
                                       ║ (P) HTTP  :80   (login / SPA / admin ride this too)
                                       ▼
                ┌──────────────────────────────────────────────────┐
                │     agentgateway   (pod: priva-gateway)            │   the edge — carries the bytes
                │     Gateway API data plane (Rust)                  │
                └───┬──────────────────────────────────┬────────────┘
     (P) control:   │                                   ║  runtime: /api/agent,/files,/pty,
     /,/admin,      │                                   ║           /hooks,/subagents
     /api/auth,     │                                   ║
     /api/admin,    │                          per req  ║   ┌── (T) ext_proc "which pod? + wake"
     *config        │                                   ║   │
                    ▼                                   ║   ▼
          ┌───────────────────────────┐                ║  :9000  ext_proc EPP  (TLS)  ◄── the brain
          │     control-panel (pod)    │◄═══════════════╝  resolve · wake · steer · provision
          │  :8080 HTTP  SPAs+auth+admin│                       │            ▲
          │  :9000 gRPC  ext_proc EPP   │                       │ returns    ║ (P) HTTP stream
          └────┬───────────────┬────────┘                       │ podIP +    ║  to the steered pod
        (K) AgentTenant CR     │ (G) accounts/        (K) create │ signed     ║
               │               │     secrets        AgentTenant  │ token      ▼
               ▼               │                         CR      │   ┌─────────────────────────────┐
       ┌────────────────┐      │                                 │   │  agent-runner  ar-<account>  │
       │ operator (kopf) │     │                                 │   │  :8091 HTTP  runtime + claude│
       │ CR→Deploy/Svc/  │─────┼──(K) scale 0↔1 + inject Secret──┼──►│  trusts EPP-signed token     │
       │ PVC; wake/idle  │     │                                 │   └──────────────┬──────────────┘
       └───────┬─────────┘     │                                 │       (G) state + activity heartbeat
          (G) read creds       ▼                                 ▼                  ▼
       ┌──────────────────────────────────────────────────────────────────────────────────┐
       │   data-spine  (pod)   :50051 gRPC (plaintext)                                      │
       │   accounts · quota · secrets (Fernet) · SQLite on a RWO PVC                        │
       └──────────────────────────────────────────────────────────────────────────────────┘
```

**How to read it:** the *bytes* of an agent turn flow `browser ═ agentgateway ═ agent-runner pod`.
Per request, agentgateway makes a **(T) side-call to the EPP** (control-panel `:9000`) asking "which pod,
and wake it," then streams straight to that pod. Provisioning, scaling, and state are all control-path
(K8s API + gRPC to data-spine). agentgateway is **never** on the byte path's far side — it relays; the EPP
only decides.

## Components

| Component | Port(s) | Role | Inbound transport | Notes |
|---|---|---|---|---|
| **agentgateway** (`priva-gateway`) | `:80` | edge / data plane; carries the runtime bytes | (P) from browser | third-party Rust proxy (Gateway API); auto-provisioned from the `Gateway` CR |
| **control-panel** | `:8080` HTTP, `:9000` ext_proc | SPAs + auth + admin + config **and** the EPP brain + provisioner; also the `/api/cp-proxy` large-read proxy lane | (P) `:8080`, **(T) `:9000`** | EPP resolves account → wakes pod → returns endpoint + signed runner token |
| **operator** (kopf) | — | `AgentTenant` CRD reconcile; **sole scaler 0↔1**; injects per-pod creds Secret at wake | (K) watch/patch | idle sweep scales back to 0 |
| **data-spine** | `:50051` gRPC | accounts / quota / **secrets (Fernet)** + SQLite (RWO PVC) | (G) plaintext | single writer (`replicas:1`, `Recreate`) |
| **agent-runner** `ar-<account>` | `:8091` HTTP | one scale-to-zero runtime pod per account; spawns the `claude` CLI | (P) from gateway **and channel-connector**, (G) to data-spine | trusts the EPP-injected HS256 signed `account_id`; creds from the mounted Secret |
| **terminal** `term-<account>` | `:8092` WS/HTTP | independent scale-to-zero Web Terminal; same image/workspace/uid as Runner, separate env/process/net/cgroup | (P) from gateway | Go `terminald`; no Runner `envFrom` or SA token; EPP-overwritten internal auth header |
| **channel-connector** | `:8083` HTTP (internal) | IM byte path: holds one Feishu/Lark **WS long-connection per account** (thread-per-app), relays DM ⇄ ar pod | (P) `:8083` internal only — control-panel reconcile push + probes, **no gateway route**; the Feishu messages are **not** inbound (see below) | data-plane client; single replica `maxSurge:0` (same app ⇒ exactly one WS); **no Fernet key** (data-spine returns the secret) |

## Transport (alpha)

Every hop is **plaintext HTTP / gRPC** *except the one EPP hop*: agentgateway dials the InferencePool
EndpointPicker (`control-panel:9000`) over **TLS** (GIE convention; it skip-verifies in-cluster), so the EPP
serves TLS (self-signed, ALPN `h2`). This is forced by agentgateway, not a choice — a plaintext EPP fails
with `InvalidContentType`. Real HTTPS / mTLS / JWKS / edge-TLS are **deferred** (plan §L).

## WebSocket auth (chat `/api/sandbox/agent/ws/run`, terminal `/api/terminal/ws`)

The edge authenticates a WS on the **upgrade** request, which has no body and no
`Authorization` header. The SPA passes the JWT as a **subprotocol**:
`new WebSocket(url, ['priva.ws.v1', 'priva.token.<jwt>'])`. The EPP reads the
`priva.token.` entry off `Sec-WebSocket-Protocol`; the agent-runner echoes back
only `priva.ws.v1` in the upstream handshake so the browser connection completes. This keeps
the token out of the URL and gateway access logs. (The EPP still accepts a legacy
`?token=` query param as a fallback for stale cached bundles.)

## Agent-runner runs as root → `IS_SANDBOX=1`

The runtime drives the `claude` CLI with `bypassPermissions`
(`--dangerously-skip-permissions`), which the CLI refuses as root. The per-account
pod is an isolated sandbox, so the operator sets `IS_SANDBOX=1` in the AR pod env
(the CLI's escape) — otherwise the CLI exits 1 and every run fails.

## Runtime request walkthrough

1. Browser → agentgateway (`:80`).
2. Path `/api/agent/...` matches the runtime `HTTPRoute` → `InferencePool agent-runners`.
3. agentgateway calls the EPP (`control-panel:9000`, **TLS**) per request: control-panel resolves the
   account from the JWT, ensures the pod is awake (patches `AgentTenant.spec.wake` → operator scales 0→1 +
   injects the creds Secret), and returns `x-gateway-destination-endpoint = <pod>:8091` + a signed runner token.
4. agentgateway streams the request straight to the woken `agent-runner` pod, which trusts the token and runs.
5. Idle past grace → the operator scales the pod back to 0. (Cold start re-wakes in ~4s; warm is instant.)

## IM channel byte path (Feishu · channel-connector)

A second, independent byte path reaches the same `ar-<account>` pods from Feishu/Lark DMs.
It does **not** ride agentgateway or the EPP — the **channel-connector** is a standalone
data-plane client. Model B: each tenant registers its own **self-built Feishu app**, so one
app ⇔ one account (no `union_id` routing).

The defining fact — and the thing to internalise — is the **inverted ingress**: a Feishu DM
is **not** an inbound webhook. There is **no** public callback URL, no `Ingress`, no gateway
`HTTPRoute`. The connector **dials OUT** to the Feishu open platform over a **WebSocket
long-connection** (`lark_oapi` `ws.Client`), and Feishu **pushes** `im.message.receive_v1`
events **down that same connection**. So the message "arrives" on a socket the connector
opened outbound — from the pod's network view the DM is an *egress-initiated* stream, which is
why it works behind NAT with zero inbound surface.

```
 LEGEND   ═══ runtime byte path      ─── control / state path
          (WS) outbound WebSocket long-connection    (R) REST (Feishu open API)
          (P) plaintext HTTP   (G) gRPC plaintext    (K) Kubernetes API

                          ┌─────────────────────────┐   ── PUBLIC INTERNET ──
                          │  FEISHU / LARK open API   │   (the ONLY hop that
                          │  open.feishu.cn  :443     │    leaves the cluster)
                          └────▲───────────────┬──────┘
        (WS) connector dials   │               ║ (WS) push im.message.receive_v1
        OUT, holds it open ────┘               ║ (R)  reply: im.v1.message.create
                                               ▼
        ┌──────────────────────────────────────────────────────────────┐
        │  channel-connector (pod · single replica · maxSurge:0)         │
        │  one lark_oapi ws.Client per account (thread-per-app)          │
        │  :8083 internal API ◄── (P) control-panel POST /internal/       │
        │                            reconcile/{account} (runner-token)   │
        └──┬───────────────┬────────────────────────┬───────────────────┘
    (K) wake │       (G) list_effective /      (P) dial ║ /run/stream
    patch CR │           get_secret /                   ║  (SSE reply relayed
    spec.wake│           set_status                     ║   as assistant text)
             ▼               ▼                          ▼
       ┌──────────┐   ┌────────────┐   ┌────────────────────────────────┐
       │ operator │   │ data-spine │   │  agent-runner  ar-<account>     │
       │ (scaler) │   │  :50051    │   │  :8091  runs the claude CLI     │
       └──────────┘   └────────────┘   └────────────────────────────────┘
```

**How to read it:** everything the connector talks to is **east-west inside the cluster**
(operator via the K8s API, data-spine gRPC, the ar pod HTTP) — **except the Feishu link**,
which is the one and only hop that leaves k8s to the public internet, and it leaves via the
node's **egress** (the connector dials out), never via an ingress.

### Inbound pipeline (Feishu DM → reply)

1. **Arm** (`engine.py`): `ReconcileEngine` polls `feishu_configs.list_effective()` every
   `CONNECTOR_POLL_SECONDS` (10s) and diffs each row's **`desired_digest`** (not `updated_at`,
   which would thrash on status write-back). A new/changed effective account is `_arm`ed:
   fetch the decrypted secret via the privileged **`GetFeishuSecret`** RPC (the connector never
   holds the Fernet key), build an `AppWorker`, start its transport. A best-effort
   `POST /internal/reconcile/{account}` from control-panel collapses the ≤10s latency after an edit.
2. **WS connect (outbound)** (`lark_ws.py`): each app gets a daemon thread running
   `lark.ws.Client(app_id, app_secret, …).start()`, which opens the WSS to `open.feishu.cn`
   (or `open.larksuite.com` when `domain == "lark"`) and blocks. `register_p2_im_message_receive_v1`
   binds the handler.
3. **Event push**: on a DM, Feishu pushes `im.message.receive_v1` down the WS → `_dispatch` (on the
   WS thread) parses text-only (MVP), builds `InboundMessage`, and bridges it onto the asyncio loop
   via `run_coroutine_threadsafe` — the handler returns immediately (honours Feishu's <3s ack).
4. **Handle** (`worker.py`): access gate → `router.decide` (slash-command semantics inherited from
   the SDK: `/clear` `/compact` pass through as the prompt; `/new` detaches the session) → `/new`
   acks a detach, otherwise **wake + dial**.
5. **Wake + dial** (`dial.py` / `wake.py`): `wake_and_wait` patches `AgentTenant.spec.wake.requestedAt`
   (operator stays the sole scaler) and polls the CR status; then
   `POST http://ar-<account>.<ns>.svc:<runner_service_port>/api/sandbox/agent/run/stream` with
   `X-Priva-Runner-Token = mint(account_id, username)`, reads the SSE, and **relays only the
   assistant text** (MVP; `result.session_id` is captured to resume next turn).
6. **Reply (outbound REST)**: `im.v1.message.create` sends the reply back to the chat.

### The connector's own inbound is minimal and internal

The only thing the connector *listens* on is FastAPI/uvicorn `:8083` (`CONNECTOR_HOST=0.0.0.0`),
with three routes: `GET /healthz` (kubelet probes), `GET /metrics` (scrape), and
`POST /internal/reconcile/{account_id}` (control-panel's low-latency push, authenticated by the
**runner-token** — verified *and* the claim's `account_id` must equal the path, so one tenant
can't nudge another). It is exposed by a **ClusterIP** `Service` on `:8083` with **no Ingress and
no gateway route** — reachable only in-cluster. The Feishu messages never touch this port.

### Outbound (egress) — the part that actually needs configuring

"Egress" in the `NetworkPolicy` sense = any traffic **leaving the connector pod**, which spans
both **intra-cluster east-west** and the **one true cluster-exit**. Only the Feishu hop leaves k8s:

| Egress target | Leaves k8s? | App-layer config | NetworkPolicy rule |
|---|---|---|---|
| **Feishu/Lark 443** (WS + REST) | **✅ to public internet** | per-app `domain` → `FEISHU_DOMAIN`/`LARK_DOMAIN`; app_id/secret from data-spine | `ipBlock 0.0.0.0/0 : 443` |
| **data-spine 50051** (gRPC) | ❌ intra-cluster | `PRIVA_DATASPINE__TRANSPORT=grpc`, `PRIVA_DATASPINE__GRPC_DSN=data-spine:50051` | `podSelector app=data-spine : 50051` |
| **ar-`<account>` 8091** (dial) | ❌ intra-cluster | `dial._url` from `kubernetes.namespace_tenants` + `runner_service_port`; `httpx(trust_env=False)`, `connect=10s`, **no read timeout** | `podSelector app=agent-runner : 8091` |
| **kube-apiserver** (wake CR patch) | ❌ intra-cluster (`kubernetes.default.svc`) | `wake.py` `load_incluster_config()`, `CustomObjectsApi` patch | covered by the `0.0.0.0/0:443` rule |
| **DNS 53** | ❌ intra-cluster (CoreDNS) | — | `namespaceSelector {} : UDP/TCP 53` |

Config sources: the connector has **no egress-specific env** — it reuses the shared
`priva_common.config` settings (`dataspine.*`, `kubernetes.namespace_tenants`,
`runner_service_port`, `in_cluster`, `wake_timeout_seconds`) from the `priva-config` ConfigMap,
and `jwt_secret`/`api_key_hmac_secret` from `priva-shared-secret` (mint the runner-token; verify
the push). There is deliberately **no `PRIVA_FERNET_KEY`** — data-spine decrypts and returns the
`app_secret` over gRPC, so the connector's blast radius excludes the master key. RBAC (SA
`priva-channel-connector`) is exactly `agenttenants` get/patch + `agenttenants/status` read —
**never** a pod verb.

**Gotchas worth knowing:**
- Vanilla `NetworkPolicy` can't match FQDNs, so the public hop is opened by **port** (`0.0.0.0/0:443`),
  not by `open.feishu.cn`. A Cilium/Calico **FQDN policy** can tighten it to the two hostnames later.
- On a locked-down cluster the node must have **outbound internet (NAT / egress gateway)** for the
  Feishu WS to connect at all; the four intra-cluster hops don't need it.
- On minikube the CNI (kindnet) **does not enforce** `NetworkPolicy` — these egress rules are
  doc-of-record there; real enforcement needs Calico/Cilium.
- **Single replica, `maxSurge:0`** is a correctness constraint, not a capacity choice: the same
  Feishu app must hold **exactly one** WS (a second connection single-casts and splits events).
  `terminationGracePeriodSeconds:40` lets SIGTERM close every WS before the kill so a rollout
  re-arms without a same-app clash. HA (N>1) needs a Redis lease first.

## Large reads ride `/api/cp-proxy` (the EPP truncates response bodies at ~8KB)

The InferencePool/EPP lane that carries an agent turn is the **only** path agentgateway exposes
for `/api/sandbox/*`, and it has a hard cap on **response** bodies: agentgateway v1.3.0's GIE
`InferenceRouting::build()` routes every response through the EPP ext_proc with
`response_body_mode = FullDuplexStreamed` **hardcoded** (`allow_mode_override=false`). It is not a
tunable buffer and has no upstream fix (byte-identical on `main` and `v1.3.1`), and the EPP cannot
recover the bytes (its own `mode_override=NONE` is ignored). The effect: any GET response over
~8KB is cut mid-body, so `JSON.parse` on the client dies with `Unterminated string`. This bites
the large reads — session transcripts (35–300KB), file previews (≤1MB), and big list/JSON bodies.

The fix is a second **read** lane that never touches the EPP: a generic control-panel
reverse-proxy that rides the `/` catch-all (which carries no ext_proc, so it returns full bodies —
the same lane that already serves the SPA bundles and the `/sandbox/apidocs` schema). `/api/cp-proxy`
is **not** under `/api/sandbox`, so Gateway-API most-specific-prefix routing sends it to the `/`
catch-all → control-panel automatically — no `deploy/gateway` / `HTTPRoute` change.

```
                            ┌─────────┐
                            │ BROWSER │
                            └────┬────┘
        GET /api/sandbox/*       │       GET /api/cp-proxy/{path}
  (+ writes · streams · WS ·     │       (large reads only)
   blob downloads)               ▼
                            agentgateway
              ┌──────────────────┴──────────────────┐
   most-specific prefix:                   "/" catch-all (NO ext_proc):
   /api/sandbox/* → InferencePool          → control-panel :8080
       → EPP ext_proc                          auth bearer → wake pod → mint
       (FullDuplexStreamed, hardcoded)         per-account runner token
       → ✂ truncates > ~8KB                    → (P) httpx GET (trust_env=False)
       ▼                                        → http://<pod>:8091/api/sandbox/{path}
   agent-runner :8091                           ▼
   (body cut at ~8KB)                       agent-runner :8091
                                            (FULL body, returned verbatim)
```

`GET /api/cp-proxy/{path}` re-does the EPP's per-account steering in plain Python (`_proxy_runner_get`
in `control-panel/app.py`): authenticate the user's bearer token (`authenticate_raw_token`; no
account → 401, revoked / non-`active` → 403), `wake_and_wait` their pod (exception or no endpoint →
503), mint a **per-account** runner token, and proxy `GET http://<pod>:8091/api/sandbox/{path}` over
plaintext httpx (upstream failure → 502), returning the full `r.content` verbatim. Security parity
with the EPP: identical auth/wake/mint gating, GET-only, the per-account token means a caller
reaches only their **own** pod, and any `..` in the path is rejected (400) so there is no escape
above `/api/sandbox/`.

**Never rerouted** — these stay on the `/api/sandbox` InferencePool lane and are never truncated:
all **writes** (POST/PUT/DELETE/PATCH); all **Runner streams** (the agent-run WS and run/stream
SSE); **blob downloads** (`.blob()`, streamed); the control-plane reads
(`/api/auth/audit`) and the `/health` readiness poll (must hit the pod directly); and always-tiny
configs. The truncation is a *buffered-body* defect, not a per-frame one, which is why streaming
responses are exempt.

The Web Terminal is not a Runner read or proxy exception. `/api/terminal/ws` has its own
InferencePool; the EPP authenticates and wakes `term-<account>` at upgrade time, then
agentgateway tunnels frames directly to the Go daemon. The exact
`GET /api/terminal/capability` path is the wake-free exception: it stays on Control Panel,
uses a non-retrying control-plane fetch in the Agent UI, and never enters either InferencePool.
It reports `enabled=true` only for an effective `Zero`/`Waking`/`Running` Terminal allocation;
`PendingRunnerRestart`, missing Operator status, disabled policy, and non-active accounts fail
closed. Global policy and per-account CR reads have short in-process TTLs, and the 0% path skips
the Kubernetes lookup entirely, so the 30-second UI poll does not become a data-plane wake loop.

On the client (`web/shared/api/client.js`), `sandboxRead(path)` tries `/api/cp-proxy` + path and
falls back to the direct `/api/sandbox` lane on a 404 (route not deployed) or a network error, so it
degrades gracefully. It shares `sandboxGet(path)`'s signature, so rerouting a >8KB-risk read is a
drop-in `sandboxGet → sandboxRead` swap. **Step 1 (shipped, via a control-panel image rebuild —
the backend is not hotloadable)** rerouted only `web/user/src/api/sessions.js`: the grouped/cwd/
archived session lists plus session transcripts (via `/api/cp-proxy/agent/sessions/{id}/messages`);
the original `/api/session-history/{id}/messages` route is now a thin back-compat **alias** onto the
same helper. **Step 2 (shipped 2026-07-03, frontend-only — hotloadable)** rerouted the rest after a
full audit of both SPAs: files list/preview, hooks catalog/logs/script content, MCP capabilities,
skill-hub file, user overview/audit/analytics, credentials models, the agent-attachments index, and
subagent detail — 13 reads, all in the user SPA (skills reads had already moved with the skills
redesign). Every other `/api/sandbox` read was verified small/bounded or streaming, and the
**admin SPA audited clean** (its only sandbox-lane reads are the tiny `/pty/*` configs; all
`/api/admin/*` reads ride the control-panel face directly). See ADR 0003 for the rollout record.

## Accessing it from a browser (minikube on macOS)

The node IP isn't host-reachable with the docker driver, so port-forward the gateway:

```bash
kubectl -n priva-cloud port-forward svc/priva-gateway 8080:80   # keep running
# then open:  http://127.0.0.1:8080/        (user SPA)
#             http://127.0.0.1:8080/admin/  (admin SPA)
```

A live agent run needs real `ANTHROPIC_*` creds set in the SPA Settings (→ data-spine secret → injected at wake).
