# deploy/

Container images + Kubernetes manifests for running Priva Cloud on a cluster
(validated on minikube). See `docs/migration_progress/phase-3-agentgateway-operator.md`
for the as-built design and the agentgateway-EPP-over-TLS gotcha.

## Layout

| Path | What |
|------|------|
| `docker/` | Dockerfiles for the 4 services (`data-spine`, `agent-runner`, `control-panel`, `operator`). The agent-runner image bakes the native `claude` CLI. |
| `config/` | Slim per-service example configs (everything is also settable as `PRIVA_*` env). |
| `crds/agenttenant.yaml` | The `AgentTenant` CRD (one record per account). |
| `rbac/` | ServiceAccounts + Roles for the operator and control-panel. |
| `k8s/` | ConfigMap + Deployments/Services for data-spine, control-panel, operator. |
| `gateway/` | agentgateway `Gateway`, `InferencePool` (EPP = control-panel:9000), `HTTPRoute`. |
| `minikube/build.sh` | Build the 4 images and load them into minikube (runtime=containerd). |
| `minikube/up.sh` | One-shot bring-up: images → Gateway API + GIE CRDs + agentgateway (Helm) → CRD/RBAC → control-plane → edge. |

## Bring-up (minikube)

```bash
deploy/minikube/up.sh
kubectl -n priva-cloud get pods                 # control-panel/data-spine/operator + priva-gateway Ready
kubectl -n priva-cloud port-forward svc/priva-gateway 8080:80   # then open http://127.0.0.1:8080/
```

The shared secret (`priva-shared-secret`: jwt/hmac) is generated at bring-up and is
**not** committed. Per-account credentials (`ANTHROPIC_*`) are set via the SPA Settings,
stored Fernet-encrypted in data-spine, and injected into each pod by the operator at wake.

## Request paths

- **Control / SPAs / admin / auth / config** → agentgateway → `control-panel:8080` (plain HTTP).
- **Runtime** (`/api/agent`, `/api/files`, `/api/pty`, `/api/hooks`, `/api/subagents`) →
  agentgateway → `InferencePool` → per-request **ext_proc EPP** (`control-panel:9000`, **TLS**)
  resolves the account + wakes its pod (operator scales 0→1) + returns the pod endpoint →
  agentgateway streams to the woken per-account `agent-runner` pod.

## Deferred (prod hardening)
NetworkPolicies, mTLS/JWKS pod trust (alpha uses an HS256 signed header), per-account
DEK/KMS, edge TLS, Redis-based wake/idle coordination, separate audit PVC. See plan §L.
