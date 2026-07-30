"""Object names shared between the operator (which writes them) and control-panel
(which reads them back to show the admin what is actually in force).

These are a cross-service contract, not operator internals: the whole point of the
Isolation panel is that it reports MEASURED state rather than restating intent. The
panel it replaced hard-coded a sentence claiming Terminal could not reach data-spine
while the policies making that true were not installed at all — the claim and the
cluster had no channel between them. Sharing the names here is that channel.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from datetime import datetime, timezone

NAME_PREFIX = "priva-tenant-"

RUNNER_EGRESS = f"{NAME_PREFIX}runner-egress"
TERMINAL_EGRESS = f"{NAME_PREFIX}terminal-egress"
RUNNER_INGRESS = f"{NAME_PREFIX}runner-ingress"
TERMINAL_INGRESS = f"{NAME_PREFIX}terminal-ingress"
PROXY_POLICY = f"{NAME_PREFIX}egress-proxy"
RUNNER_BASELINE = f"{NAME_PREFIX}runner-baseline"
TERMINAL_BASELINE = f"{NAME_PREFIX}terminal-baseline"
PROXY_BASELINE = f"{NAME_PREFIX}egress-proxy-baseline"
BASELINE_POLICIES = (RUNNER_BASELINE, TERMINAL_BASELINE, PROXY_BASELINE)

# Hand-applied, deliberately NOT operator-managed: data-spine→postgres is a
# control-plane boundary, not a tenant one. Shown in the panel as a locked row so
# an admin can see it is in force without being able to switch it off from here.
POSTGRES_POLICY = "postgres-only-data-spine"

# Selector for "policies this operator owns", used for list-and-prune.
MANAGED_LABELS = {
    "app.kubernetes.io/managed-by": "priva-operator",
    "priva.io/policy-set": "tenant-isolation",
}

# Stamped on every tenant pod template carrying the always-on proxy env. The
# NetworkPolicy deliberately selects the stable ``app`` label instead: making
# enforcement depend on this rollout label left old running pods unpoliced.

PROXY_DEPLOYMENT = "priva-egress-proxy"
PROXY_APP_LABEL = "egress-proxy"

# Cross-service annotations used to bind the admin's current desired record to
# the objects the operator actually rendered. Without this generation link, an
# old but internally self-consistent policy set looks "applied" forever while
# the operator is down.
ISOLATION_INTENT_ANNOTATION = "priva.io/isolation-intent"
POLICY_SET_DIGEST_ANNOTATION = "priva.io/policy-digest"
PROXY_CONFIG_SHA256_ANNOTATION = "priva.io/egress-proxy-config-sha256"
PROXY_CONFIG_REVISION_ANNOTATION = "priva.io/egress-proxy-config-revision"

# Non-public IPv4 space which public egress must never reach. This is code-owned,
# not merely a configurable default: replacing EGRESS_BLOCKED_CIDRS in an
# environment must not remove the NetworkPolicy layer protecting metadata,
# loopback, RFC1918 or reserved destinations from a compromised proxy.
#
# 198.18/15 is intentionally deployment-configurable. Docker Desktop/minikube can
# synthesize public DNS answers in that range, so enforcing it everywhere would
# break the development topology. Production overlays add it explicitly.
BUILTIN_BLOCKED_IPV4_CIDRS = (
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.0.0.0/24",
    "192.0.2.0/24",
    "192.168.0.0/16",
    "198.51.100.0/24",
    "203.0.113.0/24",
    "224.0.0.0/4",
    "240.0.0.0/4",
)

# The hand-applied set the operator supersedes and prunes.
LEGACY_POLICIES = (
    "data-spine-deny-terminal",
    "redis-deny-terminal",
    "runner-deny-tenant-peers",
    "terminal-deny-tenant-peers",
)

# Written by deploy/checks/networkpolicy-cni.sh. Whether the CNI *enforces*
# NetworkPolicy cannot be read from any API — it has to be measured by sending a
# packet — so the measurement is cached here and the panel reports when it was
# taken. A name-based guess is exactly the mistake that disabled isolation before:
# the preflight matched CNI names against a list, judged a kindnet that DOES
# enforce as one that doesn't, and the escape hatch deleted the policies.
FACTS_CONFIG_MAP = "priva-cluster-facts"
FACT_ENFORCED = "networkPolicyEnforced"   # "true" | "false" | "unknown"
FACT_CHECKED_AT = "networkPolicyCheckedAt"
FACT_CNI = "networkPolicyCni"
FACT_PROBE_VERSION = "networkPolicyProbeVersion"
FACT_ADDRESS_FAMILY = "networkPolicyAddressFamily"
FACT_CLUSTER_UID = "networkPolicyClusterUid"
# Bump whenever a previously-successful probe no longer proves the boundary
# expected by the operator. Version 2 adds independent directions, a positive
# control path, fail-closed fact persistence, and the IPv4-only dual-stack gate.
# Version 3 additionally invalidates the old success before a re-test starts,
# records the address family/cluster identity and makes freshness mandatory.
PROBE_VERSION = "3"

_DOMAIN_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def normalize_egress_domain(value) -> str:
    """Return the canonical Squid allowlist form or raise ``ValueError``.

    Kept in common code so the admin write path and the operator's last-resort
    renderer cannot drift into accepting different grammars.
    """
    raw = str(value or "").strip().lower()
    if not raw:
        raise ValueError("allowlist host must not be empty")
    include_subdomains = raw.startswith(".")
    domain = (raw[1:] if include_subdomains else raw).rstrip(".")
    try:
        ascii_domain = domain.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(f"allowlist host is not a domain: {value!r}") from exc
    labels = ascii_domain.split(".")
    if (
        not ascii_domain
        or len(ascii_domain) > 253
        or len(labels) < 2
        or any(not _DOMAIN_LABEL.fullmatch(label) for label in labels)
    ):
        raise ValueError(f"allowlist host is not a domain: {value!r}")
    try:
        ipaddress.ip_address(ascii_domain)
    except ValueError:
        pass
    else:
        raise ValueError(f"allowlist host must be a domain, not an IP: {value!r}")
    return f".{ascii_domain}" if include_subdomains else ascii_domain


def _ipv4_cidrs(values, *, setting: str) -> list[str]:
    """Validate and normalize a deployment-supplied IPv4 CIDR list.

    NetworkPolicy in this release renders an IPv4 public route (0.0.0.0/0).
    Silently accepting an IPv6 value would therefore display a control which
    never applied to IPv6 traffic. Reject it until dual-stack policies are
    rendered explicitly.
    """
    out: list[str] = []
    for raw in values or ():
        network = ipaddress.ip_network(str(raw).strip(), strict=False)
        if network.version != 4:
            raise ValueError(f"{setting} only supports IPv4 CIDRs: {raw!r}")
        cidr = str(network)
        if cidr not in out:
            out.append(cidr)
    return out


def blocked_egress_cidrs(settings) -> list[str]:
    """All destinations that must never be reached through public egress.

    The deprecated ``egress_internal_cidrs`` remains part of the union so an
    upgrade cannot accidentally discard an installation's existing deny list.
    """
    k = settings.kubernetes
    values: list[str] = list(BUILTIN_BLOCKED_IPV4_CIDRS)
    for field in (
        "egress_blocked_cidrs",
        "cluster_pod_cidrs",
        "cluster_service_cidrs",
        "cluster_node_cidrs",
        "egress_internal_cidrs",
    ):
        values.extend(getattr(k, field, None) or ())
    return _ipv4_cidrs(values, setting="kubernetes egress CIDRs")


def cluster_access_cidrs(settings) -> list[str]:
    """Configured pod/service/node ranges used when internal access is enabled."""
    k = settings.kubernetes
    values: list[str] = []
    plural_fields_present = any(
        hasattr(k, field)
        for field in ("cluster_pod_cidrs", "cluster_service_cidrs", "cluster_node_cidrs")
    )
    for field in ("cluster_pod_cidrs", "cluster_service_cidrs", "cluster_node_cidrs"):
        values.extend(getattr(k, field, None) or ())
    # Test fixtures and old embedders may expose only the pre-split field.
    # Real Settings always has the explicit fields, so metadata/link-local ranges
    # from that legacy deny list are never turned into an internal-access grant.
    if not plural_fields_present:
        values.extend(getattr(k, "egress_internal_cidrs", None) or ())
    return _ipv4_cidrs(values, setting="kubernetes cluster CIDRs")


def dns_ip_cidrs(settings) -> list[str]:
    """Explicit resolver addresses used in addition to labelled CoreDNS pods.

    These are listener IPs, never network ranges. Accepting ``0.0.0.0/0`` here
    would turn the DNS exception into arbitrary TCP/UDP egress on port 53.
    """
    cidrs = _ipv4_cidrs(
        getattr(settings.kubernetes, "dns_ip_cidrs", None) or (),
        setting="kubernetes.dns_ip_cidrs",
    )
    broad = [cidr for cidr in cidrs if ipaddress.ip_network(cidr).prefixlen != 32]
    if broad:
        raise ValueError(
            "kubernetes.dns_ip_cidrs entries must be single IPv4 /32 addresses: "
            + ", ".join(broad)
        )
    return cidrs


def probe_fact_is_fresh(
    data: dict[str, str],
    max_age_seconds: int,
    *,
    now: datetime | None = None,
) -> bool:
    """Validate the functional CNI fact, including its bounded lifetime."""
    if max_age_seconds <= 0:
        return False
    raw = (data.get(FACT_CHECKED_AT) or "").strip()
    try:
        checked = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if checked.tzinfo is None:
        return False
    current = now or datetime.now(timezone.utc)
    age = (current - checked.astimezone(timezone.utc)).total_seconds()
    # Reject clocks more than five minutes in the future as well as stale facts.
    return -300 <= age <= max_age_seconds


_SENSITIVE_APPS = frozenset({"agent-runner", "terminal", PROXY_APP_LABEL})


def selector_may_target_tenant_runtime(selector: dict | None) -> bool:
    """Conservatively decide whether a LabelSelector can select tenant/proxy pods.

    The fixed ``app`` label is the security identity of all three pod classes.
    Selectors without an app constraint may match them; constraints on dynamic
    account labels are deliberately not treated as proof of safety.
    """
    selector = selector or {}
    candidates = set(_SENSITIVE_APPS)
    match_labels = selector.get("matchLabels") or {}
    if "app" in match_labels:
        candidates.intersection_update({str(match_labels["app"])})
    for expression in selector.get("matchExpressions") or ():
        if expression.get("key") != "app":
            continue
        operator = expression.get("operator")
        values = {str(value) for value in (expression.get("values") or ())}
        if operator == "In":
            candidates.intersection_update(values)
        elif operator == "NotIn":
            candidates.difference_update(values)
        elif operator == "DoesNotExist":
            candidates.clear()
        elif operator == "Exists":
            pass
        else:
            # Unknown selector semantics must not be interpreted as exclusion.
            return True
    return bool(candidates)


def policy_may_widen_tenant_runtime(spec: dict | None) -> bool:
    """Whether an extra NetworkPolicy contributes any allow to sensitive pods."""
    spec = spec or {}
    if not selector_may_target_tenant_runtime(spec.get("podSelector")):
        return False
    policy_types = set(spec.get("policyTypes") or ())
    # Kubernetes infers a type from a corresponding field when policyTypes is
    # omitted. An empty rule list is restrictive and cannot widen the union.
    ingress = spec.get("ingress") or []
    egress = spec.get("egress") or []
    return bool(
        (ingress and (not policy_types or "Ingress" in policy_types))
        or (egress and (not policy_types or "Egress" in policy_types))
    )


def isolation_intent_digest(record, settings) -> str:
    """Fingerprint the desired isolation generation shared by every service.

    This is intentionally based on semantic inputs, not ``updated_at``: saving
    the same values must not manufacture drift. It includes both the admin row
    and deployment topology that affects the rendered policies/proxy, allowing
    Control Panel to distinguish the current desired generation from a stale
    but otherwise valid object set.
    """
    k = settings.kubernetes
    allowlist = [
        {
            "host": str(getattr(entry, "host", "")),
            "port": int(getattr(entry, "port", 0) or 0),
        }
        for entry in (getattr(record, "egress_allowlist", None) or ())
    ]
    payload = {
        "version": 1,
        "record": {
            "runner_deny_internal": bool(
                getattr(record, "runner_deny_internal", False)
            ),
            "terminal_deny_internal": bool(
                getattr(record, "terminal_deny_internal", False)
            ),
            "deny_tenant_peers": bool(
                getattr(record, "deny_tenant_peers", False)
            ),
            "egress_mode": str(getattr(record, "egress_mode", "deny_all")),
            "egress_allowlist": allowlist,
        },
        "topology": {
            # Use rendered/canonical unions where possible so compatibility
            # fields and code-owned security floors participate in the digest.
            "blocked_egress_cidrs": blocked_egress_cidrs(settings),
            "cluster_access_cidrs": cluster_access_cidrs(settings),
            "dns_ip_cidrs": dns_ip_cidrs(settings),
            "egress_proxy_host": str(getattr(k, "egress_proxy_host", "")),
            "egress_proxy_port": int(getattr(k, "egress_proxy_port", 0) or 0),
            "egress_proxy_image": str(getattr(k, "egress_proxy_image", "")),
            "egress_no_proxy": str(getattr(k, "egress_no_proxy", "")),
            "runner_image_pull_policy": str(
                getattr(k, "runner_image_pull_policy", "")
            ),
            "runner_image_pull_secret": str(
                getattr(k, "runner_image_pull_secret", "")
            ),
            "gateway_name": str(getattr(k, "gateway_name", "")),
            "runner_service_port": int(
                getattr(k, "runner_service_port", 0) or 0
            ),
            "terminal_service_port": int(
                getattr(k, "terminal_service_port", 0) or 0
            ),
            "dataspine_grpc_dsn": str(
                getattr(getattr(settings, "dataspine", None), "grpc_dsn", "") or ""
            ),
            "scheduler_api_port": int(
                getattr(getattr(settings, "scheduler", None), "api_port", 0) or 0
            ),
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
