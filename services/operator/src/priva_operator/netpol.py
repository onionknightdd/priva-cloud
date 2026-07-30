"""Tenant-isolation NetworkPolicy bodies, rendered from the admin settings.

Pure builders — no Kubernetes client, no I/O — so the whole policy set is unit
testable as plain dicts. ``kube.ensure_network_policies`` applies them.

Two things this file exists to get right:

**The runner is hostile too.** The legacy static policy set modelled only Terminal
as tenant-controlled and explicitly let ``app=agent-runner`` through to data-spine
and Redis. Both pod classes execute tenant code; Runner is in fact the default
path and Terminal the opt-in one. Each gets its own flag here.

**Source-side egress, not destination-side ingress.** The legacy policies protected
named destinations ("deny Terminal to Redis"), so anything nobody remembered to
name stayed reachable — which is how ``priva-quota:8099`` ended up open to every
tenant pod. An egress policy on the tenant pod is default-deny: what isn't listed
is denied, so forgetting a destination fails closed.

A NetworkPolicy object only means something if the CNI enforces it. Nothing here
can check that; ``deploy/checks/networkpolicy-cni.sh`` is where that is measured.
"""

from __future__ import annotations

from priva_common.network_isolation import (  # noqa: F401  (re-exported)
    LEGACY_POLICIES,
    MANAGED_LABELS,
    NAME_PREFIX,
    PROXY_POLICY,
    RUNNER_EGRESS,
    RUNNER_INGRESS,
    TERMINAL_EGRESS,
    TERMINAL_INGRESS,
    blocked_egress_cidrs,
    cluster_access_cidrs,
    dns_ip_cidrs,
)

# Control-plane workloads that legitimately dial a tenant pod. Named explicitly
# rather than as "every pod whose app label isn't agent-runner/terminal": the
# legacy set allowed any pod with NO app label at all, which is an implicit grant
# to anything unlabelled that ever lands in the namespace.
_CONTROL_PLANE_APPS = ("control-panel", "channel-connector", "scheduler", "operator")


def _dataspine_port(settings) -> int:
    """data-spine's gRPC port, taken from the DSN the runner is actually given so
    the policy can't drift from the connection string."""
    dsn = getattr(settings.dataspine, "grpc_dsn", "") or ""
    _, _, port = dsn.rpartition(":")
    try:
        return int(port)
    except ValueError:
        return 50051


def _scheduler_port(settings) -> int:
    return int(getattr(getattr(settings, "scheduler", None), "api_port", 8082))


def _peer_selectors(settings) -> list[dict]:
    peers = [{"podSelector": {"matchLabels": {"app": app}}} for app in _CONTROL_PLANE_APPS]
    # The gateway pod carries no plain `app` label — select it by the name the
    # Gateway resource gives its pods.
    peers.append({"podSelector": {
        "matchLabels": {"app.kubernetes.io/name": settings.kubernetes.gateway_name}}})
    return peers


def _dns_rule(settings) -> dict:
    # A bare namespaceSelector:{} grants TCP/UDP 53 to EVERY pod in EVERY
    # namespace, which is a useful exfiltration tunnel. Select the real CoreDNS
    # pods instead; NodeLocal/custom resolvers must be named by CIDR explicitly.
    peers = [{
        "namespaceSelector": {
            "matchLabels": {"kubernetes.io/metadata.name": "kube-system"},
        },
        "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
    }]
    peers.extend({"ipBlock": {"cidr": cidr}} for cidr in dns_ip_cidrs(settings))
    return {
        "to": peers,
        "ports": [{"port": 53, "protocol": "UDP"}, {"port": 53, "protocol": "TCP"}],
    }


def _internal_open_rules(settings) -> list[dict]:
    """Everything in-cluster, for the `deny_internal=False` case. Needed even then
    because an egress policy exists at all (a restricted egress_mode), and adding
    ANY egress rule makes everything unlisted denied."""
    return [{"to": [{"namespaceSelector": {}}]
             + [{"ipBlock": {"cidr": c}} for c in cluster_access_cidrs(settings)]}]


def _internet_rule(settings) -> dict:
    """The public internet, and nothing internal. The except-list is what makes
    that true; without it 0.0.0.0/0 silently re-opens every in-cluster path the
    deny flags just closed."""
    return {
        "to": [{"ipBlock": {
            "cidr": "0.0.0.0/0",
            "except": blocked_egress_cidrs(settings),
        }}],
        # Squid is a TCP forward proxy. Leaving protocols unrestricted gives a
        # compromised proxy process a UDP/SCTP path that bypasses every Squid ACL.
        "ports": [{"protocol": "TCP"}],
    }


def _external_rules(iso, settings) -> list[dict]:
    k = settings.kubernetes
    # The proxy is the only public path in every mode. Under deny_all the
    # connection is still admitted so Squid can return its deterministic 403
    # immediately; the proxy's own NetworkPolicy has no public-internet egress in
    # that mode. Keeping this peer stable also avoids turning a policy toggle into
    # a Runner/Terminal template rollout. A tenant can unset HTTPS_PROXY, but then
    # the direct connection is simply denied.
    return [{
        "to": [{"podSelector": {"matchLabels": {"app": "egress-proxy"}}}],
        "ports": [{"port": k.egress_proxy_port, "protocol": "TCP"}],
    }]


def _tenant_selector(app: str) -> dict:
    # Stable across modes. Selecting only pods carrying a newly-added "proxied"
    # label creates a fail-open upgrade window: existing pods remain completely
    # unpoliced until they restart.
    return {"matchLabels": {"app": app}}


