#!/usr/bin/env bash
# Does this cluster's CNI actually ENFORCE NetworkPolicy?
#
# This used to answer by matching the CNI's name against a hand-maintained list of
# six "known good" plugins. That was wrong in both directions and cost us the
# control it was meant to protect: recent kindnetd DOES enforce NetworkPolicy, but
# it wasn't on the list, so `up.sh` aborted at step 0 and the only way past was the
# escape hatch — which DELETES the tenant isolation policies. The guard is why the
# isolation was off.
#
# A name can't answer this question. The same plugin enforces or doesn't depending
# on version and flags, and a VPC-native CNI (Volcengine cello, AWS VPC CNI,
# Alibaba Terway) delegates policy to a separate engine that may or may not be
# installed. So measure both directions with independent client/server pairs:
# deny ingress to one server and deny egress from one client, then verify both
# packets die while every probe pod stays Ready.
#
# Exit codes — the caller decides what to do with each:
#   0  enforced
#   1  NOT enforced (policy objects will be accepted and silently ignored)
#   2  undetermined (probe couldn't run; do NOT read this as either answer)
set -uo pipefail

NS="${PRIVA_NP_PROBE_NS:-priva-np-probe-$$-${RANDOM}}"
IMAGE="${PRIVA_NP_PROBE_IMAGE:-busybox:1.36}"
READY_TIMEOUT="${PRIVA_NP_PROBE_TIMEOUT:-90s}"

FACTS_NS="${PRIVA_FACTS_NS:-priva-cloud}"
FACTS_CM="priva-cluster-facts"
CREATED_NS=0
CLUSTER_UID=""

# Cache the verdict where the admin Isolation panel can read it. Enforcement is
# not readable from any API — it has to be measured — so the panel would
# otherwise have to guess, and guessing from the CNI's name is precisely the
# mistake this script was rewritten to stop making.
record_fact() {
  local verdict="$1" ingress="$2" egress="$3" cni="${4:-}" address_family="${5:-unknown}"
  if ! kubectl get ns "$FACTS_NS" >/dev/null 2>&1; then
    if ! kubectl create ns "$FACTS_NS" >/dev/null 2>&1; then
      echo "probe: warning: cannot create facts namespace $FACTS_NS; verdict was not recorded" >&2
      return 1
    fi
  fi
  kubectl -n "$FACTS_NS" create configmap "$FACTS_CM" \
    --from-literal=networkPolicyEnforced="$verdict" \
    --from-literal=networkPolicyIngressEnforced="$ingress" \
    --from-literal=networkPolicyEgressEnforced="$egress" \
    --from-literal=networkPolicyProbeVersion="3" \
    --from-literal=networkPolicyCheckedAt="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --from-literal=networkPolicyCni="$cni" \
    --from-literal=networkPolicyAddressFamily="$address_family" \
    --from-literal=networkPolicyClusterUid="$CLUSTER_UID" \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null 2>&1 || {
      echo "probe: warning: could not record verdict in $FACTS_NS/$FACTS_CM" >&2
      return 1
    }
}

detect_cni() {
  kubectl -n kube-system get daemonsets -o jsonpath='{range .items[*]}{.metadata.name}{" "}{end}' 2>/dev/null \
    | tr ' ' '\n' | grep -iE 'calico|cilium|antrea|kube-router|weave|canal|kindnet|cello|terway' \
    | head -1
}

