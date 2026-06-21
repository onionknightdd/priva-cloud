#!/usr/bin/env bash
#
# Build the single distributable wheel: priva_cloud-<ver>-py3-none-any.whl
#
# Flow:
#   1. Build the two SPAs (user -> dist, admin -> dist-admin).
#   2. Stage them into the control-panel package as data:
#        services/control-panel/src/priva_control_panel/_web/{dist,dist-admin}
#   3. Build the mega-wheel from packaging/priva-cloud (force-includes every service
#      + the staged _web + agent-runner/bundled).
#
# The result is self-contained:
#   pip install dist/priva_cloud-*.whl
#   priva-cloud control-panel --port 8080   # serves / and /admin from bundled package data
#
# Usage:
#   scripts/build-wheel.sh [--skip-web]
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${SCRIPT_DIR}"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${GREEN}[build-wheel]${NC} $1"; }
warn() { echo -e "${YELLOW}[build-wheel]${NC} $1"; }
die()  { echo -e "${RED}[build-wheel]${NC} $1"; exit 1; }

SKIP_WEB=false
for arg in "$@"; do
    case "$arg" in
        --skip-web) SKIP_WEB=true ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) die "unknown option: $arg" ;;
    esac
done

CP_PKG="services/control-panel/src/priva_control_panel"
WEB_STAGE="${CP_PKG}/_web"

# --- 1. + 2. build SPAs and stage into the control-panel package ---
stage_dist() {
    # $1 = built dist dir, $2 = target subdir under _web
    local src="$1" sub="$2"
    [ -f "${src}/index.html" ] || die "expected ${src}/index.html — SPA build did not produce output"
    rm -rf "${WEB_STAGE:?}/${sub}"
    mkdir -p "${WEB_STAGE}/${sub}"
    cp -r "${src}/." "${WEB_STAGE}/${sub}/"
    log "staged ${src} -> ${WEB_STAGE}/${sub}"
}

if [ "${SKIP_WEB}" = "true" ]; then
    warn "--skip-web: reusing whatever is already staged under ${WEB_STAGE}"
    [ -f "${WEB_STAGE}/dist/index.html" ] || die "no staged user SPA; run without --skip-web"
else
    # Split layout: three projects under web/ (shared component lib + user + admin SPAs).
    log "building web (web/user + web/admin)"
    [ -d "web/node_modules" ] || (log "npm install (web)"; npm --prefix web install)
    npm --prefix web run build:user
    npm --prefix web run build:admin
    stage_dist "web/user/dist"  "dist"
    stage_dist "web/admin/dist" "dist-admin"
fi

# --- 3. build the mega-wheel ---
log "building mega-wheel (packaging/priva-cloud)"
mkdir -p dist
# --wheel only: the sdist step can't capture packaging's ../../ force-include paths.
# --no-isolation: build deps (hatchling) are already in the active env; avoids a network hop.
if python -c "import build" >/dev/null 2>&1; then
    python -m build --wheel --no-isolation --outdir dist packaging/priva-cloud
else
    die "python 'build' module not found. Install it first: uv pip install build hatchling"
fi

WHEEL="$(ls -t dist/priva_cloud-*.whl 2>/dev/null | head -1)"
[ -n "${WHEEL}" ] || die "wheel not produced"
log "=========================================="
log " wheel: ${WHEEL}"
log " size : $(du -sh "${WHEEL}" | cut -f1)"
log "=========================================="
log " install:  pip install ${WHEEL}"
log " run    :  priva-cloud control-panel --port 8080"
