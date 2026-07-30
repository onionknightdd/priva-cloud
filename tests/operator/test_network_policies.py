"""Tenant-isolation NetworkPolicy rendering.

These assert *behaviour*, not manifest shape: `_permits` is a small evaluator that
answers "would this policy let the tenant pod reach X?". Shape assertions pass
just as happily on a policy that allows everything — the legacy static set looked
correct and left `priva-quota:8099` open to every tenant, which is the bug class
this file exists to catch.
"""

from __future__ import annotations

import ipaddress
from types import SimpleNamespace

import pytest

from priva_common.config import KubernetesSettings
from priva_operator import kube, netpol

NS = "priva-cloud"

# Everything internal. Measured on the dev cluster: pod 10.244.x, service 10.96.x,
# and the NODE at 192.168.49.2 — which a Service ClusterIP DNATs to, and which the
# original pod+service-only except-list left wide open.
INTERNAL_CIDRS = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                  "169.254.0.0/16", "127.0.0.0/8"]


def _settings():
    return SimpleNamespace(
        kubernetes=SimpleNamespace(
            egress_internal_cidrs=INTERNAL_CIDRS,
            egress_proxy_host="priva-egress-proxy.priva-cloud.svc",
            egress_proxy_port=3128,
            gateway_name="priva-gateway",
            runner_service_port=8091,
            terminal_service_port=8092,
        ),
        dataspine=SimpleNamespace(grpc_dsn="data-spine.priva-cloud.svc.cluster.local:50051"),
        scheduler=SimpleNamespace(api_port=8082),
    )


def _iso(**kw):
    base = dict(runner_deny_internal=False, terminal_deny_internal=False,
                deny_tenant_peers=False, egress_mode="unrestricted", egress_allowlist=[])
    base.update(kw)
    return SimpleNamespace(**base)


def _build(**kw):
    return {p["metadata"]["name"]: p for p in netpol.build_policies(_iso(**kw), _settings(), NS)}


# --- a minimal NetworkPolicy evaluator ---------------------------------------

def _selector_matches(sel: dict, labels: dict) -> bool:
    for k, v in (sel.get("matchLabels") or {}).items():
        if labels.get(k) != v:
            return False
    for expr in sel.get("matchExpressions") or []:
        key, op, values = expr["key"], expr["operator"], expr.get("values", [])
        if op == "In" and labels.get(key) not in values:
            return False
        if op == "NotIn" and labels.get(key) in values:
            return False
        if op == "Exists" and key not in labels:
            return False
        if op == "DoesNotExist" and key in labels:
            return False
    return True


def _peer_matches(peer: dict, labels: dict, ip: str | None) -> bool:
    if "ipBlock" in peer:
        if ip is None:
            return False
        block = peer["ipBlock"]
        addr = ipaddress.ip_address(ip)
        if addr not in ipaddress.ip_network(block["cidr"]):
            return False
        return not any(addr in ipaddress.ip_network(x) for x in block.get("except", []))
    if "podSelector" in peer:
        # namespaceSelector+podSelector combos aren't used here; a bare
        # namespaceSelector means "any pod in any namespace".
        return _selector_matches(peer["podSelector"], labels)
    if "namespaceSelector" in peer:
        return True
    return False


def _permits(
    policy: dict,
    direction: str,
    *,
    labels=None,
    ip=None,
    port: int,
    protocol: str = "TCP",
) -> bool:
    for rule in policy["spec"].get(direction, []):
        ports = rule.get("ports")
        if ports and not any(
            ("port" not in p or p["port"] == port)
            and p.get("protocol", "TCP") == protocol
            for p in ports
        ):
            continue
        peers = rule.get("to" if direction == "egress" else "from")
        if peers is None:  # no peer restriction = everyone
            return True
        if any(_peer_matches(p, labels or {}, ip) for p in peers):
            return True
    return False


# --- the policy set -----------------------------------------------------------