cleanup() {
  if [[ "$CREATED_NS" == "1" ]]; then
    kubectl delete ns "$NS" --wait=false >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

CNI="$(detect_cni || true)"
CLUSTER_UID="$(kubectl get namespace kube-system \
  -o jsonpath='{.metadata.uid}' 2>/dev/null || true)"

# Re-testing must revoke the previous success before creating a single probe
# workload. Otherwise a failed or interrupted re-test leaves an old "true" fact
# authorizing tenant wake indefinitely. This write is mandatory; if it cannot be
# persisted the gate cannot be made fail-closed.
if ! record_fact unknown unknown unknown "$CNI" unknown; then
  echo "probe: cannot invalidate the previous enforcement fact" >&2
  exit 2
fi
if [[ -z "$CLUSTER_UID" ]]; then
  echo "probe: cannot read the kube-system namespace UID" >&2
  exit 2
fi

if [[ "$NS" == "$FACTS_NS" ]]; then
  echo "probe: probe namespace and facts namespace must be different" >&2
  exit 2
fi
if kubectl get ns "$NS" >/dev/null 2>&1; then
  echo "probe: namespace $NS already exists; refusing to delete or reuse it" >&2
  record_fact unknown unknown unknown "$CNI"
  exit 2
fi
if ! kubectl create ns "$NS" >/dev/null 2>&1; then
  echo "probe: cannot create namespace $NS" >&2
  record_fact unknown unknown unknown "$CNI"
  exit 2
fi
CREATED_NS=1

run_server() {
  local name="$1" role="$2"
  kubectl -n "$NS" run "$name" --image="$IMAGE" --image-pull-policy=IfNotPresent \
    --labels="priva.io/np-probe-role=$role" --restart=Never \
    --command -- sh -c 'while true; do printf "HTTP/1.1 200 OK\r\n\r\nok\n" | nc -l -p 8080; done' \
    >/dev/null 2>&1
}
run_client() {
  local name="$1" role="$2"
  kubectl -n "$NS" run "$name" --image="$IMAGE" --image-pull-policy=IfNotPresent \
    --labels="priva.io/np-probe-role=$role" --restart=Never \
    --command -- sleep 3600 >/dev/null 2>&1
}

if ! run_server ingress-server ingress-server \
  || ! run_client ingress-client ingress-client \
  || ! run_server egress-server egress-server \
  || ! run_client egress-client egress-client; then
  echo "probe: failed to create one or more probe pods" >&2
  record_fact unknown unknown unknown "$CNI"
  exit 2
fi

if ! kubectl -n "$NS" wait --for=condition=Ready \
  pod/ingress-server pod/ingress-client pod/egress-server pod/egress-client \
  --timeout="$READY_TIMEOUT" >/dev/null 2>&1; then
  echo "probe: pods never became Ready (image pull blocked?). Set PRIVA_NP_PROBE_IMAGE" >&2
  echo "       to something this cluster can pull, or check enforcement by hand." >&2
  record_fact unknown unknown unknown "$CNI"
  exit 2
fi

INGRESS_SERVER_IP="$(kubectl -n "$NS" get pod ingress-server -o jsonpath='{.status.podIP}')"
EGRESS_SERVER_IP="$(kubectl -n "$NS" get pod egress-server -o jsonpath='{.status.podIP}')"
if [[ -z "$INGRESS_SERVER_IP" || -z "$EGRESS_SERVER_IP" ]]; then
  echo "probe: a server pod has no IP; cannot test enforcement" >&2
  record_fact unknown unknown unknown "$CNI"
  exit 2
fi

# This release renders and verifies an IPv4-only public boundary. A basic IPv4
# drop does not prove that the CNI enforces the same policy for a second IPv6
# address, and Squid's destination ACL is not a substitute for that L3 boundary.
# Refuse to mint a reusable "true" fact on a dual-stack cluster.
POD_IPS="$(kubectl -n "$NS" get pods -l 'priva.io/np-probe-role' \
  -o jsonpath='{range .items[*].status.podIPs[*]}{.ip}{"\n"}{end}' 2>/dev/null)" || {
  echo "probe: cannot inspect probe pod address families" >&2
  record_fact unknown unknown unknown "$CNI"
  exit 2
}
if grep -q ':' <<<"$POD_IPS"; then
  echo "probe: dual-stack pod addresses detected; this release verifies IPv4 only" >&2
  record_fact unknown unknown unknown "$CNI"
  exit 2
fi

reach() {
  local client="$1" server_ip="$2"
  kubectl -n "$NS" exec "$client" -- nc -z -w 3 "$server_ip" 8080 >/dev/null 2>&1
}
all_ready() {
  local ready
  ready="$(kubectl -n "$NS" get pods \
    -l 'priva.io/np-probe-role' \
    -o jsonpath='{range .items[*]}{range .status.conditions[?(@.type=="Ready")]}{.status}{"\n"}{end}{end}' \
    2>/dev/null)" || return 1
  [[ "$(grep -c '^True$' <<<"$ready")" == "4" ]]
}
baseline_reaches() {
  local client="$1" server_ip="$2"
  for _ in 1 2 3; do
    reach "$client" "$server_ip" && return 0
    sleep 1
  done
  return 1
}

# Baselines. If either fails, a later "blocked" result proves nothing.
if ! baseline_reaches ingress-client "$INGRESS_SERVER_IP" \
  || ! baseline_reaches egress-client "$EGRESS_SERVER_IP" \
  || ! baseline_reaches ingress-client "$EGRESS_SERVER_IP"; then
  echo "probe: baseline connectivity failed; cannot distinguish enforcement from breakage" >&2
  record_fact unknown unknown unknown "$CNI"
  exit 2
fi

if ! kubectl -n "$NS" apply -f - >/dev/null 2>&1 <<EOF
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: deny-probe-ingress
  namespace: $NS
spec:
  podSelector:
    matchLabels:
      priva.io/np-probe-role: ingress-server
  policyTypes:
    - Ingress
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: deny-probe-egress
  namespace: $NS
spec:
  podSelector:
    matchLabels:
      priva.io/np-probe-role: egress-client
  policyTypes:
    - Egress
EOF
then
  echo "probe: API server rejected the probe NetworkPolicies" >&2
  record_fact unknown unknown unknown "$CNI"
  exit 2
fi

# CNI programming is asynchronous. Require two consecutive blocked attempts in
# each direction so a transient packet loss cannot be mistaken for enforcement.
INGRESS_HITS=0
EGRESS_HITS=0
INGRESS_ENFORCED=false
EGRESS_ENFORCED=false
for _ in 1 2 3 4 5 6; do
  if [[ "$INGRESS_ENFORCED" != "true" ]]; then
    if reach ingress-client "$INGRESS_SERVER_IP"; then
      INGRESS_HITS=0
    else
      INGRESS_HITS=$((INGRESS_HITS + 1))
      [[ "$INGRESS_HITS" -ge 2 ]] && INGRESS_ENFORCED=true
    fi
  fi
  if [[ "$EGRESS_ENFORCED" != "true" ]]; then
    if reach egress-client "$EGRESS_SERVER_IP"; then
      EGRESS_HITS=0
    else
      EGRESS_HITS=$((EGRESS_HITS + 1))
      [[ "$EGRESS_HITS" -ge 2 ]] && EGRESS_ENFORCED=true
    fi
  fi
  [[ "$INGRESS_ENFORCED" == "true" && "$EGRESS_ENFORCED" == "true" ]] && break
  sleep 2
done

if ! all_ready; then
  echo "probe: a probe pod stopped being Ready; blocked packets are inconclusive" >&2
  record_fact unknown unknown unknown "$CNI"
  exit 2
fi

# Positive control after the deny policies are programmed. This path is selected
# by neither policy (unrestricted client -> unrestricted server), so a transient
# namespace/CNI-wide outage must not be mistaken for successful enforcement just
# because both negative probes happened to fail at the same time.
if ! baseline_reaches ingress-client "$EGRESS_SERVER_IP"; then
  echo "probe: unaffected control path failed after policy apply; result is inconclusive" >&2
  record_fact unknown unknown unknown "$CNI"
  exit 2
fi

if [[ "$INGRESS_ENFORCED" == "true" && "$EGRESS_ENFORCED" == "true" ]]; then
  echo "NetworkPolicy: INGRESS+EGRESS ENFORCED${CNI:+ ($CNI)}"
  if ! record_fact true true true "$CNI" ipv4; then
    echo "probe: enforcement passed but the fail-closed gate could not be recorded" >&2
    exit 2
  fi
  exit 0
fi

echo "NetworkPolicy: NOT FULLY ENFORCED${CNI:+ ($CNI)}" >&2
echo "  ingress=$INGRESS_ENFORCED egress=$EGRESS_ENFORCED" >&2
echo "  Policy objects may be accepted by the API server but ignored in one or both" >&2
echo "  directions. Do not start tenant workloads until both directions pass." >&2
record_fact false "$INGRESS_ENFORCED" "$EGRESS_ENFORCED" "$CNI" ipv4
exit 1
