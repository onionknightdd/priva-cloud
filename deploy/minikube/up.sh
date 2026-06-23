#!/usr/bin/env bash
# Reproducible minikube bring-up for the agentgateway/operator/EPP slice.
# Prereqs: minikube running (driver=docker), kubectl, helm, docker. Run from repo root.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
NS=priva-cloud

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
kubectl apply -f deploy/rbac/operator-rbac.yaml -f deploy/rbac/control-panel-rbac.yaml

echo "==> 6. config + shared secret (generated; not committed)"
kubectl apply -f deploy/k8s/configmap.yaml
kubectl -n "$NS" create secret generic priva-shared-secret \
  --from-literal=PRIVA_AUTH__JWT_SECRET="$(openssl rand -hex 32)" \
  --from-literal=PRIVA_DATASPINE__API_KEY_HMAC_SECRET="$(openssl rand -hex 32)" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "==> 7. control-plane"
kubectl apply -f deploy/k8s/data-spine.yaml -f deploy/k8s/control-panel.yaml -f deploy/k8s/operator.yaml
kubectl -n "$NS" rollout status deploy/data-spine --timeout=120s
kubectl -n "$NS" rollout status deploy/control-panel --timeout=120s
kubectl -n "$NS" rollout status deploy/operator --timeout=120s

echo "==> 8. edge: Gateway + InferencePool + HTTPRoute"
kubectl apply -f deploy/gateway/gateway.yaml -f deploy/gateway/inferencepool.yaml -f deploy/gateway/httproute.yaml
kubectl -n "$NS" wait --for=condition=Programmed gateway/priva-gateway --timeout=120s

echo "==> done. Reach the edge with:"
echo "    kubectl -n $NS port-forward svc/priva-gateway 8080:80   # then open http://127.0.0.1:8080/"