def test_open_admin_posture_still_has_a_stable_egress_boundary():
    # "Unrestricted" controls what Squid allows; it must not mean "unpoliced".
    # Static empty baselines make deletion fail closed, so the dynamic ingress
    # policies remain present and explicitly restore allow-all when disabled.
    assert set(_build()) == {
        netpol.RUNNER_EGRESS,
        netpol.TERMINAL_EGRESS,
        netpol.RUNNER_INGRESS,
        netpol.TERMINAL_INGRESS,
        netpol.PROXY_POLICY,
    }
    assert _build()[netpol.RUNNER_INGRESS]["spec"]["ingress"] == [{}]
    assert _build()[netpol.TERMINAL_INGRESS]["spec"]["ingress"] == [{}]


def test_runner_deny_internal_blocks_the_undeclared_in_cluster_surface():
    pol = _build(runner_deny_internal=True)[netpol.RUNNER_EGRESS]
    # The one dependency the runner actually has.
    assert _permits(pol, "egress", labels={"app": "data-spine"}, port=50051)
    # Everything the legacy destination-side policies forgot.
    assert not _permits(pol, "egress", labels={"app": "priva-nfs"}, port=8099)
    assert not _permits(pol, "egress", labels={"app": "redis"}, port=6379)
    assert not _permits(pol, "egress", labels={"app": "postgres"}, port=5432)
    assert not _permits(pol, "egress", labels={"app": "control-panel"}, port=9000)
    assert not _permits(pol, "egress", labels={"app": "agent-runner"}, port=8091)
    assert not _permits(pol, "egress", labels={"app": "terminal"}, port=8092)


def test_dns_survives_every_mode():
    # Without :53 every other allow is dead — the pod can't resolve the name it
    # was permitted to reach, and the failure looks like a hung agent.
    for kw in ({"runner_deny_internal": True},
               {"runner_deny_internal": True, "egress_mode": "allowlist"},
               {"runner_deny_internal": True, "egress_mode": "deny_all"}):
        pol = _build(**kw)[netpol.RUNNER_EGRESS]
        assert _permits(pol, "egress", labels={"k8s-app": "kube-dns"}, port=53), kw
        # DNS is restricted to labelled CoreDNS, not every tenant-controlled
        # pod which happens to listen on port 53.
        assert not _permits(pol, "egress", labels={"app": "agent-runner"}, port=53), kw


def test_unrestricted_egress_is_still_forced_through_the_proxy():
    policies = _build(runner_deny_internal=True)
    pol = policies[netpol.RUNNER_EGRESS]
    assert _permits(pol, "egress", labels={"app": "egress-proxy"}, port=3128)
    assert not _permits(pol, "egress", ip="140.82.121.4", port=443)  # direct bypass
    assert not _permits(pol, "egress", ip="10.96.0.1", port=443)      # apiserver ClusterIP
    assert not _permits(pol, "egress", ip="10.244.1.7", port=6379)    # a pod IP
    # Measured leak: a ClusterIP is DNATed to its real endpoint, and the
    # apiserver's endpoint is the NODE — outside both the pod and service CIDRs.
    # kubernetes.default stayed reachable from a pod with in-cluster egress denied.
    assert not _permits(pol, "egress", ip="192.168.49.2", port=8443)   # node / apiserver
    # Cloud metadata hands out instance credentials on most providers.
    assert not _permits(pol, "egress", ip="169.254.169.254", port=80)
    proxy = policies[netpol.PROXY_POLICY]
    assert _permits(proxy, "egress", ip="140.82.121.4", port=443)
    assert not _permits(
        proxy, "egress", ip="140.82.121.4", port=53, protocol="UDP"
    )
    assert not _permits(proxy, "egress", ip="169.254.169.254", port=80)


def test_allowlist_mode_routes_everything_through_the_proxy():
    pol = _build(runner_deny_internal=True, egress_mode="allowlist")[netpol.RUNNER_EGRESS]
    assert _permits(pol, "egress", labels={"app": "egress-proxy"}, port=3128)
    # Direct internet must be gone, or the proxy is advisory and HTTPS_PROXY is
    # just an env var the agent's bash can unset.
    assert not _permits(pol, "egress", ip="140.82.121.4", port=443)


