#!/usr/bin/env bash
# Reproducible minikube bring-up for the agentgateway/operator/EPP slice.
# Prereqs: minikube running (driver=docker), kubectl, helm, docker. Run from repo root.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
NS=priva-cloud

echo "==> 0. tenant isolation preflight"
bash "$ROOT/deploy/checks/pod-pids-limit.sh"
# The operator applies the same fail-closed gate at startup. Continuing here
# would only build everything and then strand the operator in CrashLoopBackOff,
# while making the warning look like an accepted insecure deployment mode.
set +e
bash "$ROOT/deploy/checks/networkpolicy-cni.sh"
NP_STATUS=$?
set -e
case "$NP_STATUS" in
  0) ;;
  1) echo "    ERROR: this cluster does NOT enforce NetworkPolicy in both directions." >&2
     echo "           Refusing deployment because tenant workloads would be unsafe." >&2
     exit 1 ;;
  *) echo "    ERROR: NetworkPolicy enforcement could not be determined (see above)." >&2
     echo "           Refusing deployment until the probe can complete." >&2
     exit 2 ;;
esac

echo "==> 1. build + load images"
"$ROOT/deploy/minikube/build.sh"

echo "==> 1b. CSI hostpath driver (expandable StorageClass 'csi-hostpath-sc' for PVC grow)"
# The default 'standard' SC (k8s.io/minikube-hostpath) does NOT allow volume expansion;
# the csi-hostpath-driver addon installs the hostpath.csi.k8s.io provisioner + a
# 'csi-hostpath-sc' SC. Some minikube versions ship that SC with expansion DISABLED,
# so patch it on (allowVolumeExpansion is mutable). Idempotent.
minikube addons enable volumesnapshots
minikube addons enable csi-hostpath-driver
for i in 1 2 3 4 5 6 7 8; do
  kubectl get sc csi-hostpath-sc >/dev/null 2>&1 && break; sleep 2
done
kubectl patch sc csi-hostpath-sc -p '{"allowVolumeExpansion":true}' >/dev/null 2>&1 || true
kubectl get sc csi-hostpath-sc -o jsonpath='{.allowVolumeExpansion}' | grep -q true \
  && echo "    csi-hostpath-sc expandable: ok" \
  || echo "    WARN: csi-hostpath-sc missing/not expandable — volume grow edits will fail"

echo "==> 2. namespace"
kubectl get ns "$NS" >/dev/null 2>&1 || kubectl create ns "$NS"

echo "==> 2b. dev storage: in-cluster NFS + XFS project quota (the shared RWX export)"
# DEV-ONLY: one NFS server on a loopback XFS export (prjquota) + the quota-manager API.
# Every runner subPaths into the 'priva-export' RWX PVC; the per-account quota is the XFS
# project quota (set by the operator via the quota-manager). Prod swaps this for Ceph/NFS.
"$ROOT/deploy/minikube/build.sh" nfs-xfs
kubectl apply -f deploy/dev-storage/nfs-xfs.yaml
kubectl -n "$NS" rollout restart statefulset/priva-nfs
kubectl -n "$NS" rollout status statefulset/priva-nfs --timeout=180s
kubectl apply -f deploy/dev-storage/export-pv.yaml

echo "==> 3. Gateway API + GIE CRDs"
kubectl apply --server-side -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.5.0/standard-install.yaml
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/v1.5.0/manifests.yaml

echo "==> 4. agentgateway (Helm OCI, inference extension enabled)"
helm upgrade -i --create-namespace --namespace agentgateway-system --version v1.3.0 \
  agentgateway-crds oci://cr.agentgateway.dev/charts/agentgateway-crds
helm upgrade -i -n agentgateway-system agentgateway oci://cr.agentgateway.dev/charts/agentgateway \
  --version v1.3.0 --set inferenceExtension.enabled=true
kubectl -n agentgateway-system rollout status deploy/agentgateway --timeout=180s

echo "==> 5. AgentTenant CRD + RBAC"
kubectl apply -f deploy/crds/agenttenant.yaml
kubectl apply -f deploy/rbac/operator-rbac.yaml -f deploy/rbac/control-panel-rbac.yaml -f deploy/rbac/channel-connector-rbac.yaml

