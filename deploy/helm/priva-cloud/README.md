# priva-cloud Helm chart

A full Helm chart for the Priva Cloud control plane, templated from the raw manifests in
`deploy/{k8s,rbac,crds,gateway,dev-storage}`. Defaults reproduce the minikube dev bring-up
(`deploy/minikube/up.sh`) one-for-one.

## What it installs

| Group | Resources |
|-------|-----------|
| CRD | `AgentTenant` (`crds.install`, kept on uninstall) |
| Config | `priva-config` ConfigMap, `priva-shared-secret` Secret (random, preserved across upgrades) |
| Control plane | `data-spine` (Deployment+PVC+Service), `control-panel` (Deployment+Service), `operator` (single-replica standalone Deployment), `scheduler` (Deployment+Service — leaderless firing engine, replicas = availability knob) |
| Feishu channel (`channelConnector.enabled`) | `channel-connector` Deployment+Service+RBAC — always-on WS relay, replicas pinned to 1 (no NetworkPolicy — unlike the raw dev manifest; egress governed by VPC policy) |
| RBAC | ServiceAccounts + Roles/Bindings for operator (incl. discovery ClusterRole), control-panel and scheduler |
| Edge (`gateway.enabled`) | `Gateway`, Runner + Terminal `InferencePool`s, `HTTPRoute` (+ `AgentgatewayParameters` when `gateway.serviceType` set) |
| Tenant isolation | Not chart-managed — the operator renders the `NetworkPolicy` set from the admin settings (Sandbox ▸ Isolation) and prunes the superseded static policies. PostgreSQL's own policy stays here (control-plane boundary) |
| LB front (`ingress.enabled`) | `Ingress` — "/" catch-all to the gateway Service, for an external cloud LB (e.g. Volcengine ALB) |
| Dev storage (`devStorage.enabled`) | `priva-nfs` StatefulSet, `priva-nfs`/`priva-quota` Services, `priva-export` PV+PVC |

Per-account **agent-runner** and independent **Terminal** pods are created by the operator,
not by this chart. The same immutable image is used for both; Terminal defaults to disabled.

## Prerequisites (NOT installed by this chart)

The chart manages only Priva's own resources. The deployment depends on:

1. **Gateway API CRDs** (v1.5) — `kubectl apply --server-side -f .../gateway-api/.../standard-install.yaml`
2. **Gateway-API-Inference-Extension CRDs** (v1.5) — `kubectl apply -f .../gateway-api-inference-extension/.../manifests.yaml`
3. **agentgateway controller** — the OCI Helm charts `agentgateway-crds` + `agentgateway` (`inferenceExtension.enabled=true`)
4. A **CNI that enforces NetworkPolicy in both directions** — rendering policies is insufficient if the CNI ignores them. Measure it with `deploy/checks/networkpolicy-cni.sh` and treat every non-zero exit as a production stop; a VPC-native CNI (Volcengine cello, AWS VPC CNI, Terway) delegates policy to a separate engine that may not be installed.
5. kubelet **`podPidsLimit` in `1..512` on every node** — verify with `deploy/checks/pod-pids-limit.sh`.

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
# EDIT the placeholders in values-prod.yaml first (registry, tag, storageClassName,
# cluster CIDRs, DNS addresses and the digest-pinned egress proxy image),
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
| `config.dataspine.networkIsolationRpcTimeoutSeconds` | `5` | Bounds the Operator's isolation read so a black-holed data-spine falls back to the verified on-cluster snapshot instead of blocking forever |
| `config.kubernetes.storageBackend` | `nfs_xfs` | `nfs_xfs` (dev) or `cephfs` (prod) |
| `config.kubernetes.terminalResourcePercent` | `0` | Upgrade-safe seed: `0` disables/hides Terminal; Admin may set fixed 5% steps through 50% |
| `config.kubernetes.clusterPodCidrs` / `clusterServiceCidrs` / `clusterNodeCidrs` | minikube ranges | Replace with every real cluster and node/VPC range; prod/UAT overlays intentionally set empty, which the services reject until edited |
| `config.kubernetes.egressBlockedCidrs` | private + CGNAT + link-local + reserved | Proxy and public-egress deny set; extend with provider metadata/control-plane ranges |
| `config.kubernetes.dnsIpCidrs` | `10.96.0.10/32` | kube-dns Service IP plus NodeLocal/custom listeners; replace it with the cluster's real resolver addresses |
| `config.kubernetes.networkPolicyProbeRequired` | `true` | Blocks tenant create/wake unless the ingress+egress functional probe recorded success |
| `config.kubernetes.egressProxyImage` | `ubuntu/squid:latest` | Development only; prod/UAT overlays use a non-pullable placeholder until replaced by a reviewed immutable digest |
| `config.kubernetes.egressNoProxy` | localhost only | Internal clients opt out explicitly; do not add DNS bypasses or broaden to `.svc` / `.cluster.local` |
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
- **Service-identity rotation is staged, never a one-step replacement.** First add the
  future public key to `sharedSecret.serviceIdentityAdditionalPublicKeys` while the old
  keypair remains current, deploy, and wait until all Runner/Terminal templates have
  converged. Then switch the current private/public pair and keep the old public key in
  the additional list. Wait again until every dormant template and active runtime has
  converged to the new current-key generation (this re-mints each Runner's permanent,
  account-scoped service token), and retain the overlap for at least the longest
  scheduled run plus control-plane service-token TTL. Remove the old key only after no
  old runtime or in-flight token remains. The per-Pod
  drain capability lets the new Operator close admission on a Pod which still trusts the
  old signer, but it does not replace this verifier-overlap window for FinishRun.
