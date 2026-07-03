# Byte path & components (as-built, minikube alpha)

The runtime topology of the per-account-pod slice (agentgateway + operator + EPP).
Detailed status: [`../migration_progress/phase-3-agentgateway-operator.md`](../migration_progress/phase-3-agentgateway-operator.md).
The EPP-over-TLS gotcha: memory `agentgateway-epp-tls`.

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
| **agent-runner** `ar-<account>` | `:8091` HTTP | one scale-to-zero runtime pod per account; spawns the `claude` CLI | (P) from gateway, (G) to data-spine | trusts the EPP-injected HS256 signed `account_id`; creds from the mounted Secret |

## Transport (alpha)

Every hop is **plaintext HTTP / gRPC** *except the one EPP hop*: agentgateway dials the InferencePool
EndpointPicker (`control-panel:9000`) over **TLS** (GIE convention; it skip-verifies in-cluster), so the EPP
serves TLS (self-signed, ALPN `h2`). This is forced by agentgateway, not a choice — a plaintext EPP fails
with `InvalidContentType`. Real HTTPS / mTLS / JWKS / edge-TLS are **deferred** (plan §L).

## WebSocket auth (chat `/api/agent/ws/run`, terminal `/api/pty/ws`)

The edge authenticates a WS on the **upgrade** request, which has no body and no
`Authorization` header. The SPA passes the JWT as a **subprotocol**:
`new WebSocket(url, ['priva.ws.v1', 'priva.token.<jwt>'])`. The EPP reads the
`priva.token.` entry off `Sec-WebSocket-Protocol`; the agent-runner echoes back
only `priva.ws.v1` in `accept()` so the browser handshake completes. This keeps
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
all **writes** (POST/PUT/DELETE/PATCH); all **streams** (the agent-run WS, run/stream SSE, the
terminal pty WS — the EPP is consulted only at WS *setup* for steering, not in the byte path, so
frames tunnel through intact); **blob downloads** (`.blob()`, streamed); the control-plane reads
(`/api/auth/audit`) and the `/health` readiness poll (must hit the pod directly); and always-tiny
configs. The truncation is a *buffered-body* defect, not a per-frame one, which is why streaming
responses are exempt.

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