echo "==> 6. config + per-scope secrets (generated; not committed)"
kubectl apply -f deploy/k8s/configmap.yaml
# Three Secrets, one per trust scope — a single shared Secret envFrom'd by every
# service is what let platform signing material reach tenant pods. Each is
# created once and preserved on re-run (rotating them invalidates sessions, the
# api_key_lookup index, or stored ciphertext respectively).
#
#   priva-shared-secret       signing key  -> control-panel/operator/scheduler/connector
#   priva-control-panel-secret login JWT   -> control-panel only
#   priva-data-spine-secret   hmac+fernet  -> data-spine only
# Capture pre-split values BEFORE the shared Secret is recreated below —
# regenerating the HMAC secret orphans every api_key_lookup row, and
# regenerating the JWT secret logs everyone out.
PRIOR_HMAC="$(kubectl -n "$NS" get secret priva-shared-secret \
  -o jsonpath='{.data.PRIVA_DATASPINE__API_KEY_HMAC_SECRET}' 2>/dev/null | base64 -d 2>/dev/null || true)"
PRIOR_JWT="$(kubectl -n "$NS" get secret priva-shared-secret \
  -o jsonpath='{.data.PRIVA_AUTH__JWT_SECRET}' 2>/dev/null | base64 -d 2>/dev/null || true)"
# The pre-split Secret also held the Fernet key. Losing it makes every stored
# api_key and Feishu app_secret permanently undecryptable, silently: decrypt_value
# returns None and the values just vanish from the UI.
PRIOR_FERNET="$(kubectl -n "$NS" get secret priva-shared-secret \
  -o jsonpath='{.data.PRIVA_FERNET_KEY}' 2>/dev/null | base64 -d 2>/dev/null || true)"
# Post-migration these live in the split Secrets; read them there too so a
# re-run after someone deletes one Secret cannot mint fresh values.
[[ -n "$PRIOR_JWT" ]] || PRIOR_JWT="$(kubectl -n "$NS" get secret priva-control-panel-secret \
  -o jsonpath='{.data.PRIVA_AUTH__JWT_SECRET}' 2>/dev/null | base64 -d 2>/dev/null || true)"
[[ -n "$PRIOR_HMAC" ]] || PRIOR_HMAC="$(kubectl -n "$NS" get secret priva-data-spine-secret \
  -o jsonpath='{.data.PRIVA_DATASPINE__API_KEY_HMAC_SECRET}' 2>/dev/null | base64 -d 2>/dev/null || true)"
[[ -n "$PRIOR_FERNET" ]] || PRIOR_FERNET="$(kubectl -n "$NS" get secret priva-data-spine-secret \
  -o jsonpath='{.data.PRIVA_FERNET_KEY}' 2>/dev/null | base64 -d 2>/dev/null || true)"

# The signing keypair. data-spine VERIFIES service tokens but never mints one,
# so it needs the public half — without it the process falls back to an ephemeral
# keypair, rejects every caller, and the TCP readiness probe still says healthy.
# Both halves are generated together and reused together.
IDENTITY_KEY="$(kubectl -n "$NS" get secret priva-shared-secret \
  -o jsonpath='{.data.PRIVA_SERVICE_IDENTITY__PRIVATE_KEY}' 2>/dev/null | base64 -d 2>/dev/null || true)"
IDENTITY_PUB="$(kubectl -n "$NS" get secret priva-shared-secret \
  -o jsonpath='{.data.PRIVA_SERVICE_IDENTITY__PUBLIC_KEY}' 2>/dev/null | base64 -d 2>/dev/null || true)"
if [[ -z "$IDENTITY_KEY" || -z "$IDENTITY_PUB" ]]; then
  echo "    generating a service-identity keypair"
  _kf="$(mktemp)"; trap 'rm -f "$_kf"' EXIT
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$_kf" 2>/dev/null
  IDENTITY_KEY="$(cat "$_kf")"
  IDENTITY_PUB="$(openssl pkey -in "$_kf" -pubout 2>/dev/null)"
  rm -f "$_kf"
  # apply, not delete+create: a delete leaves a window in which the JWT/HMAC
  # values exist only in shell variables, and `set -e` on any error between the
  # two commands loses them permanently.
  kubectl -n "$NS" create secret generic priva-shared-secret \
    --from-literal=PRIVA_SERVICE_IDENTITY__PRIVATE_KEY="$IDENTITY_KEY" \
    --from-literal=PRIVA_SERVICE_IDENTITY__PUBLIC_KEY="$IDENTITY_PUB" \
    --dry-run=client -o yaml | kubectl -n "$NS" apply -f -