def test_deny_all_routes_to_squid_for_fast_rejection_but_not_the_internet():
    policies = _build(runner_deny_internal=True, egress_mode="deny_all")
    pol = policies[netpol.RUNNER_EGRESS]
    assert not _permits(pol, "egress", ip="140.82.121.4", port=443)
    assert _permits(pol, "egress", labels={"app": "egress-proxy"}, port=3128)
    assert _permits(pol, "egress", labels={"app": "data-spine"}, port=50051)
    assert _permits(pol, "egress", labels={"app": "scheduler"}, port=8082)

    # Squid can answer locally, but deny_all removes every proxy egress path,
    # including recursive DNS as a possible exfiltration channel.
    proxy = policies[netpol.PROXY_POLICY]
    assert not _permits(
        proxy, "egress", labels={"k8s-app": "kube-dns"}, port=53
    )
    assert not _permits(proxy, "egress", ip="140.82.121.4", port=443)


def test_restricted_egress_alone_keeps_the_cluster_reachable():
    # deny_internal off but a restricted mode on: the egress object now exists, so
    # everything unlisted is denied — the in-cluster allow must be explicit or the
    # runner loses data-spine as a side effect of an egress-only setting.
    pol = _build(egress_mode="allowlist")[netpol.RUNNER_EGRESS]
    assert _permits(pol, "egress", labels={"app": "data-spine"}, port=50051)
    assert _permits(pol, "egress", ip="10.96.0.1", port=443)


def test_terminal_never_reaches_data_spine():
    # terminald deliberately carries no data-spine credentials, so a Terminal that
    # can reach it is reaching it as an attacker, not as a client.
    pol = _build(terminal_deny_internal=True)[netpol.TERMINAL_EGRESS]
    assert not _permits(pol, "egress", labels={"app": "data-spine"}, port=50051)
    assert _permits(pol, "egress", labels={"k8s-app": "kube-dns"}, port=53)


def test_tenant_peer_ingress_admits_control_plane_but_not_tenants():
    pols = _build(deny_tenant_peers=True)
    runner = pols[netpol.RUNNER_INGRESS]
    for app in ("control-panel", "channel-connector", "scheduler", "operator"):
        assert _permits(runner, "ingress", labels={"app": app}, port=8091), app
    assert _permits(runner, "ingress",
                    labels={"app.kubernetes.io/name": "priva-gateway"}, port=8091)
    assert not _permits(runner, "ingress", labels={"app": "agent-runner"}, port=8091)
    assert not _permits(runner, "ingress", labels={"app": "terminal"}, port=8091)
    # An unlabelled pod is NOT an implicit peer. The legacy policy allowed every
    # pod with no `app` label, to let the gateway in; the gateway is now named.
    assert not _permits(runner, "ingress", labels={}, port=8091)

    terminal = pols[netpol.TERMINAL_INGRESS]
    assert _permits(terminal, "ingress",
                    labels={"app.kubernetes.io/name": "priva-gateway"}, port=8092)
    # The whole terminald auth story is one constant header, so this rule is the
    # only thing standing between a tenant runner and another tenant's shell.
    assert not _permits(terminal, "ingress", labels={"app": "agent-runner"}, port=8092)
    assert not _permits(terminal, "ingress", labels={"app": "terminal"}, port=8092)


def test_dataspine_port_follows_the_dsn():
    s = _settings()
    s.dataspine.grpc_dsn = "data-spine.other.svc:15051"
    pol = [p for p in netpol.build_policies(_iso(runner_deny_internal=True), s, NS)
           if p["metadata"]["name"] == netpol.RUNNER_EGRESS][0]
    assert _permits(pol, "egress", labels={"app": "data-spine"}, port=15051)
    assert not _permits(pol, "egress", labels={"app": "data-spine"}, port=50051)


