"""Squid egress proxy — manifest + squid.conf rendering.

Pure builders, like ``netpol``: everything here is a dict or a string, so the
generated config is unit-testable without a cluster.

Why a proxy at all: NetworkPolicy is L3/L4 and cannot express a domain. The hosts
a runner genuinely needs (api.anthropic.com, registry.npmjs.org, …) sit behind
CDNs on rotating, shared IPs — a CIDR allowlist for them would either be stale
tomorrow or so broad it admits most of the internet. Squid matches on the CONNECT
target, which is the name the client asked for.

Two properties the surrounding code depends on:

* **The proxy env vars are not the control.** Tenant bash can `unset HTTPS_PROXY`.
  What makes the proxy unavoidable is the NetworkPolicy that denies direct egress
  (``netpol``). Env alone is a convenience for well-behaved clients.
* **The proxy must not become a confused deputy.** It is the one pod allowed out,
  so if it could also reach in-cluster services a tenant could simply ask it to
  CONNECT to data-spine. ``netpol.build_policies`` gives it its own egress policy
  that excludes the cluster ranges.

The pinned production image should be built with OpenSSL if policy requires
matching CONNECT authority to the encrypted TLS SNI. Ubuntu's current Squid 6.13
image is built with GnuTLS and rejects the ``ssl-bump`` listener option, so this
renderer does not pretend it can inspect the inside of a CONNECT tunnel. It
enforces the requested authority/port, strict Host consistency, and the resolved
destination IP; changing the image alone does not add SNI enforcement.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json

from priva_common.network_isolation import (
    BUILTIN_BLOCKED_IPV4_CIDRS,
    ISOLATION_INTENT_ANNOTATION,
    PROXY_APP_LABEL as APP_LABEL,
    PROXY_CONFIG_REVISION_ANNOTATION,
    PROXY_CONFIG_SHA256_ANNOTATION,
    PROXY_DEPLOYMENT as NAME,
    normalize_egress_domain,
)

CONFIG_MAP = f"{NAME}-config"
CONFIG_KEY = "squid.conf"
# Replaces the image's own config. A conf.d fragment would be evaluated AFTER
# debian.conf's `http_access allow localnet`, which matches every RFC1918 pod IP —
# see render_squid_conf.
CONFIG_MOUNT = "/etc/squid/squid.conf"

# Digest of the complete desired workload, stamped on the POD TEMPLATE (not just
# the ConfigMap). A ConfigMap volume update alone would not reach squid: it
# re-reads its config only on `squid -k reconfigure`. Including the whole
# Deployment spec also means an image/security-context/resource change cannot be
# skipped by the operator's digest fast path.
CONFIG_DIGEST_ANNOTATION = "priva.io/egress-proxy-digest"

# Defense in depth for the proxy pod. NetworkPolicy is still the primary L3/L4
# boundary, but it has unavoidable blind spots: traffic to the local node is not
# covered by the Kubernetes NetworkPolicy API and some CNIs evaluate ipBlock
# before/after Service DNAT differently. Squid resolves the CONNECT authority and
# applies this `dst` ACL to that actual address before any mode-specific allow.
#
# 100.64/10 covers CGNAT-backed VPCs, Alibaba's 100.100.100.200 metadata IP and
# Volcengine's 100.96.0.96 metadata IP. This release does not render a dual-stack
# public NetworkPolicy, so the deployment probe rejects dual-stack clusters.
# The catch-all IPv6 ACL below is additional defense, not the primary proof:
# Squid can resolve an ACL and a later forwarding attempt to different members
# of a mixed A/AAAA answer.
#
# RFC 2544's 198.18/15 is intentionally deployment-configurable instead of
# built-in: Docker Desktop (including the project's minikube environment) maps
# ordinary public DNS answers into that synthetic range. Blocking it
# unconditionally makes every public destination look internal. Production
# clusters that do not use that mapping should add it to egress_blocked_cidrs.
_BUILTIN_BLOCKED_CIDRS = (*BUILTIN_BLOCKED_IPV4_CIDRS, "::/0")

_CIDR_LIST_SETTINGS = (
    # The Helm chart exposes these as JSON arrays.
    "egress_blocked_cidrs",
    "cluster_pod_cidrs",
    "cluster_service_cidrs",
    "cluster_node_cidrs",
    # Backward-compatible name used by the first implementation.
    "egress_internal_cidrs",
)

def _iter_setting_values(value):
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return value


def blocked_destination_cidrs(settings) -> tuple[str, ...]:
    """Return deterministic, validated CIDRs Squid must never connect to.

    Invalid deployment configuration is rejected rather than silently weakening
    the boundary. The operator's strict bootstrap path then keeps tenant
    workloads closed instead of installing a proxy with a missing exclusion.
    """
    k = settings.kubernetes
    values: list[str] = list(_BUILTIN_BLOCKED_CIDRS)
    for name in _CIDR_LIST_SETTINGS:
        values.extend(str(v) for v in _iter_setting_values(getattr(k, name, None)))

    canonical: dict[
        tuple[int, int, int], ipaddress.IPv4Network | ipaddress.IPv6Network
    ] = {}
    for raw in values:
        try:
            network = ipaddress.ip_network(raw.strip(), strict=False)
        except (AttributeError, ValueError) as exc:
            raise ValueError(f"invalid blocked egress CIDR: {raw!r}") from exc
        key = (network.version, int(network.network_address), network.prefixlen)
        canonical[key] = network
    collapsed = []
    for version in (4, 6):
        collapsed.extend(
            ipaddress.collapse_addresses(
                network for network in canonical.values() if network.version == version
            )
        )
    return tuple(str(network) for network in collapsed)


def _allowlist_host(value) -> str | None:
    """Return Squid-safe ASCII dstdomain syntax, or None for an invalid row.

    The API validates normal writes, but the data-spine row can predate that
    validation or be changed out of band. Never interpolate raw admin text into
    squid.conf: a newline there is a configuration-injection primitive.
    """
    try:
        return normalize_egress_domain(value)
    except ValueError:
        return None


def _allowlist_port(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def render_squid_conf(iso, settings) -> str:
    """A COMPLETE squid.conf, replacing the image's own — not a conf.d fragment.

    Measured on ubuntu/squid:6.13, where a fragment is unsafe three ways:

      1. ``/etc/squid/conf.d/debian.conf`` ships ``http_access allow localnet``,
         and conf.d is included in alphabetical order, so it is evaluated BEFORE
         a file named priva.conf. Pod IPs are RFC1918 and therefore `localnet`,
         so squid would allow everything before reaching a single rule below —
         the allowlist would be a silent no-op and this would be an open proxy.
      2. The base config's ``http_access deny CONNECT !SSL_ports`` (SSL_ports =
         443 only) runs before the include, so an allowlist entry on any other
         port could never match.
      3. ``http_port`` is already declared, so redeclaring it is a conflict.

    Inheriting an ordered ACL list from a base image is exactly the mistake this
    file exists to avoid: in squid the ORDER is the security property.

    One acl pair per entry, so `host:port` in the admin UI means exactly that. A
    single shared port list would be looser than what the UI shows — domain A's
    port would silently also open for domain B.
    """
    mode = getattr(iso, "egress_mode", "deny_all")
    lines = [
        "# Generated by priva-operator from the admin Isolation settings. Do not edit.",
        "# Complete config: the image's squid.conf is REPLACED, never extended.",
        f"http_port {settings.kubernetes.egress_proxy_port}",
        # Validate forward-proxy Host/authority names and ports instead of merely
        # logging mismatches. This covers plain HTTP and the CONNECT request
        # itself; TLS SNI needs ssl-bump support (not built into ubuntu/squid).
        "host_verify_strict on",
        # Do not disclose the tenant pod address or the proxy implementation to
        # an HTTP origin. These are privacy controls only; NetworkPolicy remains
        # the authorization boundary.
        "forwarded_for delete",
        "via off",
        "",
        "acl CONNECT method CONNECT",
        # Squid exposes process/request metadata through both the historical
        # cache_object scheme and the HTTP /squid-internal-mgr endpoint. This is
        # a shared proxy, so no tenant request may reach either interface.
        "acl priva_cache_manager proto cache_object",
        r"acl priva_internal_manager urlpath_regex -i ^/squid-internal-mgr(/|$)",
    ]
    for cidr in blocked_destination_cidrs(settings):
        lines.append(f"acl priva_blocked_dst dst {cidr}")
    if mode == "deny_all":
        # FIRST, and before any `dst` ACL. `dst` matching forces Squid to resolve
        # the requested name, but deny_all also strips the proxy's own egress —
        # including DNS — so that resolution never completes. Measured on the dev
        # cluster: the client connected to :3128, sent CONNECT, and then hung
        # until its own timeout instead of being refused. `deny all` needs no
        # resolution, so the tenant gets an immediate 403 it can report.
        # The rules below stay for defence in depth; they are simply unreachable.
        lines += ["", "http_access deny all"]

    lines += [
        "",
        # This rule MUST precede unrestricted/domain allows. It protects metadata,
        # node, Service and pod destinations even if DNS names are attacker-owned.
        "http_access deny priva_blocked_dst",
        # Ubuntu's GnuTLS Squid build does not reliably apply a `dst ::/0` ACL to
        # every bracketed IPv6 CONNECT authority. Reject the literal authority at
        # the request layer as well. Anchor the expression to the authority so a
        # harmless IPv6 string in a path/query does not block an IPv4 request.
        (
            r"acl priva_ipv6_literal url_regex -i "
            r"^([a-z][a-z0-9+.-]*://)?\[[0-9a-f:.]+\]"
            r"(:[0-9]+)?(/|$)"
        ),
        "http_access deny priva_ipv6_literal",
        # These rules must precede every mode-specific allow. In particular, an
        # unrestricted proxy must not expose active request URLs across tenants.
        "http_access deny priva_cache_manager",
        "http_access deny priva_internal_manager",
    ]

    allow: list[str] = []
    if mode == "allowlist":
        for i, entry in enumerate(getattr(iso, "egress_allowlist", ()) or ()):
            host = _allowlist_host(getattr(entry, "host", None))
            port = _allowlist_port(getattr(entry, "port", None))
            if host is None or port is None:
                # Safe omission: one corrupt row must not make Squid fail to
                # parse, and it must never become a broader rule.
                lines.append(f"# skipped invalid allowlist entry {i}")
                continue
            # squid dstdomain: a leading dot matches the domain AND its
            # subdomains, which is the semantics the record documents.
            # `-n` is a security boundary, not an optimisation. Without it,
            # Squid reverse-resolves a CONNECT made directly to an IP address;
            # an IP whose PTR happens to match an allowed suffix then inherits
            # that domain's grant even though the client never requested it.
            lines.append(f"acl priva_dom_{i} dstdomain -n {host}")
            # `_allowlist_port` accepts only 1..65535. There is deliberately no
            # host-only fallback: every row grants exactly one concrete port.
            lines.append(f"acl priva_prt_{i} port {port}")
            allow.append(f"http_access allow priva_dom_{i} priva_prt_{i}")
    elif mode == "unrestricted":
        allow.append("http_access allow all")

    lines.append("")
    lines.extend(allow)
    lines += [
        "",
        # Everything not allowed above. An empty allowlist, deny_all, or an
        # unknown future mode therefore denies all.
        "http_access deny all",
        "",
        # Tenant traffic must not be retained on a shared node.
        "cache deny all",
        # The image defaults cache_mem to 256 MiB, exactly equal to this pod's
        # memory limit before Squid/process overhead. Keep the disabled cache
        # from turning ordinary concurrency into an avoidable OOMKill.
        "cache_mem 32 MB",
        "maximum_object_size_in_memory 0 KB",
        # Do not spawn Squid's privileged ICMP helper. It is unnecessary for a
        # TCP-only forward proxy and the container intentionally has no raw
        # socket capability.
        "pinger_enable off",
        "access_log none",
        "cache_store_log none",
        # NO access_log/cache_log redirection to /dev/stdout: squid drops to the
        # `proxy` user, which cannot open the container's stdout — measured, and
        # it is FATAL at startup, i.e. a CrashLooping proxy. Access logging is
        # disabled above; cache/error logs use the bounded emptyDir.
        "logfile_rotate 0",
        # The pod runs as uid 13 (proxy) under runAsNonRoot, and /run is
        # root-owned 755 in the image — squid's default pid file is FATAL there.
        # Measured: CrashLoopBackOff, which under allowlist mode means every agent
        # hangs. A pid file is useless in a container anyway; k8s owns the
        # lifecycle. (/var/log/squid and /var/spool/squid are already proxy-owned.)
        "pid_filename none",
        "coredump_dir /var/spool/squid",
        "max_filedescriptors 1024",
        f"visible_hostname {NAME}",
        # A dead upstream should surface as an error, not a stall: the claude CLI
        # prints nothing and never exits when its proxy hangs, so a silent hang
        # here reads to the user as "the agent froze".
        "connect_timeout 15 seconds",
        "read_timeout 120 seconds",
        # A security-policy rollout uses Deployment/Recreate so no old, wider
        # generation remains routable if the replacement cannot start. Do not
        # preserve existing CONNECT tunnels during that cutover.
        "shutdown_lifetime 0 seconds",
    ]
    return "\n".join(lines) + "\n"


def _manifest_digest(body: dict, conf: str) -> str:
    payload = json.dumps(
        {"config": conf, "deployment": body},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _labels() -> dict:
    return {"app": APP_LABEL, "app.kubernetes.io/managed-by": "priva-operator"}


def config_sha256(conf: str) -> str:
    return hashlib.sha256(conf.encode()).hexdigest()


def config_map_body(
    namespace: str, conf: str, *, intent_digest: str | None = None
) -> dict:
    annotations = {PROXY_CONFIG_SHA256_ANNOTATION: config_sha256(conf)}
    if intent_digest is not None:
        annotations[ISOLATION_INTENT_ANNOTATION] = intent_digest
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": CONFIG_MAP,
            "namespace": namespace,
            "labels": _labels(),
            "annotations": annotations,
        },
        "data": {CONFIG_KEY: conf},
    }


def service_body(namespace: str, settings) -> dict:
    port = settings.kubernetes.egress_proxy_port
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": NAME, "namespace": namespace, "labels": _labels()},
        "spec": {
            "type": "ClusterIP",
            "selector": {"app": APP_LABEL},
            "externalIPs": [],
            "ports": [{
                "port": port,
                "targetPort": port,
                "name": "proxy",
                "protocol": "TCP",
            }],
        },
    }


def deployment_body(
    namespace: str,
    conf: str,
    settings,
    *,
    config_revision: str | None = None,
    intent_digest: str | None = None,
) -> dict:
    k = settings.kubernetes
    port = k.egress_proxy_port
    body = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": NAME, "namespace": namespace, "labels": _labels()},
        "spec": {
            # Two replicas cover ordinary pod/node loss. Policy changes use
            # Recreate deliberately: retaining an old Ready unrestricted pod
            # while a tightened allowlist generation fails to start is a
            # fail-open rollout. A short fail-closed outage is the safe result.
            "replicas": 2,
            "selector": {"matchLabels": {"app": APP_LABEL}},
            "strategy": {"type": "Recreate"},
            "template": {
                "metadata": {
                    "labels": _labels(),
                    "annotations": {},
                },
                "spec": {
                    "serviceAccountName": "default",
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "hostNetwork": False,
                    "hostPID": False,
                    "hostIPC": False,
                    "shareProcessNamespace": False,
                    "terminationGracePeriodSeconds": 5,
                    # Prefer different nodes so the two replicas are not a
                    # cosmetic redundancy. This remains soft for single-node
                    # development clusters; production capacity should provide
                    # at least two schedulable nodes.
                    "affinity": {
                        "podAntiAffinity": {
                            "preferredDuringSchedulingIgnoredDuringExecution": [
                                {
                                    "weight": 100,
                                    "podAffinityTerm": {
                                        "labelSelector": {
                                            "matchLabels": {"app": APP_LABEL}
                                        },
                                        "topologyKey": "kubernetes.io/hostname",
                                    },
                                }
                            ]
                        }
                    },
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 13,
                        "runAsGroup": 13,
                        "fsGroup": 13,
                        "fsGroupChangePolicy": "OnRootMismatch",
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "squid",
                            "image": k.egress_proxy_image,
                            "imagePullPolicy": k.runner_image_pull_policy,
                            "args": ["-f", CONFIG_MOUNT, "-NYC"],
                            "ports": [{"containerPort": port, "name": "proxy"}],
                            "resources": {
                                "requests": {"cpu": "50m", "memory": "64Mi"},
                                "limits": {"cpu": "500m", "memory": "256Mi"},
                            },
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "privileged": False,
                                "readOnlyRootFilesystem": True,
                                "runAsNonRoot": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "volumeMounts": [
                                {
                                    "name": "config",
                                    "mountPath": CONFIG_MOUNT,
                                    "subPath": CONFIG_KEY,
                                    "readOnly": True,
                                },
                                {"name": "logs", "mountPath": "/var/log/squid"},
                                {"name": "spool", "mountPath": "/var/spool/squid"},
                            ],
                            # TCP only: an HTTP probe would need an allowlisted target,
                            # and a probe that depends on the allowlist turns a config
                            # typo into a CrashLoop.
                            "readinessProbe": {
                                "tcpSocket": {"port": port},
                                "initialDelaySeconds": 3,
                                "periodSeconds": 5,
                            },
                            "livenessProbe": {
                                "tcpSocket": {"port": port},
                                "initialDelaySeconds": 15,
                                "periodSeconds": 20,
                            },
                            # This release proves only an IPv4 NetworkPolicy
                            # boundary. Squid's IPv6 destination ACL is defense in
                            # depth, not a reliable isolation primitive, so refuse
                            # a Pod that owns a non-link-local IPv6 address. Merely
                            # having loopback, multicast state, or an eth0
                            # link-local route is normal on IPv4-only clusters.
                            "startupProbe": {
                                "exec": {
                                    "command": [
                                        "/bin/sh",
                                        "-ec",
                                        (
                                            "command -v awk >/dev/null 2>&1 "
                                            "|| exit 1; "
                                            'ipv6_file="${1:-/proc/net/if_inet6}"; '
                                            'if [ -e "$ipv6_file" ]; then '
                                            '[ -r "$ipv6_file" ] || exit 1; '
                                            "awk '{ addr=tolower($1); "
                                            'if ($6 != "lo" '
                                            "&& addr !~ /^fe[89ab]/ "
                                            "&& addr !~ /^ff/) unsafe=1 } "
                                            "END { if (unsafe) exit 1 }' "
                                            '"$ipv6_file"; '
                                            "fi"
                                        ),
                                    ]
                                },
                                "periodSeconds": 5,
                                "failureThreshold": 12,
                            },
                        }
                    ],
                    "volumes": [
                        {"name": "config", "configMap": {"name": CONFIG_MAP}},
                        {"name": "logs", "emptyDir": {"sizeLimit": "64Mi"}},
                        {"name": "spool", "emptyDir": {"sizeLimit": "64Mi"}},
                    ],
                },
            },
        },
    }
    pull_secret = getattr(k, "runner_image_pull_secret", "")
    if pull_secret:
        body["spec"]["template"]["spec"]["imagePullSecrets"] = [{"name": pull_secret}]
    annotations = body["spec"]["template"]["metadata"]["annotations"]
    annotations[PROXY_CONFIG_SHA256_ANNOTATION] = config_sha256(conf)
    if config_revision is not None:
        annotations[PROXY_CONFIG_REVISION_ANNOTATION] = config_revision
    if intent_digest is not None:
        annotations[ISOLATION_INTENT_ANNOTATION] = intent_digest
    annotations[CONFIG_DIGEST_ANNOTATION] = _manifest_digest(body, conf)
    return body