else
  echo "    priva-shared-secret exists with a keypair: preserving"
fi
if kubectl -n "$NS" get secret priva-control-panel-secret >/dev/null 2>&1; then
  echo "    priva-control-panel-secret exists: preserving the login JWT secret"
else
  kubectl -n "$NS" create secret generic priva-control-panel-secret \
    --from-literal=PRIVA_AUTH__JWT_SECRET="${PRIOR_JWT:-$(openssl rand -hex 32)}"
fi
if kubectl -n "$NS" get secret priva-data-spine-secret >/dev/null 2>&1; then
  echo "    priva-data-spine-secret exists: preserving the HMAC + Fernet keys"
  # An install from before the public-key distribution fix has no verification
  # key: patch it in rather than recreating the Secret (which would lose the
  # Fernet key and orphan every stored credential).
  # Unconditional: guarding this on "is the field empty?" meant that if the
  # shared keypair was regenerated (e.g. someone deleted priva-shared-secret and
  # re-ran) data-spine kept the OLD public key — control plane signs with K2,
  # data-spine verifies with K1, every gRPC call fails, readiness stays green.
  # The value is public by definition, so writing it every run is free.
  echo "    syncing the verification key into priva-data-spine-secret"
  kubectl -n "$NS" patch secret priva-data-spine-secret --type=merge \
    -p "{\"data\":{\"PRIVA_SERVICE_IDENTITY__PUBLIC_KEY\":\"$(printf '%s' "$IDENTITY_PUB" | base64 | tr -d '\n')\"}}"
else
  # PRIOR_HMAC was captured above, before the shared Secret was recreated.
  # PUBLIC key only — data-spine must never hold the signer.
  kubectl -n "$NS" create secret generic priva-data-spine-secret \
    --from-literal=PRIVA_DATASPINE__API_KEY_HMAC_SECRET="${PRIOR_HMAC:-$(openssl rand -hex 32)}" \
    --from-literal=PRIVA_FERNET_KEY="${PRIOR_FERNET:-$(openssl rand -base64 32 | tr -d '\n')}" \
    --from-literal=PRIVA_SERVICE_IDENTITY__PUBLIC_KEY="$IDENTITY_PUB"
fi

echo "==> 7. control-plane"
kubectl apply -f deploy/k8s/tenant-networkpolicy-baseline.yaml
kubectl apply -f deploy/k8s/data-spine.yaml -f deploy/k8s/control-panel.yaml -f deploy/k8s/operator.yaml
kubectl -n "$NS" rollout restart deployment/data-spine deployment/control-panel deployment/operator
kubectl -n "$NS" rollout status deploy/data-spine --timeout=120s
kubectl -n "$NS" rollout status deploy/control-panel --timeout=120s
kubectl -n "$NS" rollout status deploy/operator --timeout=120s

echo "==> 7b. channel-connector (Feishu WS byte-path)"
kubectl apply -f deploy/k8s/channel-connector.yaml
kubectl -n "$NS" rollout restart deployment/channel-connector
kubectl -n "$NS" rollout status deploy/channel-connector --timeout=120s

# scheduler was deployed by hand and never added here, so its Deployment kept
# whatever podspec it was first created with. `rollout restart` does NOT pick up
# manifest changes, so PRIVA_SERVICE_IDENTITY__SERVICE_NAME never reached the
# live pod: it presented to data-spine as the default role and, once the
# per-workload ACL landed, crash-looped on PERMISSION_DENIED.
echo "==> 7c. scheduler (cross-tenant job dispatch)"
kubectl apply -f deploy/k8s/scheduler.yaml
kubectl -n "$NS" rollout restart deployment/scheduler
kubectl -n "$NS" rollout status deploy/scheduler --timeout=120s

# The Helm/raw baseline above is static default-deny. The operator separately
# renders the additive allow policies from Sandbox ▸ Isolation and prunes only
# its old dynamic set.

echo "==> 8. edge: Gateway + InferencePool + HTTPRoute"
kubectl apply -f deploy/gateway/gateway.yaml -f deploy/gateway/inferencepool.yaml -f deploy/gateway/httproute.yaml
kubectl -n "$NS" wait --for=condition=Programmed gateway/priva-gateway --timeout=120s

echo "==> done. Reach the edge with:"
echo "    kubectl -n $NS port-forward svc/priva-gateway 8080:80   # then open http://127.0.0.1:8080/"
