# deploy/

Container images + Kubernetes manifests for running Priva Cloud on a cluster
(validated on minikube). See `docs/migration_progress/phase-3-agentgateway-operator.md`
for the as-built design and the agentgateway-EPP-over-TLS gotcha.

> **First time building or deploying? Read `docs/READ_BEFORE_BUILD.md` first** — host
> toolchain, the build-SPAs-before-the-image rule, secrets you must provide, and the
> dev/UAT pre-flight gotchas.

## Layout

| Path | What |
|------|------|
| `docker/` | Service Dockerfiles. The agent-runner image also contains the Go `terminald` binary and native `claude` CLI. |
| `checks/` | Fail-closed deployment and CI checks, including CNI enforcement, kubelet PID limits, and real Squid ACL integration. |
| `config/` | Slim per-service example configs (everything is also settable as `PRIVA_*` env). |
| `crds/agenttenant.yaml` | The `AgentTenant` CRD (one record per account). |
| `rbac/` | ServiceAccounts + Roles for the operator and control-panel. |
| `k8s/` | ConfigMap + Deployments/Services for data-spine, control-panel, operator. |
| `gateway/` | agentgateway `Gateway`, `InferencePool` (EPP = control-panel:9000), `HTTPRoute`. |
| `dev-storage/` | DEV-ONLY shared-RWX storage backend: in-cluster NFS server + quota-manager + the `priva-export` PV/PVC. |
| `helm/priva-cloud/` | Full Helm chart for the whole control plane (CRD, config/secret, control-plane, RBAC, edge, dev-storage) — `helm install` alternative to the raw `kubectl apply` flow. |
| `minikube/build.sh` | Build the 4 images and load them into minikube (runtime=containerd). |
| `minikube/up.sh` | One-shot bring-up: images → Gateway API + GIE CRDs + agentgateway (Helm) → CRD/RBAC → control-plane → edge. |
| `uat/` | UAT deployment: `build-push.sh` (buildx linux/amd64 → registry) + runbook for the Helm install on a real cluster (cephfs storage backend). |

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

Tenant network isolation is no longer applied by this script. The operator renders
the NetworkPolicy set from the admin settings (Sandbox ▸ Isolation) and prunes the
superseded static policies, so the switches are runtime-configurable and their live
enforcement state is visible in the UI.

A fresh data-spine seeds a functional secure posture: both tenant workload classes
deny undeclared internal access, tenant peers are isolated, and public egress uses
the shipped minimal domain allowlist. Upgrades preserve an existing administrator
row; review and explicitly tighten legacy `unrestricted` settings before release.

Bring-up runs `deploy/checks/networkpolicy-cni.sh`, which **measures** ingress and
egress enforcement with independent client/server pairs rather than matching the
plugin's name. The script returns non-zero unless both directions are enforced and
records the aggregate plus both directional results in
`priva-cluster-facts`. `minikube/up.sh` and UAT/production gates treat exit 1 or
2 as fatal before creating any tenant. The kubelet PID limit preflight remains
mandatory.

If it reports NOT ENFORCED, treat every Isolation switch as inert: the fixed
agentgateway-to-Terminal trust assertion is then not a cross-tenant boundary, because
that header is terminald's *only* authentication.

## Tenant egress configuration

The operator keeps the Runner/Terminal egress policy and proxy path present across
isolation-mode changes. The deployment must supply the real cluster topology through
`clusterPodCidrs`, `clusterServiceCidrs`, `clusterNodeCidrs`, and (for NodeLocal or
custom DNS) `dnsIpCidrs`. The proxy's own deny set comes from
`egressBlockedCidrs`; it must include every provider metadata/control-plane range in
addition to the private, link-local, CGNAT, multicast, and reserved defaults. Helm
serializes these lists as JSON environment values. Production must also mirror and pin
`egressProxyImage` by immutable digest.

The verified Ubuntu Squid 6.13 development image is built with GnuTLS and cannot
enable `ssl-bump`. The proxy validates CONNECT authority/port, strict Host consistency
and resolved destination IP, but cannot compare the encrypted TLS SNI. Environments
that require authority=SNI must use a reviewed OpenSSL Squid build with tested
peek/splice/terminate rules or a dedicated Envoy egress gateway.

