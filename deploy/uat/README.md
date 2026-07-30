# UAT deployment runbook

Deploy Priva Cloud to a real (non-minikube) amd64 cluster from one Helm chart, with
storage on the cluster's Ceph. Everything here is run by the UAT operator — no access
to the dev machine is needed once images are pushed.

## 0. Prerequisites (cluster)

| Requirement | Why | Check |
|---|---|---|
| Kubernetes ≥ 1.29, amd64 nodes | Gateway API v1.5 + built image arch | `kubectl get nodes -o wide` |
| CephFS CSI StorageClass, **RWX-capable**, `allowVolumeExpansion: true` | per-account export PVCs; PVC expand = quota grow | `kubectl get sc <name> -o yaml` |
| An RWO StorageClass (ceph-rbd or default) | data-spine SQLite PVC | `kubectl get sc` |
| NetworkPolicy ingress **and** egress enforcement | tenant and proxy boundaries | `PRIVA_FACTS_NS=priva-cloud deploy/checks/networkpolicy-cni.sh` (must exit 0) |
| Egress to `api.anthropic.com` (or your `ANTHROPIC_BASE_URL` relay) through the approved proxy path | runners call the Claude API | validate after install from a policy-selected test workload |
| Egress to `cr.agentgateway.dev` + GitHub releases (install-time only) | edge prerequisites below | — |

## 1. Build + push images (from the repo, any machine with docker buildx)

```bash
cd web && npm run build:user && npm run build:admin && cd ..
deploy/uat/build-push.sh <registry> <tag>       # e.g. registry.example.com v0.2.0
```

Builds `control-panel`, `agent-runner`, `data-spine`, `operator` for **linux/amd64** and
pushes `<registry>/priva/<name>:<tag>`. The agent-runner image needs no Node stage — the
`claude` CLI ships inside the claude-agent-sdk wheel (≥0.1.8; pinned 0.2.110) and the
amd64 build pulls the manylinux x86_64 wheel automatically.

## 2. Edge prerequisites (once per cluster)

```bash
kubectl apply --server-side -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.5.0/standard-install.yaml
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/v1.5.0/manifests.yaml
helm upgrade -i --create-namespace -n agentgateway-system --version v1.3.0 \
  agentgateway-crds oci://cr.agentgateway.dev/charts/agentgateway-crds
helm upgrade -i -n agentgateway-system agentgateway oci://cr.agentgateway.dev/charts/agentgateway \
  --version v1.3.0 --set inferenceExtension.enabled=true
```

## 3. Install

```bash
kubectl create ns priva-cloud
# only if the registry is private:
kubectl -n priva-cloud create secret docker-registry priva-regcred \
  --docker-server=<registry> --docker-username=... --docker-password=...

# EDIT deploy/helm/priva-cloud/values-uat.yaml first: registry, tag, pullSecrets,
# cephfsStorageClass, dataSpine.storageClassName, every cluster/node CIDR,
# NodeLocal DNS (if used), and the digest-pinned egress proxy image.
PRIVA_FACTS_NS=priva-cloud deploy/checks/networkpolicy-cni.sh
helm install priva deploy/helm/priva-cloud -n priva-cloud \
  -f deploy/helm/priva-cloud/values-uat.yaml
```

The `priva-shared-secret` (JWT + HMAC) is auto-generated on first install and preserved
across `helm upgrade`.

## 4. Verify

```bash
kubectl -n priva-cloud get pods                      # control-panel/data-spine/operator Ready
kubectl -n priva-cloud wait --for=condition=Programmed gateway/priva-gateway --timeout=120s
kubectl -n priva-cloud port-forward svc/priva-gateway 8080:80   # or use the LB address
# open http://127.0.0.1:8080/ → first visit shows the setup wizard (creates the admin
# account + provisions its runner). Watch the runner come up:
kubectl -n priva-cloud get agenttenants,pvc,pods -l app=agent-runner
```

A healthy first account shows: an `AgentTenant` CR, a PVC `ar-<account>-export` bound on
the CephFS SC, and an `ar-<account>` pod that goes Ready. Per-account Anthropic
credentials are entered in the SPA Settings after login.

## 5. Storage model on UAT (cephfs backend)

- One **RWX PVC per account** (`ar-<id>-export`) = one CephFS subvolume; its size IS the
  hard quota; the runner mounts the claim root at `/workspace` (no subPath), so tenants
  can't see siblings by construction.
- **Quota grow** (admin resource editor) = PVC expand — hence `allowVolumeExpansion`.
  Shrink is rejected, same as dev.
- Account deletion keeps the PVC (data retention, mirroring dev's loop images). Delete
  `pvc/ar-<id>-export` manually to reclaim space.
- Per-account disk *usage* isn't reported on this backend yet (shows as unavailable in
  the admin UI); quota itself is still hard-enforced by the subvolume size.

## Known gaps / cautions for UAT

- **Edge TLS**: the Gateway listens on plain :80. Put your LB/ingress TLS in front, or
  accept HTTP for internal UAT.
- **Web terminal prerequisites**: use a CNI that enforces Kubernetes NetworkPolicy and
  set kubelet `podPidsLimit` to `1..512` on every node (verify with
  `deploy/checks/pod-pids-limit.sh`). Terminal is disabled by default (`0%`); enable it
  from Admin only after both checks pass.
- **Node and metadata boundary**: standard NetworkPolicy does not portably block access
  to the node hosting a pod and `hostNetwork` behavior varies by CNI. Enforce
  tenant-to-node denies in CNI host policy/node firewalls, prohibit tenant
  `hostNetwork`/privileged workloads at admission, and disable/restrict instance
  metadata (prefer workload identity). Add every provider-specific address to
  `egressBlockedCidrs`.
- **TLS SNI boundary**: the Ubuntu Squid 6.13 development image cannot use
  `ssl-bump` (GnuTLS build). It validates CONNECT authority/port and resolved
  destination IP, but not the encrypted ClientHello SNI. A requirement for
  authority=SNI needs a pinned OpenSSL Squid build with tested bump rules or a
  dedicated Envoy egress gateway.
- The chart intentionally does NOT install Gateway API CRDs / agentgateway (step 2) —
  they're cluster-level and shared.
