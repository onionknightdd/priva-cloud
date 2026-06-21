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

## RESOLVED — the runtime path works end-to-end (2026-06-21)

**Root cause:** agentgateway dials the InferencePool EndpointPicker over **TLS** (the GIE reference-EPP
convention; captured ClientHello bytes `16 03 01…` on the EPP dial), but our EPP served **plaintext** →
TLS-into-plaintext = `received corrupt message of type InvalidContentType` (the same class as linkerd#13427:
a Rust proxy's mTLS/TLS vs a plaintext target). **NOT** a Python-vs-Go gRPC issue — both grpc.aio and
grpclib failed for the same reason (plaintext).

**Fix:** the EPP (`extproc.py`) now serves **TLS** (self-signed, ALPN `h2`; agentgateway skip-verifies
in-cluster). Verified: `GET /api/agent/sessions` through the edge → **HTTP 200**, gateway log
`inferencepool.selected_endpoint=10.244.0.43:8091` — agentgateway → InferencePool → EPP (TLS) → control-panel
resolves the account, wakes the pod, returns its endpoint → agentgateway routes to the **woken agent-runner
pod**, which trusts the EPP-injected signed runner token. (grpclib stays — a clean pure-Python EPP — but the
TLS change is the actual fix and would have worked on grpc.aio too.)

## Live agent run — WORKS (2026-06-21)

A real turn ran end-to-end through the edge against a local OpenAI/Anthropic-compatible LLM proxy
(`host.minikube.internal:8000`, model `Qwen3.6-...`): BYOK creds set via `PUT /api/auth/me/env` →
data-spine secret store → operator injected them at wake → `POST /api/agent/run` through the gateway →
**HTTP 200**, `result: "hello from priva-cloud"`, `is_error:false`.

**Second crack — the EPP was dropping bodies.** agentgateway sends request/response **body** chunks to the
InferencePool EPP (it ignores `mode_override`; `allow_mode_override` is off), and our empty `BodyResponse`
**dropped** them → POST runs reached the pod with an empty body → 422. Fix: the EPP **echoes** the bytes back
via `body_mutation` for `request_body`/`response_body` (`extproc.py:_passthrough_body`). (A `mode_override`
hint is also sent for gateways that honor it.)

## Remaining
2. ~~Cold-start wake-and-hold vs ext_proc timeout~~ **VALIDATED**: from `replicas=0, phase=Zero`, a runtime
   request through the edge returned **HTTP 200 in 4.1s** — the EPP patched `spec.wake`, the operator scaled
   0→1 + injected the Secret, the pod went Ready, and agentgateway routed to the freshly-woken pod (well
   within the ext_proc deadline; the AR image is node-cached). Scale-from-zero on demand works.
3. The `:9000` Service still carries `appProtocol: kubernetes.io/h2c` — harmless (the InferencePool EPP dial
   is TLS regardless), tidy to `https`/grpc-tls later.

## (historical) the ext_proc transport investigation

1. agentgateway's call to the EPP failed `received corrupt message of type InvalidContentType`. Isolated:
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
