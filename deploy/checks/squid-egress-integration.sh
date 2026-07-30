#!/usr/bin/env bash
# Exercise the rendered egress policy against the exact Squid major/minor used
# by this release. This is intentionally an explicit CI check rather than a
# pytest mock: ACL ordering and URL parsing are Squid runtime behaviour.
#
# Docker is a required dependency. An unavailable daemon, image pull failure,
# wrong Squid version, parse error, or unexpected HTTP result is a hard failure.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

# Multi-architecture ubuntu/squid manifest verified as Squid 6.13. An override
# is useful for a mirrored registry, but the version assertion below still
# prevents accidentally testing another release.
IMAGE="${PRIVA_SQUID_CHECK_IMAGE:-ubuntu/squid@sha256:6a097f68bae708cedbabd6188d68c7e2e7a38cedd05a176e1cc0ba29e3bbe029}"
NETWORK_INDEX=$((($$ + RANDOM) % 512))
NETWORK_SECOND=$((18 + NETWORK_INDEX / 256))
NETWORK_THIRD=$((NETWORK_INDEX % 256))
NETWORK_SUBNET="${PRIVA_SQUID_CHECK_SUBNET:-198.${NETWORK_SECOND}.${NETWORK_THIRD}.0/24}"
ORIGIN_IP="${PRIVA_SQUID_CHECK_ORIGIN_IP:-198.${NETWORK_SECOND}.${NETWORK_THIRD}.2}"
NETWORK_NAME="priva-squid-check-$$-${RANDOM}"

TMP_DIR=""
NETWORK_ID=""
ORIGIN_ID=""
PROXY_ID=""

fail() {
  echo "squid integration: ERROR: $*" >&2
  exit 1
}

cleanup() {
  if [[ -n "$PROXY_ID" ]]; then
    docker rm -f "$PROXY_ID" >/dev/null 2>&1 || true
  fi
  if [[ -n "$ORIGIN_ID" ]]; then
    docker rm -f "$ORIGIN_ID" >/dev/null 2>&1 || true
  fi
  if [[ -n "$NETWORK_ID" ]]; then
    docker network rm "$NETWORK_ID" >/dev/null 2>&1 || true
  fi
  if [[ -n "$TMP_DIR" ]]; then
    case "$TMP_DIR" in
      */priva-squid-check.*) rm -rf -- "$TMP_DIR" ;;
      *) echo "squid integration: refusing to remove unexpected temp path: $TMP_DIR" >&2 ;;
    esac
  fi
}
trap cleanup EXIT

command -v docker >/dev/null 2>&1 \
  || fail "docker CLI is required"
docker info >/dev/null 2>&1 \
  || fail "docker daemon is unavailable"
command -v uv >/dev/null 2>&1 \
  || fail "uv is required to load the project renderer"

cd "$REPO_ROOT"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/priva-squid-check.XXXXXX")"

echo "squid integration: checking image ${IMAGE}"
VERSION_OUTPUT="$(
  docker run --rm --entrypoint squid "$IMAGE" -v 2>&1
)" || fail "could not run Squid from ${IMAGE}"
grep -Eq '^Squid Cache: Version 6\.13([[:space:]]|$)' <<<"$VERSION_OUTPUT" \
  || fail "expected Squid 6.13, got: $(grep -m1 'Squid Cache: Version' <<<"$VERSION_OUTPUT" || echo unknown)"

# Render with the production builder and validated project models. The synthetic
# origin uses an RFC 2544 benchmark address, which is outside the built-in deny
# set, so a successful CONNECT proves the positive allowlist rule actually
# matched without depending on public DNS or internet access.
uv run --frozen --package priva-operator python - "$TMP_DIR" <<'PY'
from pathlib import Path
import sys
from types import SimpleNamespace

from priva_common.config import KubernetesSettings
from priva_common.dataplane import EgressAllowEntryRecord, NetworkIsolationRecord
from priva_operator.egress_proxy import render_squid_conf

output = Path(sys.argv[1])
settings = SimpleNamespace(
    kubernetes=KubernetesSettings(egress_proxy_port=3128)
)
for mode in ("unrestricted", "allowlist", "deny_all"):
    allowlist = (
        [EgressAllowEntryRecord(host="allowed.test", port=3128)]
        if mode == "allowlist"
        else []
    )
    isolation = NetworkIsolationRecord(
        egress_mode=mode,
        egress_allowlist=allowlist,
    )
    (output / f"{mode}.conf").write_text(
        render_squid_conf(isolation, settings),
        encoding="utf-8",
    )
PY

for mode in unrestricted allowlist deny_all; do
  echo "squid integration: parsing ${mode}"
  if ! docker run --rm -i --entrypoint /bin/sh "$IMAGE" -c \
    'tee /tmp/priva-squid.conf >/dev/null && squid -k parse -f /tmp/priva-squid.conf' \
    <"$TMP_DIR/${mode}.conf" >"$TMP_DIR/${mode}.parse.log" 2>&1; then
    cat "$TMP_DIR/${mode}.parse.log" >&2
    fail "${mode} squid -k parse failed"
  fi
done

NETWORK_ID="$(docker network create --subnet "$NETWORK_SUBNET" "$NETWORK_NAME")"

