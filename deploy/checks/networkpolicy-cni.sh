#!/usr/bin/env bash
# A NetworkPolicy object has no effect unless the cluster CNI enforces it. The
# dev bring-up fails closed instead of giving a false sense of Terminal isolation.
set -euo pipefail

daemonsets="$(kubectl -n kube-system get daemonsets -o name)"
for supported in calico-node cilium antrea-agent kube-router weave-net canal; do
  if grep -Eq "(^|/)${supported}$" <<<"$daemonsets"; then
    echo "NetworkPolicy CNI: ${supported}"
    exit 0
  fi
done

echo "ERROR: no recognized NetworkPolicy-enforcing CNI is installed." >&2
echo "The default minikube Kindnet CNI does not enforce NetworkPolicy." >&2
echo "Create dev clusters with: minikube start --driver=docker --cni=calico --extra-config=kubelet.pod-pids-limit=512" >&2
exit 1