Run the real Squid integration check whenever the renderer, image, or ACL ordering
changes:

```bash
deploy/checks/squid-egress-integration.sh
```

The check requires `uv` and a working Docker daemon. It renders all three egress
modes, executes `squid -k parse` for each with the pinned multi-architecture Squid
6.13 image, then verifies an allowlisted CONNECT succeeds while a non-allowlisted
CONNECT, both manager interfaces, an IPv6 authority, and `deny_all` are rejected.
It also checks that IPv6-looking query text is not mistaken for an IPv6 authority.
The runtime proxy uses the Pod's non-root, read-only, capability-free security
posture. Missing Docker is a failure, not a skipped check. A CI mirror can set
`PRIVA_SQUID_CHECK_IMAGE`, but the image must still report Squid 6.13.

The operator runs two proxy replicas and prefers placing them on different
nodes; production therefore needs at least two schedulable nodes. Steady-state
convergence accepts one healthy replica so an ordinary pod/node loss does not
quiesce every tenant. Policy changes deliberately use a fail-closed `Recreate`
rollout, so a bad replacement can still cause a short global egress outage
rather than leave an older, wider policy serving traffic. Runner-side silence
timeouts turn that common-mode failure into an explicit stream error. Monitor
proxy readiness and capacity as a platform-wide dependency.

This release supports IPv4-only tenant public egress. Probe contract v3 refuses
to record success when probe pods have IPv6 addresses; Squid's IPv6 destination
ACL is defense in depth and is not accepted as a dual-stack enforcement proof.
It invalidates an earlier success before every re-test, binds the result to the
cluster UID/address family, and expires after the configured maximum age. Run it
again at least weekly (with the default TTL) and after every network change;
expiry blocks new wakes and quiesces running tenant workloads.

Helm installs independent empty NetworkPolicy baselines for Runner, Terminal and
the egress proxy. The operator adds the current allow rules and rejects any
additional policy whose union could widen those pod classes; resolve the named
conflict rather than deleting unknown resources automatically.

Kubernetes `NetworkPolicy` is not a host firewall. The standard does not provide a
portable guarantee for traffic to the node hosting the pod, and `hostNetwork` handling
is CNI-specific. Consequently the policy/proxy pair is only one layer: production
must also deny tenant-to-node traffic with the CNI's host policy or node firewall,
disable/restrict cloud instance metadata (prefer workload identity), and prevent
tenant workloads from using `hostNetwork`, `hostPID`, privileged containers, or
untrusted CNI capabilities. Re-run the ingress+egress probe after every CNI upgrade.

DNS to the cluster resolver remains necessary for internal service discovery and
can carry a covert exfiltration channel because standard NetworkPolicy cannot
filter query names. High-assurance deployments must add a cluster-only tenant
resolver or CNI/L7 DNS policy. Private/LAN BYOK model endpoints are also rejected
by the proxy; they require a separately modelled administrator-approved upstream,
not a tenant-controlled `ANTHROPIC_BASE_URL`.

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
- **Runner runtime** (`/api/sandbox/*`) →
  agentgateway → `InferencePool` → per-request **ext_proc EPP** (`control-panel:9000`, **TLS**)
  resolves the account + wakes its pod (operator scales 0→1) + returns the pod endpoint →
  agentgateway streams to the woken per-account `agent-runner` pod.
- **Web Terminal** (`/api/terminal/ws`) → agentgateway → Terminal `InferencePool` → the
  same EPP authenticates and wakes `term-<account>` → agentgateway carries WebSocket bytes
  directly to the independent Terminal Pod. `/api/terminal/capability` stays on Control Panel.

## Deferred (prod hardening)
mTLS/JWKS pod trust (alpha uses an EPP-overwritten internal Terminal assertion and an
HS256 Runner header), per-account
DEK/KMS, edge TLS, Redis-based wake/idle coordination, separate audit PVC. See plan §L.
Scaling control-panel: bump `replicas` only — never `uvicorn --workers` (the same process
binds the `:9000` EPP) — and move its control-plane audit (per-pod local JSONL under
`priva_home()`) into data-spine first, or replicas fork the audit history.
