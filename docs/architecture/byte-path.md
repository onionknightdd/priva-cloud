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
| **control-panel** | `:8080` HTTP, `:9000` ext_proc | SPAs + auth + admin + config **and** the EPP brain + provisioner | (P) `:8080`, **(T) `:9000`** | EPP resolves account → wakes pod → returns endpoint + signed runner token |
| **operator** (kopf) | — | `AgentTenant` CRD reconcile; **sole scaler 0↔1**; injects per-pod creds Secret at wake | (K) watch/patch | idle sweep scales back to 0 |
| **data-spine** | `:50051` gRPC | accounts / quota / **secrets (Fernet)** + SQLite (RWO PVC) | (G) plaintext | single writer (`replicas:1`, `Recreate`) |
| **agent-runner** `ar-<account>` | `:8091` HTTP | one scale-to-zero runtime pod per account; spawns the `claude` CLI | (P) from gateway, (G) to data-spine | trusts the EPP-injected HS256 signed `account_id`; creds from the mounted Secret |

## Transport (alpha)

Every hop is **plaintext HTTP / gRPC** *except the one EPP hop*: agentgateway dials the InferencePool
EndpointPicker (`control-panel:9000`) over **TLS** (GIE convention; it skip-verifies in-cluster), so the EPP
serves TLS (self-signed, ALPN `h2`). This is forced by agentgateway, not a choice — a plaintext EPP fails
with `InvalidContentType`. Real HTTPS / mTLS / JWKS / edge-TLS are **deferred** (plan §L).

## Runtime request walkthrough

1. Browser → agentgateway (`:80`).
2. Path `/api/agent/...` matches the runtime `HTTPRoute` → `InferencePool agent-runners`.
3. agentgateway calls the EPP (`control-panel:9000`, **TLS**) per request: control-panel resolves the
   account from the JWT, ensures the pod is awake (patches `AgentTenant.spec.wake` → operator scales 0→1 +
   injects the creds Secret), and returns `x-gateway-destination-endpoint = <pod>:8091` + a signed runner token.
4. agentgateway streams the request straight to the woken `agent-runner` pod, which trusts the token and runs.
5. Idle past grace → the operator scales the pod back to 0. (Cold start re-wakes in ~4s; warm is instant.)

## Accessing it from a browser (minikube on macOS)

The node IP isn't host-reachable with the docker driver, so port-forward the gateway:

```bash
kubectl -n priva-cloud port-forward svc/priva-gateway 8080:80   # keep running
# then open:  http://127.0.0.1:8080/        (user SPA)
#             http://127.0.0.1:8080/admin/  (admin SPA)
```

A live agent run needs real `ANTHROPIC_*` creds set in the SPA Settings (→ data-spine secret → injected at wake).
