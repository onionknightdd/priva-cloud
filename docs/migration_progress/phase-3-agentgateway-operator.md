# Phase 3 (slice) — per-account runner provisioning on minikube: agentgateway edge + operator + EPP

As-built status of the vertical slice (branch `feat/agentgateway-operator-epp`). Plan:
`.claude/plans/explore-the-overall-migration-reactive-cake.md`.

## Done + verified

| # | Increment | State |
|---|-----------|-------|
| 1 | Config: `PRIVA_` nested env override, log paths decoupled from the config file, `KubernetesSettings`/`EdgeSettings`, single `priva-cloud` ns | ✅ committed, unit-tested |
| 2 | data-spine gRPC pod (`serve`) + `build_grpc_client` + converters + Fernet **secret store** (`PutSecret/GetSecret`) | ✅ committed, round-trip test |
| 5 | **operator** (kopf): AgentTenant CRD, reconcile→Deploy(0)/Svc/PVC, wake→secret-inject→scale 0→1, idle sweep→0 | ✅ committed, **verified on cluster** |
| 6 | control-panel **ext_proc EPP** + K8s provisioner + secret-store cred path (deleted `proxy.py`) | ✅ committed, EPP unit-tested + **server verified on cluster** |
| 8 | agent-runner activity tracking (`/health` active_runs/last_activity_ts) | ✅ committed |
| 3 | Dockerfiles (4) + `minikube image build/load`; AR bakes the native `claude` CLI (v2.x) | ✅ all 4 images built + loaded |
| 4 | K8s base: data-spine + control-panel + operator Deployments/Services/RBAC/ConfigMap | ✅ committed, **all 1/1 Running** |
| 7 | agentgateway (v1.3.0) + Gateway API (v1.5.0) + GIE (v1.5.0); Gateway + InferencePool + HTTPRoute | ✅ installed; Gateway Programmed; **edge control path works** |

### End-to-end proven on minikube
- `setup`/`login` and **all control-path traffic route browser → agentgateway → control-panel** (200, JWT issued).
- user-create → account via **CP→data-spine gRPC** → CP **provisions AgentTenant CR** → **operator reconciles** → scale-to-zero `ar-<account>` Deployment + Service + Bound PVC.
- CR **wake** → operator **materializes the 6-key creds Secret** (from the data-spine secret store) → scale 0→1 → **agent-runner pod 1/1 Ready**, `status.podIP` set.

## Open (the last mile)

1. **agentgateway → EPP ext_proc transport** (the one blocker). agentgateway's call to the EPP fails
   `ext_proc ... received corrupt message of type InvalidContentType` (FailClose → 500). Isolated:
   - Our EPP **server is correct** — a normal gRPC client (in-pod `grpc.insecure_channel` → `Process`)
     gets a proper 401 immediate response.
   - agentgateway reaches **control-panel:8080 over HTTP/1.1 fine** (health/login/SPA all 200).
   - The config dump shows agentgateway recognized the h2c hint: `appProtocols: {"9000":"Http2"}`.
   - **Both** ext_proc mechanisms fail identically: the InferencePool `endpointPickerRef` AND the
     documented `AgentgatewayPolicy.traffic.extProc` (so it's not the mechanism).
   **Byte capture + ~13 experiments (2026-06-21):**
   - agentgateway dials the EPP as **clean h2c prior-knowledge** (captured preface `PRI * HTTP/2.0…`) —
     NOT TLS, NOT HTTP/1.1. Transport is correct.
   - Not ServiceAccount/mesh-identity (default-SA EPP also fails); not workload-conflation (dedicated EPP
     deployment also fails); the config dump shows the port correctly classified `appProtocols {9000: Http2}`.
   - **InferencePool `endpointPickerRef` dial is broken** even to a plain TCP relay (always InvalidContentType).
   - **`AgentgatewayPolicy.traffic.extProc` → a separate relay pod → grpc.aio returned a real EPP 401 once**,
     but the identical relay did NOT reproduce it later → the agentgateway ext_proc↔grpc.aio path is
     **flaky/broken on v1.3.0**, not config-fixable from our side.
   ⇒ Root cause is an **agentgateway v1.3.0 ext_proc interop problem** (its h2c gRPC ext_proc client vs a
   C-core `grpc.aio` server, plus a separate InferencePool-EPP-dial bug). Our EPP server is correct
   (normal gRPC clients, in-pod and cross-pod, get proper responses).
   **Real fix paths (need a different approach, not more YAML tweaks):**
   (a) serve the ext_proc EPP from an **agentgateway-proven stack** (a small Go/Rust ext_proc, or the GIE
   reference EPP shape) instead of grpc.aio;
   (b) try a **different agentgateway version** (e.g. ≥ the one validated against GIE v1.5.0 InferencePool v1);
   (c) **file an agentgateway issue** with this repro (clean h2c, grpc.aio EPP, InvalidContentType);
   (d) fall back to the **CP byte-path edge** (Phase-2 reverse-proxy, no agentgateway) for a working E2E now.
2. **Wake-and-hold vs ext_proc timeout.** The EPP currently blocks up to `wake_timeout_seconds` (90s)
   waiting for a cold pod; agentgateway's ext_proc timeout is much shorter. Once (1) is fixed, shorten
   the EPP hold to a few seconds and return a fast 503 "waking, retry" on cold start (predictive wake on
   login keeps pods warm). Pattern is in `control-panel.md §2.2`.
3. **Live agent run** needs real `ANTHROPIC_*` creds (the demo used a placeholder token); routing/auth
   are independent of this.

## Reproduce

`deploy/minikube/up.sh` (build+load images, install CRDs/agentgateway, apply control-plane + edge).
Reach the edge: `kubectl -n priva-cloud port-forward svc/priva-gateway 8080:80`.

## Deferred (per plan §L)
Redis coordination, per-account DEK/KMS, mTLS/JWKS + NetworkPolicies, edge TLS, dual-face config/exec
route split granularity, scheduler + channel-connector, predictive wake, offboard/purge.
