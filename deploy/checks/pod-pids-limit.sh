#!/usr/bin/env bash
# Fail closed unless every kubelet bounds PIDs per pod. Kubernetes has no PodSpec
# field for this limit; it is a node-level kubelet prerequisite.
set -euo pipefail

MAX_ALLOWED="${PRIVA_MAX_POD_PIDS_LIMIT:-512}"
failed=0
while IFS= read -r node; do
  value="$(kubectl get --raw "/api/v1/nodes/${node}/proxy/configz" | python3 -c '
import json, sys
data = json.load(sys.stdin)
print((data.get("kubeletconfig") or {}).get("podPidsLimit", -1))
')"
  if [ "$value" -le 0 ] || [ "$value" -gt "$MAX_ALLOWED" ]; then
    echo "ERROR: node ${node} podPidsLimit=${value}; require 1..${MAX_ALLOWED}" >&2
    failed=1
  else
    echo "node ${node}: podPidsLimit=${value}"
  fi
done < <(kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')

if [ "$failed" -ne 0 ]; then
  echo "For a new minikube cluster: minikube start --driver=docker --cni=calico --extra-config=kubelet.pod-pids-limit=512" >&2
  exit 1
fi