def test_scheduler_is_the_only_other_runner_control_plane_dependency():
    pol = _build(runner_deny_internal=True)[netpol.RUNNER_EGRESS]
    assert _permits(pol, "egress", labels={"app": "scheduler"}, port=8082)
    assert not _permits(pol, "egress", labels={"app": "scheduler"}, port=8080)
    assert not _permits(pol, "egress", labels={"app": "control-panel"}, port=8082)


def test_split_namespaces_fail_fast_until_peers_and_rbac_support_them():
    with pytest.raises(ValueError, match="separate system/tenant namespaces"):
        KubernetesSettings(
            namespace_system="priva-system",
            namespace_tenants="priva-tenants",
        )


def test_missing_cluster_topology_fails_before_the_operator_can_render():
    with pytest.raises(ValueError, match="cluster_node_cidrs"):
        KubernetesSettings(cluster_node_cidrs=[])


def test_config_cannot_remove_builtin_private_and_metadata_exclusions():
    settings = _settings()
    # A deployment override replaces Pydantic list defaults. The security floor
    # must still be code-owned so a typo cannot leave a compromised proxy free
    # to dial metadata or RFC1918 space directly.
    settings.kubernetes.egress_internal_cidrs = ["203.0.113.7/32"]
    blocked = netpol.blocked_egress_cidrs(settings)
    assert "10.0.0.0/8" in blocked
    assert "127.0.0.0/8" in blocked
    assert "169.254.0.0/16" in blocked
    assert "100.64.0.0/10" in blocked


# --- the applier --------------------------------------------------------------

class _FakeNetworking:
    def __init__(self, existing=None):
        self.store = {p["metadata"]["name"]: _obj(p) for p in (existing or [])}
        self.creates = self.replaces = self.deletes = 0

    def list_namespaced_network_policy(
        self, ns, label_selector=None, **_kwargs
    ):
        want = dict(x.split("=", 1) for x in label_selector.split(",")) if label_selector else {}
        items = [o for o in self.store.values()
                 if all((o.metadata.labels or {}).get(k) == v for k, v in want.items())]
        return SimpleNamespace(items=items)

    def read_namespaced_network_policy(self, name, ns, **_kwargs):
        if name not in self.store:
            raise kube.client.ApiException(status=404)
        return self.store[name]

    def create_namespaced_network_policy(self, ns, body, **_kwargs):
        self.creates += 1
        self.store[body["metadata"]["name"]] = _obj(body)

    def replace_namespaced_network_policy(self, name, ns, body, **_kwargs):
        self.replaces += 1
        self.store[name] = _obj(body)

    def delete_namespaced_network_policy(self, name, ns, **_kwargs):
        if name not in self.store:
            raise kube.client.ApiException(status=404)
        self.deletes += 1
        del self.store[name]


def _obj(body):
    m = body["metadata"]
    return SimpleNamespace(
        metadata=SimpleNamespace(name=m["name"], labels=m.get("labels") or {},
                                 annotations=m.get("annotations") or {},
                                 resource_version="1"),
        spec=body["spec"])


@pytest.fixture
def wire(monkeypatch):
    def _wire(iso, existing=None):
        fake = _FakeNetworking(existing)
        monkeypatch.setattr(kube, "networking", lambda: fake)
        monkeypatch.setattr("priva_common.config.get_settings", lambda: _settings())
        monkeypatch.setattr(
            "priva_common.dataplane.get_client",
            lambda: SimpleNamespace(network_isolation=SimpleNamespace(get=lambda: iso)))
        return fake
    return _wire


def test_ensure_creates_then_skips_on_unchanged_digest(wire):
    fake = wire(_iso(runner_deny_internal=True, deny_tenant_peers=True))
    assert kube.ensure_network_policies(NS) is True
    assert fake.creates == 5  # runner+terminal+proxy egress + both ingress
    assert kube.ensure_network_policies(NS) is False
    assert fake.replaces == 0