# A tiny HTTP server in the same pinned image is a deterministic local target.
# It supports both the CONNECT establishment check and a normal HTTP response,
# without another image pull or any public network dependency.
ORIGIN_ID="$(
  docker run -d \
    --network "$NETWORK_ID" \
    --ip "$ORIGIN_IP" \
    --entrypoint /usr/bin/perl \
    "$IMAGE" \
    -MIO::Socket::INET -e '
      $SIG{PIPE} = "IGNORE";
      my $server = IO::Socket::INET->new(
        LocalAddr => "0.0.0.0",
        LocalPort => 3128,
        Listen => 20,
        ReuseAddr => 1,
      ) or die $!;
      while (my $client = $server->accept()) {
        $client->autoflush(1);
        while (my $line = <$client>) {
          last if $line eq "\r\n" || $line eq "\n";
        }
        print {$client} "HTTP/1.1 204 No Content\r\n",
                        "Content-Length: 0\r\n",
                        "Connection: close\r\n\r\n";
        close $client;
      }
    '
)"

wait_for_listener() {
  local container_id="$1"
  local label="$2"
  local attempt
  for attempt in {1..40}; do
    if [[ "$(docker inspect -f '{{.State.Running}}' "$container_id" 2>/dev/null || true)" != "true" ]]; then
      docker logs "$container_id" >&2 || true
      fail "${label} container exited before becoming ready"
    fi
    if docker exec "$container_id" /usr/bin/bash -c \
      'exec 3<>/dev/tcp/127.0.0.1/3128' >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  docker logs "$container_id" >&2 || true
  fail "${label} did not listen on port 3128"
}

start_proxy() {
  local mode="$1"
  PROXY_ID="$(
    docker run -d \
      --network "$NETWORK_ID" \
      --add-host "allowed.test:${ORIGIN_IP}" \
      --add-host "blocked.test:${ORIGIN_IP}" \
      --mount "type=bind,src=${TMP_DIR}/${mode}.conf,dst=/tmp/priva-squid.conf,readonly" \
      --read-only \
      --user 13:13 \
      --cap-drop ALL \
      --security-opt no-new-privileges:true \
      --tmpfs /var/log/squid:rw,noexec,nosuid,nodev,uid=13,gid=13,size=67108864 \
      --tmpfs /var/spool/squid:rw,noexec,nosuid,nodev,uid=13,gid=13,size=67108864 \
      --entrypoint squid \
      "$IMAGE" \
      -f /tmp/priva-squid.conf -NYC
  )"
  wait_for_listener "$PROXY_ID" "${mode} proxy"
}

stop_proxy() {
  docker rm -f "$PROXY_ID" >/dev/null
  PROXY_ID=""
}

request_status() {
  local request="$1"
  docker exec "$PROXY_ID" /usr/bin/timeout 5 /usr/bin/bash -c '
    exec 3<>/dev/tcp/127.0.0.1/3128
    printf "%s" "$1" >&3
    IFS= read -r status <&3
    printf "%s" "$status"
  ' _ "$request"
}

expect_status() {
  local label="$1"
  local expected_prefix="$2"
  local request="$3"
  local status
  if ! status="$(request_status "$request")"; then
    fail "${label}: proxy did not return a status within 5 seconds"
  fi
  if [[ "$status" != "$expected_prefix"* ]]; then
    fail "${label}: expected '${expected_prefix}...', got '${status}'"
  fi
  echo "squid integration: ${label}: ${status%$'\r'}"
}

start_proxy allowlist
expect_status \
  "allowlisted CONNECT" \
  "HTTP/1.1 200 Connection established" \
  $'CONNECT allowed.test:3128 HTTP/1.1\r\nHost: allowed.test:3128\r\nConnection: close\r\n\r\n'
expect_status \
  "non-allowlisted CONNECT" \
  "HTTP/1.1 403 Forbidden" \
  $'CONNECT blocked.test:3128 HTTP/1.1\r\nHost: blocked.test:3128\r\nConnection: close\r\n\r\n'
expect_status \
  "cache_object manager" \
  "HTTP/1.1 403 Forbidden" \
  $'GET cache_object://allowed.test:3128/active_requests HTTP/1.1\r\nHost: allowed.test:3128\r\nConnection: close\r\n\r\n'
expect_status \
  "HTTP manager endpoint" \
  "HTTP/1.1 403 Forbidden" \
  $'GET /squid-internal-mgr/info HTTP/1.1\r\nHost: allowed.test:3128\r\nConnection: close\r\n\r\n'
expect_status \
  "IPv6 literal authority" \
  "HTTP/1.1 403 Forbidden" \
  $'CONNECT [2001:db8::1]:3128 HTTP/1.1\r\nHost: [2001:db8::1]:3128\r\nConnection: close\r\n\r\n'
expect_status \
  "IPv6 text in query" \
  "HTTP/1.1 204 No Content" \
  $'GET http://allowed.test:3128/path?ids[0]=[::1] HTTP/1.1\r\nHost: allowed.test:3128\r\nConnection: close\r\n\r\n'
stop_proxy

start_proxy deny_all
expect_status \
  "deny_all CONNECT" \
  "HTTP/1.1 403 Forbidden" \
  $'CONNECT allowed.test:3128 HTTP/1.1\r\nHost: allowed.test:3128\r\nConnection: close\r\n\r\n'

echo "squid integration: PASS"
