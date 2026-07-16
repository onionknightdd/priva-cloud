#!/usr/bin/env bash
# Build all Priva images for linux/amd64 and push them to a registry.
#
#   deploy/uat/build-push.sh <registry> <tag> [--with-dev-storage]
#   e.g. deploy/uat/build-push.sh registry.example.com v0.2.0
#
# - Runs from any host with docker buildx (cross-builds fine on Apple Silicon; the
#   Python stages only download platform wheels, so emulation cost is modest).
# - The frontend SPAs must be built FIRST (control-panel bakes web/{user,admin}/dist):
#     cd web && npm run build:user && npm run build:admin
# - nfs-xfs is dev-only storage; UAT uses the cephfs backend, so it's skipped unless
#   --with-dev-storage is passed.
set -euo pipefail
cd "$(cd "$(dirname "$0")/../.." && pwd)"

REGISTRY="${1:?usage: build-push.sh <registry> <tag> [--with-dev-storage]}"
TAG="${2:?usage: build-push.sh <registry> <tag> [--with-dev-storage]}"
PLATFORM="${PLATFORM:-linux/amd64}"

IMAGES=(control-panel agent-runner data-spine operator channel-connector)
if [[ "${3:-}" == "--with-dev-storage" ]]; then
  IMAGES+=(nfs-xfs)
fi

# The SPAs are served from dist/ baked into the control-panel image — refuse to ship
# an image with stale or missing bundles.
for app in user admin; do
  [[ -f "web/$app/dist/index.html" ]] || {
    echo "ERROR: web/$app/dist missing — run:  cd web && npm run build:$app" >&2; exit 1; }
done

docker buildx inspect priva-uat >/dev/null 2>&1 || docker buildx create --name priva-uat >/dev/null

for name in "${IMAGES[@]}"; do
  if [[ "$name" == "nfs-xfs" ]]; then
    dockerfile="deploy/dev-storage/nfs-xfs.Dockerfile"; context="deploy/dev-storage"
  else
    dockerfile="deploy/docker/${name}.Dockerfile"; context="."
  fi
  ref="${REGISTRY}/priva/${name}:${TAG}"
  echo "=== ${ref} (${PLATFORM})"
  docker buildx build --builder priva-uat --platform "$PLATFORM" \
    -f "$dockerfile" -t "$ref" --push "$context"
done

echo "ALL PUSHED: ${IMAGES[*]} @ ${REGISTRY}/priva/*:${TAG}"
echo "Next: edit deploy/helm/priva-cloud/values-uat.yaml (registry=${REGISTRY}, tag=${TAG}) and follow deploy/uat/README.md"