def test_ensure_reopens_ingress_without_deleting_the_dynamic_policy(wire):
    fake = wire(_iso(runner_deny_internal=True, deny_tenant_peers=True))
    kube.ensure_network_policies(NS)
    # The independent static baseline is empty-ingress. Turning this switch off
    # must therefore converge the dynamic policy to an explicit allow-all rule;
    # deleting it would leave the static baseline denying all ingress.
    fake2 = wire(_iso(runner_deny_internal=True))
    fake2.store = fake.store
    assert kube.ensure_network_policies(NS) is True
    assert fake2.store[netpol.RUNNER_INGRESS].spec["ingress"] == [{}]
    assert fake2.store[netpol.TERMINAL_INGRESS].spec["ingress"] == [{}]
    assert netpol.RUNNER_EGRESS in fake2.store


def test_ensure_prunes_the_superseded_static_policies(wire):
    legacy = [{"metadata": {"name": n, "labels": {}}, "spec": {}}
              for n in netpol.LEGACY_POLICIES]
    fake = wire(_iso(runner_deny_internal=True), existing=legacy)
    kube.ensure_network_policies(NS)
    # Policies UNION their allow rules, so leaving the old permissive set alongside
    # the new strict one would quietly widen it.
    for name in netpol.LEGACY_POLICIES:
        assert name not in fake.store


def test_ensure_prunes_legacy_even_when_nothing_else_changed(wire):
    fake = wire(_iso(runner_deny_internal=True))
    kube.ensure_network_policies(NS)
    fake.store["redis-deny-terminal"] = _obj(
        {"metadata": {"name": "redis-deny-terminal", "labels": {}}, "spec": {}})
    # Second pass is a digest no-op for the managed set; the prune must still run,
    # or a policy re-applied by an old up.sh would survive forever.
    assert kube.ensure_network_policies(NS) is False
    assert "redis-deny-terminal" not in fake.store


@pytest.mark.parametrize(
    ("name", "spec"),
    [
        (
            "tenant-egress-open",
            {
                "podSelector": {"matchLabels": {"app": "agent-runner"}},
                "policyTypes": ["Egress"],
                "egress": [{}],
            },
        ),
        (
            "tenant-ingress-open",
            {
                "podSelector": {
                    "matchExpressions": [{
                        "key": "app",
                        "operator": "In",
                        "values": ["terminal", "unrelated"],
                    }],
                },
                "policyTypes": ["Ingress"],
                "ingress": [{}],
            },
        ),
        (
            "proxy-egress-open",
            {
                "podSelector": {},
                "policyTypes": ["Egress"],
                "egress": [{"to": [{"namespaceSelector": {}}]}],
            },
        ),
    ],
)
def test_unknown_union_allow_fails_closed_and_is_not_deleted(wire, name, spec):
    body = {
        "metadata": {
            "name": name,
            # Even copied operator labels are not deletion authority for an
            # otherwise unknown object name.
            "labels": dict(netpol.MANAGED_LABELS),
        },
        "spec": spec,
    }
    fake = wire(_iso(runner_deny_internal=True), existing=[body])

    with pytest.raises(kube.IsolationConflictError) as raised:
        kube.ensure_network_policies(NS)

    assert raised.value.policy_names == (name,)
    assert name in str(raised.value)
    assert name in fake.store
    assert fake.creates == 0
    assert fake.replaces == 0
    assert fake.deletes == 0


def test_empty_static_baselines_and_unrelated_allows_do_not_conflict(wire):
    baseline = {
        "metadata": {"name": "priva-tenant-runner-baseline", "labels": {}},
        "spec": {
            "podSelector": {"matchLabels": {"app": "agent-runner"}},
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [],
            "egress": [],
        },
    }
    unrelated = {
        "metadata": {"name": "postgres-maintenance", "labels": {}},
        "spec": {
            "podSelector": {"matchLabels": {"app": "postgres"}},
            "policyTypes": ["Ingress"],
            "ingress": [{}],
        },
    }
    fake = wire(
        _iso(runner_deny_internal=True),
        existing=[baseline, unrelated],
    )

    assert kube.ensure_network_policies(NS) is True
    assert baseline["metadata"]["name"] in fake.store
    assert unrelated["metadata"]["name"] in fake.store


