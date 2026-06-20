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
  echo ">>> docker build priva/${svc}:dev"
  docker build -t "priva/${svc}:dev" -f "deploy/docker/${svc}.Dockerfile" .
  echo ">>> minikube image load priva/${svc}:dev"
  minikube image load "priva/${svc}:dev"
done
echo ">>> images in minikube:"
minikube image ls | grep -E "priva/" || true