- **CRD:** templated (not in Helm's install-only `crds/` dir) so `helm upgrade` re-applies schema
  edits. Kept on uninstall to avoid cascading-deleting live `AgentTenant` CRs.
- **Operator replicas:** exactly `1` is enforced at render time. The operator runs Kopf
  standalone without cross-pod leader election, and its `Recreate` strategy prevents two
  policy reconcilers from applying different isolation snapshots concurrently.
- **Isolation defaults:** a fresh data-spine starts with Runner/Terminal internal
  isolation and tenant-peer isolation enabled, plus the shipped minimal public-domain
  allowlist. Existing `network_isolation` rows are preserved as administrator intent;
  review and explicitly tighten an older row before promoting an upgraded cluster.
- **Selectors are verbatim:** pod `app:` labels feed the operator and the InferencePool selector,
  so the chart never templates selector labels — only additive `app.kubernetes.io/*` metadata labels.
- **clusterIP pin:** dev only. In prod the export is a real CSI volume; `devStorage.enabled=false`
  drops the PV/StatefulSet entirely.
- **Terminal resource accounting is fixed, not borrowed dynamically.** When enabled,
  Runner + Terminal CPU/memory requests and limits sum to the tenant commitment. A policy
  generation is not partially applied while its sibling runtime is live.
- **NetworkPolicy is not a node firewall.** Standard policy does not portably block a
  tenant pod from the node hosting it, and `hostNetwork` behavior is CNI-specific. Pair
  this chart with CNI host policy/node firewall rules, admission controls that prohibit
  tenant `hostNetwork`/privileged pods, and cloud-side metadata restrictions/workload
  identity. The egress proxy's address deny list is defense in depth, not a substitute.
- **Tenant public egress is IPv4-only in this release.** IPv6 CIDR configuration
  is rejected, and probe contract v3 refuses to record success when the probe
  pods have IPv6 addresses. Squid's `dst ::/0` ACL is only defense in depth:
  mixed A/AAAA resolution can choose a different address at forwarding time, so
  it is not treated as proof that a dual-stack cluster is closed.
- **Probe both directions before tenants exist.** Run
  `PRIVA_FACTS_NS=<release-namespace> deploy/checks/networkpolicy-cni.sh`; exit 0 means
  both ingress and egress denies worked. Exit 1 (not enforced) and 2 (undetermined) are
  both fail-closed results for UAT/production. A re-test first revokes the old
  success; the result is cluster-bound and expires after
  `networkPolicyProbeMaxAgeSeconds`. Repeat at least before that TTL and after
  every CNI/network change.
- **Static baselines remain when the operator is unavailable.** Helm owns empty
  ingress+egress policies for Runner, Terminal and the proxy; dynamic policies
  add only the current allows. The operator also blocks on any extra policy
  whose Kubernetes union could widen those workloads and reports its name.
- **CONNECT SNI is not inspected by the dev image.** The verified Ubuntu Squid
  6.13 image is a GnuTLS build without `ssl-bump`; it enforces CONNECT authority,
  port, strict Host consistency and resolved destination IP, but cannot compare
  encrypted TLS SNI. If SNI equality is a hard requirement, use a reviewed
  OpenSSL Squid build with tested peek/splice/terminate rules or a dedicated
  Envoy egress gateway; pinning a different image alone does not enable it.
- **DNS is a residual covert channel.** Runner/Terminal must reach the cluster
  resolver for internal service discovery, and standard NetworkPolicy cannot
  constrain query names. High-assurance deployments need a tenant resolver that
  serves only approved/internal zones or a CNI/L7 DNS policy; `deny_all` alone
  does not prevent data encoded in recursive DNS queries.
- **Private/LAN model gateways are not currently supported by tenant BYOK.** The
  proxy rejects private, node and metadata addresses before every domain allow.
  Supporting an internal model relay requires a separate administrator-approved
  upstream class; never infer that permission from a tenant-supplied base URL.