def test_widening_legacy_policy_is_pruned_before_conflict_check(wire):
    legacy_name = netpol.LEGACY_POLICIES[0]
    legacy = {
        "metadata": {"name": legacy_name, "labels": {}},
        "spec": {
            "podSelector": {"matchLabels": {"app": "agent-runner"}},
            "policyTypes": ["Egress"],
            "egress": [{}],
        },
    }
    fake = wire(_iso(runner_deny_internal=True), existing=[legacy])

    assert kube.ensure_network_policies(NS) is True
    assert legacy_name not in fake.store


def test_ensure_repairs_spec_drift_even_when_digest_annotation_was_left_intact(wire):
    fake = wire(_iso(runner_deny_internal=True))
    kube.ensure_network_policies(NS)
    # Simulate an out-of-band widening which leaves the operator's annotation in
    # place. A digest-only fast path would report this as healthy forever.
    fake.store[netpol.RUNNER_EGRESS].spec["egress"].append({
        "to": [{"ipBlock": {"cidr": "0.0.0.0/0"}}],
    })
    assert kube.ensure_network_policies(NS) is True
    assert fake.replaces >= 1
    assert {"to": [{"ipBlock": {"cidr": "0.0.0.0/0"}}]} not in \
        fake.store[netpol.RUNNER_EGRESS].spec["egress"]


def test_ensure_repairs_same_named_policy_after_managed_labels_are_removed(wire):
    fake = wire(_iso(runner_deny_internal=True))
    kube.ensure_network_policies(NS)
    policy = fake.store[netpol.RUNNER_EGRESS]
    policy.metadata.labels = {}
    policy.spec["egress"].append({
        "to": [{"ipBlock": {"cidr": "0.0.0.0/0"}}],
    })

    assert kube.ensure_network_policies(NS) is True
    assert fake.replaces >= 1
    repaired = fake.store[netpol.RUNNER_EGRESS]
    assert all(
        repaired.metadata.labels.get(key) == value
        for key, value in netpol.MANAGED_LABELS.items()
    )
    assert {"to": [{"ipBlock": {"cidr": "0.0.0.0/0"}}]} not in repaired.spec[
        "egress"
    ]


@pytest.mark.parametrize("operation", ["create", "replace"])
def test_policy_write_conflict_is_not_treated_as_applied(wire, operation):
    fake = wire(_iso(runner_deny_internal=True))
    if operation == "replace":
        kube.ensure_network_policies(NS)
        fake.store[netpol.RUNNER_EGRESS].spec["egress"].append({
            "to": [{"ipBlock": {"cidr": "0.0.0.0/0"}}],
        })

    def conflict(*_args, **_kwargs):
        raise kube.client.ApiException(status=409)

    if operation == "create":
        fake.create_namespaced_network_policy = conflict
    else:
        fake.replace_namespaced_network_policy = conflict

    with pytest.raises(kube.client.ApiException) as raised:
        kube.ensure_network_policies(
            NS,
            strict=True,
            iso=_iso(runner_deny_internal=True),
            settings=_settings(),
        )
    assert raised.value.status == 409


def test_ensure_advances_intent_when_policy_specs_happen_to_be_equal(wire):
    # unrestricted and allowlist both route tenants only to the proxy, so their
    # NetworkPolicy specs are identical; the real permission change is in Squid.
    # The generation annotation must still advance or status reports the old
    # unrestricted proxy as applied to the new allowlist intent.
    old = wire(_iso(egress_mode="unrestricted"))
    kube.ensure_network_policies(NS)

    new = wire(_iso(egress_mode="allowlist"))
    new.store = old.store
    assert kube.ensure_network_policies(NS) is True
    assert new.replaces == 5


def test_ensure_is_fail_soft_by_default_and_loud_under_strict(monkeypatch):
    def boom():
        raise RuntimeError("data-spine unreachable")
    monkeypatch.setattr("priva_common.dataplane.get_client", boom)
    assert kube.ensure_network_policies(NS) is False
    with pytest.raises(RuntimeError):
        kube.ensure_network_policies(NS, strict=True)
