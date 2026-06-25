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
| `dev-storage/` | DEV-ONLY shared-RWX storage backend: in-cluster NFS server + quota-manager + the `priva-export` PV/PVC. |
| `helm/priva-cloud/` | Full Helm chart for the whole control plane (CRD, config/secret, control-plane, RBAC, edge, dev-storage) — `helm install` alternative to the raw `kubectl apply` flow. |
| `minikube/build.sh` | Build the 4 images and load them into minikube (runtime=containerd). |
| `minikube/up.sh` | One-shot bring-up: images → Gateway API + GIE CRDs + agentgateway (Helm) → CRD/RBAC → control-plane → edge. |

## Install via Helm (alternative to `up.sh`)

```bash
deploy/minikube/build.sh                          # build + load images (still needed)
# Gateway API + GIE CRDs + agentgateway controller — see deploy/helm/priva-cloud/README.md
helm install priva deploy/helm/priva-cloud -n priva-cloud --create-namespace
```

The chart mirrors these manifests one-for-one; `deploy/helm/priva-cloud/README.md` covers
prod overrides (`devStorage.enabled=false`, `storageBackend=cephfs`, registry/tags).

## Bring-up (minikube)

```bash
deploy/minikube/up.sh
kubectl -n priva-cloud get pods                 # control-panel/data-spine/operator + priva-gateway Ready
kubectl -n priva-cloud port-forward svc/priva-gateway 8080:80   # then open http://127.0.0.1:8080/
```

The shared secret (`priva-shared-secret`: jwt/hmac) is generated at bring-up and is
**not** committed. Per-account credentials (`ANTHROPIC_*`) are set via the SPA Settings,
stored Fernet-encrypted in data-spine, and injected into each pod by the operator at wake.

`up.sh` also enables the `csi-hostpath-driver` addon (and patches its `csi-hostpath-sc`
StorageClass to `allowVolumeExpansion: true`) so per-account PVCs can grow live. The
default `standard` SC is **not** expandable.

## Per-account runner type, resource specs & self-registration

- **Runner type** (`account.agent_runner_type` ∈ `auto_scale` | `persistent`): `auto_scale`
  is the wake-on-demand / idle-scale-to-zero default; `persistent` pins the pod to 1 replica
  and exempts it from the idle sweep (always-on). Stamped onto `AgentTenant.spec.agentRunnerType`.
- **Resource specs** (`account_resource_spec`: cpu_cores / memory_mb / volume_gb): templated
  into the runner container `resources` (requests==limits) + PVC size. Admin-editable live in
  the admin UI — control-panel patches the CR, the operator applies it: CPU/mem → `Recreate`
  restart, volume → online grow (grow-only). Stamped onto `AgentTenant.spec.{resources,storageGb}`.
- **Self-registration**: public `POST /api/auth/register` stores a `pending_registration` row
  (user-chosen bcrypt password + requested runner type / resources). An admin approves via
  `POST /api/admin/pending-registrations/{id}/approve` (or `/reject`), which creates the account
  from the stored hash + provisions the tenant. Admin routes accept an admin JWT **or** an
  admin's account api-key.

## Request paths

- **Control / SPAs / admin / auth / config** → agentgateway → `control-panel:8080` (plain HTTP).
- **Runtime** (`/api/agent`, `/api/files`, `/api/pty`, `/api/hooks`, `/api/subagents`) →
  agentgateway → `InferencePool` → per-request **ext_proc EPP** (`control-panel:9000`, **TLS**)
  resolves the account + wakes its pod (operator scales 0→1) + returns the pod endpoint →
  agentgateway streams to the woken per-account `agent-runner` pod.

## Deferred (prod hardening)
NetworkPolicies, mTLS/JWKS pod trust (alpha uses an HS256 signed header), per-account
DEK/KMS, edge TLS, Redis-based wake/idle coordination, separate audit PVC. See plan §L.
