#!/usr/bin/env bash
# Build the four service images on the host docker, then load them into minikube
# (runtime=containerd, so `minikube image load` imports the docker-built image).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

SERVICES=("${@:-data-spine agent-runner control-panel operator}")
# allow: build.sh                  -> all
#        build.sh data-spine ...   -> subset
read -r -a LIST <<< "${SERVICES[*]}"

for svc in "${LIST[@]}"; do
  if [[ "$svc" == "nfs-xfs" ]]; then
    # DEV-ONLY storage image: its Dockerfile COPYs files relative to deploy/dev-storage,
    # so build with that as the context (not the repo root).
    echo ">>> docker build priva/nfs-xfs:dev (dev-storage)"
    docker build -t "priva/nfs-xfs:dev" -f "deploy/dev-storage/nfs-xfs.Dockerfile" deploy/dev-storage
  else
    echo ">>> docker build priva/${svc}:dev"
    docker build -t "priva/${svc}:dev" -f "deploy/docker/${svc}.Dockerfile" .
  fi
  echo ">>> minikube image load priva/${svc}:dev"
  minikube image load "priva/${svc}:dev"
done
echo ">>> images in minikube:"
minikube image ls | grep -E "priva/" || true