def _egress_body(name, namespace, app, iso, settings, *, deny_internal, allow_dataspine):
    rules = [_dns_rule(settings)]
    if deny_internal:
        if allow_dataspine:
            rules.append({
                "to": [{"podSelector": {"matchLabels": {"app": "data-spine"}}}],
                "ports": [{"port": _dataspine_port(settings), "protocol": "TCP"}],
            })
            # Run-now/scheduled-run control calls are an explicit runner
            # dependency. They intentionally bypass the public proxy.
            rules.append({
                "to": [{"podSelector": {"matchLabels": {"app": "scheduler"}}}],
                "ports": [{"port": _scheduler_port(settings), "protocol": "TCP"}],
            })
    else:
        rules.extend(_internal_open_rules(settings))
    # This is the proxy rule in every mode. Under deny_internal it is the ONLY
    # thing that makes the proxy reachable — no broader in-cluster rule exists.
    rules.extend(_external_rules(iso, settings))
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": name, "namespace": namespace, "labels": dict(MANAGED_LABELS)},
        "spec": {
            "podSelector": _tenant_selector(app),
            "policyTypes": ["Egress"],
            "egress": rules,
        },
    }


def _ingress_body(name, namespace, app, port, settings, *, deny_tenant_peers):
    # Helm installs an empty-ingress baseline so deleting the dynamic policy
    # fails closed. When the admin turns peer isolation off, this owned policy
    # explicitly restores Kubernetes' ordinary allow-all ingress semantics.
    ingress = (
        [{"from": _peer_selectors(settings),
          "ports": [{"port": port, "protocol": "TCP"}]}]
        if deny_tenant_peers
        else [{}]
    )
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": name, "namespace": namespace, "labels": dict(MANAGED_LABELS)},
        "spec": {
            "podSelector": {"matchLabels": {"app": app}},
            "policyTypes": ["Ingress"],
            "ingress": ingress,
        },
    }


def _proxy_body(namespace, iso, settings):
    """The proxy is the one pod allowed out, which makes it the one pod that must
    NOT be allowed back in. Without this an admin who allowlists a wildcard — or a
    squid ACL bug — turns the proxy into a confused deputy: the tenant just asks it
    to CONNECT to data-spine.priva-cloud.svc:50051 and the L3/L4 boundary is moot.
    """
    k = settings.kubernetes
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": PROXY_POLICY, "namespace": namespace,
                     "labels": dict(MANAGED_LABELS)},
        "spec": {
            "podSelector": {"matchLabels": {"app": "egress-proxy"}},
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [{
                "from": [{"podSelector": {"matchLabels": {"app": "agent-runner"}}},
                         {"podSelector": {"matchLabels": {"app": "terminal"}}}],
                "ports": [{"port": k.egress_proxy_port, "protocol": "TCP"}],
            }],
            # DNS is available only in the two modes which may resolve public
            # destinations. deny_all has an empty egress list: Squid can return
            # its local 403 without DNS, and even a compromised proxy process
            # then has no recursive-DNS exfiltration path.
            "egress": (
                [_dns_rule(settings), _internet_rule(settings)]
                if iso.egress_mode in {"allowlist", "unrestricted"}
                else []
            ),
        },
    }


def proxy_env(iso, settings) -> list[dict]:
    """Proxy environment for a tenant pod, as a k8s ``env`` list.

    Deterministic — the operator re-renders the pod template on every converge and
    replaces the Deployment, so a value that varied per render would restart every
    dormant runner every 10 seconds.

    Present in every mode so changing a mode never requires replacing every
    Runner/Terminal pod. Under ``deny_all`` tenants can still reach Squid for a
    prompt policy denial, while the proxy itself has no public-internet egress.
    """
    k = settings.kubernetes
    url = f"http://{k.egress_proxy_host}:{k.egress_proxy_port}"
    # Both cases: clients disagree. curl and requests read the lowercase form,
    # Go and the bundled claude CLI read the uppercase one.
    return [
        {"name": "HTTP_PROXY", "value": url},
        {"name": "HTTPS_PROXY", "value": url},
        {"name": "http_proxy", "value": url},
        {"name": "https_proxy", "value": url},
        {"name": "NO_PROXY", "value": k.egress_no_proxy},
        {"name": "no_proxy", "value": k.egress_no_proxy},
    ]


def build_policies(iso, settings, namespace: str) -> list[dict]:
    """The full desired policy set for the current settings.

    Runner and Terminal egress plus the proxy boundary are always present. The
    flags change only the allow rules, never whether a tenant pod is selected.
    That invariant prevents mode transitions and upgrades from creating an
    unpoliced pod population.
    """
    k = settings.kubernetes
    out: list[dict] = [
        _egress_body(
            RUNNER_EGRESS, namespace, "agent-runner", iso, settings,
            deny_internal=iso.runner_deny_internal, allow_dataspine=True),
        # No data-spine allow: terminald deliberately carries no data-spine
        # credentials (kube.py "Deliberately no envFrom"), so a Terminal that can
        # reach it is reaching it as an attacker, not as a client.
        _egress_body(
            TERMINAL_EGRESS, namespace, "terminal", iso, settings,
            deny_internal=iso.terminal_deny_internal, allow_dataspine=False),
    ]

    out.append(_ingress_body(
        RUNNER_INGRESS,
        namespace,
        "agent-runner",
        k.runner_service_port,
        settings,
        deny_tenant_peers=iso.deny_tenant_peers,
    ))
    out.append(_ingress_body(
        TERMINAL_INGRESS,
        namespace,
        "terminal",
        k.terminal_service_port,
        settings,
        deny_tenant_peers=iso.deny_tenant_peers,
    ))

    out.append(_proxy_body(namespace, iso, settings))

    return out
