# priva-cloud Helm chart

A full Helm chart for the Priva Cloud control plane, templated from the raw manifests in
`deploy/{k8s,rbac,crds,gateway,dev-storage}`. Defaults reproduce the minikube dev bring-up
(`deploy/minikube/up.sh`) one-for-one.

## What it installs

| Group | Resources |
|-------|-----------|
| CRD | `AgentTenant` (`crds.install`, kept on uninstall) |
| Config | `priva-config` ConfigMap, `priva-shared-secret` Secret (random, preserved across upgrades) |
| Control plane | `data-spine` (Deployment+PVC+Service), `control-panel` (Deployment+Service), `operator` (Deployment), `scheduler` (Deployment+Service — leaderless firing engine, replicas = availability knob) |
| Feishu channel (`channelConnector.enabled`) | `channel-connector` Deployment+Service+RBAC — always-on WS relay, replicas pinned to 1 (no NetworkPolicy — unlike the raw dev manifest; egress governed by VPC policy) |
| RBAC | ServiceAccounts + Roles/Bindings for operator (incl. discovery ClusterRole), control-panel and scheduler |
| Edge (`gateway.enabled`) | `Gateway`, `InferencePool`, `HTTPRoute` (+ `AgentgatewayParameters` when `gateway.serviceType` set) |
| LB front (`ingress.enabled`) | `Ingress` — "/" catch-all to the gateway Service, for an external cloud LB (e.g. Volcengine ALB) |
| Dev storage (`devStorage.enabled`) | `priva-nfs` StatefulSet, `priva-nfs`/`priva-quota` Services, `priva-export` PV+PVC |

Per-account **agent-runner** pods are created by the operator at wake — not by this chart.
The chart only publishes the runner image ref to the operator via the ConfigMap.

## Prerequisites (NOT installed by this chart)

The chart manages only Priva's own resources. The edge depends on three things you install once:

1. **Gateway API CRDs** (v1.5) — `kubectl apply --server-side -f .../gateway-api/.../standard-install.yaml`
2. **Gateway-API-Inference-Extension CRDs** (v1.5) — `kubectl apply -f .../gateway-api-inference-extension/.../manifests.yaml`
3. **agentgateway controller** — the OCI Helm charts `agentgateway-crds` + `agentgateway` (`inferenceExtension.enabled=true`)

The exact commands are printed in the post-install NOTES (and live in `deploy/minikube/up.sh`).
If you only want the control plane, install with `--set gateway.enabled=false` and skip them.

## Install

`values.yaml` **is** the dev (minikube) config — a bare install mirrors `up.sh`.
For prod, layer `values-prod.yaml` on top with `-f`.

```bash
# dev (minikube) — mirrors up.sh: in-cluster NFS storage + edge wiring.
# images already loaded into minikube as priva/<svc>:dev (deploy/minikube/build.sh)
helm install priva deploy/helm/priva-cloud -n priva-cloud --create-namespace
```

```bash
# prod — external RWX CSI (Ceph/NFS), no privileged NFS pod, real registry/tags.
# EDIT the placeholders in values-prod.yaml first (registry, tag, storageClassName),
# then bind an external RWX export to a PVC named "priva-export" (config.kubernetes.exportClaimName).
helm install priva deploy/helm/priva-cloud -n priva-cloud --create-namespace \
  -f deploy/helm/priva-cloud/values-prod.yaml
```

```bash
# UAT — amd64 cluster + CephFS CSI (per-account PVCs, quota = PVC size).
# Full runbook: deploy/uat/README.md (build-push script, prereqs, verification).
helm install priva deploy/helm/priva-cloud -n priva-cloud --create-namespace \
  -f deploy/helm/priva-cloud/values-uat.yaml
```

```bash
# Volcengine VKE — layer the ALB edge overlay on top of the cluster overlay.
# EDIT ingress.host (and className if the SRE's ALBInstance isn't named "alb").
# Prereqs + SRE boundary notes are in values-volcengine.yaml comments.
helm install priva deploy/helm/priva-cloud -n priva-cloud --create-namespace \
  -f deploy/helm/priva-cloud/values-uat.yaml \
  -f deploy/helm/priva-cloud/values-volcengine.yaml
```

You can still `--set key=value` on top for one-off tweaks.

## Key values

| Value | Default | Notes |
|-------|---------|-------|
| `image.registry` / `image.tag` | `""` / `dev` | registry prepended only if set; per-service `tag` overrides |
| `image.pullSecrets` | `[]` | dockerconfigjson Secret names → `imagePullSecrets` on every chart pod; first entry is published to the operator for runner pods |
| `config.kubernetes.cephfsStorageClass` | `""` | cephfs backend: RWX CSI SC for per-account export PVCs (needs `allowVolumeExpansion`); `""` = default SC |
| `namespaceOverride` | `""` | else the release namespace |
| `crds.install` / `crds.keep` | `true` / `true` | templated CRD (upgrades apply schema changes), kept on uninstall |
| `sharedSecret.create` | `true` | random jwt+hmac, preserved across upgrades via `lookup`; set values to pin |
| `config.kubernetes.storageBackend` | `nfs_xfs` | `nfs_xfs` (dev) or `cephfs` (prod) |
| `gateway.enabled` | `true` | the `Gateway`/`HTTPRoute`/`InferencePool` trio |
| `gateway.serviceType` | `""` | override the agentgateway-provisioned Service type (default LoadBalancer); set `ClusterIP`/`NodePort` when an external LB fronts the gateway |
| `ingress.enabled` | `false` | "/" catch-all Ingress to the gateway Service; `className`/`host`/`annotations` are controller-specific (see `values-volcengine.yaml`) |
| `devStorage.enabled` | `true` | **privileged** in-cluster NFS — disable for prod |
| `devStorage.nfs.clusterIP` | `10.96.200.200` | pinned (PV references an IP, not DNS) — keep it free in the service CIDR |

See `values.yaml` for the full set (replicas, resources, idle/wake timings, storage sizes).

## Notes / caveats

- **Secret rotation:** `priva-shared-secret` is annotated `helm.sh/resource-policy: keep` and
  re-read via `lookup` on upgrade, so JWTs/api-key lookups survive `helm upgrade`. `helm template`
  (no cluster) can't `lookup`, so it emits fresh randoms — fine for diffing, not for applying.
- **CRD:** templated (not in Helm's install-only `crds/` dir) so `helm upgrade` re-applies schema
  edits. Kept on uninstall to avoid cascading-deleting live `AgentTenant` CRs.
- **Selectors are verbatim:** pod `app:` labels feed the operator and the InferencePool selector,
  so the chart never templates selector labels — only additive `app.kubernetes.io/*` metadata labels.
- **clusterIP pin:** dev only. In prod the export is a real CSI volume; `devStorage.enabled=false`
  drops the PV/StatefulSet entirely.
