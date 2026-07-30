"""The admin Isolation API.

The panel this serves replaced one whose value was a client-side literal — never
fetched, never saved — under a hint asserting an enforcement that was not
installed anywhere. So the properties worth testing are the ones that make the
new surface incapable of the same lie: that `applied` is measured separately from
`desired`, that "cannot look" is distinct from "nothing is in force", and that a
blank allowlist host is rejected before it can reach squid.
"""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from priva_common import network_isolation as ni
from priva_control_panel.services import isolation_status


def _record(**kw):
    base = dict(runner_deny_internal=False, terminal_deny_internal=False,
                deny_tenant_peers=False, egress_mode="unrestricted",
                egress_allowlist=[], updated_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def cluster(monkeypatch):
    """Fake the three cluster reads independently — they fail independently in
    production too (RBAC on one, absent object on another)."""
    state = {"policies": set(), "conflicts": [], "proxy": None, "facts": None}

    monkeypatch.setattr(
        isolation_status,
        "_present_policies",
        lambda ns, expected_intent: (
            state["policies"],
            state["conflicts"],
        ),
    )
    monkeypatch.setattr(
        isolation_status,
        "_proxy_health",
        lambda ns, expected_intent: state["proxy"]
        or {
            "present": False,
            "ready": 0,
            "desired": 0,
            "applied": False,
        },
    )
    monkeypatch.setattr(isolation_status, "_enforcement",
                        lambda ns: state["facts"] or {"state": "unknown", "checked_at": None, "cni": None})
    monkeypatch.setattr("priva_common.config.get_settings",
                        lambda: SimpleNamespace(kubernetes=SimpleNamespace(namespace_tenants="priva-cloud")))
    monkeypatch.setattr(
        ni, "isolation_intent_digest", lambda record, settings: "expected-intent"
    )
    return state


def _by_key(status):
    return {b["key"]: b for b in status["boundaries"]}


def test_desired_without_applied_is_reported_as_drift(cluster):
    cluster["policies"] = set()  # operator never wrote it
    status = isolation_status.collect(_record(runner_deny_internal=True))
    b = _by_key(status)["runner_deny_internal"]
    # The settings row still reads exactly as the admin left it, so without this
    # split an operator that is down (or lost its networkpolicies RBAC) looks
    # indistinguishable from a working one.
    assert b["desired"] is True and b["applied"] is False


def test_applied_is_true_once_the_object_exists(cluster):
    cluster["policies"] = {ni.RUNNER_EGRESS}
    b = _by_key(isolation_status.collect(_record(runner_deny_internal=True)))["runner_deny_internal"]
    assert b["desired"] is True and b["applied"] is True


def test_same_named_but_unmanaged_policy_is_not_reported_as_applied(cluster):
    cluster["policies"] = {ni.RUNNER_EGRESS: False}
    b = _by_key(
        isolation_status.collect(_record(runner_deny_internal=True))
    )["runner_deny_internal"]
    assert b["applied"] is False


def test_live_policy_spec_must_match_its_stamped_set_digest(monkeypatch):
    specs = {
        ni.RUNNER_EGRESS: {
            "podSelector": {"matchLabels": {"app": "agent-runner"}},
            "policyTypes": ["Egress"],
            "egress": [],
        },
        ni.TERMINAL_EGRESS: {
            "podSelector": {"matchLabels": {"app": "terminal"}},
            "policyTypes": ["Egress"],
            "egress": [],
        },
        ni.PROXY_POLICY: {
            "podSelector": {"matchLabels": {"app": "egress-proxy"}},
            "policyTypes": ["Egress"],
            "egress": [],
        },
    }
    digest = hashlib.sha256(
        json.dumps([specs[name] for name in sorted(specs)], sort_keys=True).encode()
    ).hexdigest()[:16]

    def policy(name):
        return SimpleNamespace(
            metadata=SimpleNamespace(
                name=name,
                labels=dict(ni.MANAGED_LABELS),
                annotations={
                    ni.POLICY_SET_DIGEST_ANNOTATION: digest,
                    ni.ISOLATION_INTENT_ANNOTATION: "current-intent",
                },
            ),
            spec=specs[name],
        )

    api = SimpleNamespace(
        list_namespaced_network_policy=lambda _: SimpleNamespace(
            items=[policy(name) for name in specs]
        )
    )
    monkeypatch.setattr(isolation_status, "_networking", lambda: api)
    present, conflicts = isolation_status._present_policies(
        "priva-cloud", "current-intent"
    )
    assert conflicts == []
    assert all(
        present.values()
    )

    # An old generation can be perfectly self-consistent. It is still not
    # applied to the admin's current record.
    present, conflicts = isolation_status._present_policies(
        "priva-cloud", "new-intent"
    )
    assert conflicts == []
    assert not any(
        present.values()
    )

    # Preserve name, labels and annotation while widening the live spec: this
    # was the status-path bypass the prior name/digest-presence check missed.
    specs[ni.RUNNER_EGRESS]["egress"] = [{"to": [{"namespaceSelector": {}}]}]
    present, conflicts = isolation_status._present_policies(
        "priva-cloud", "current-intent"
    )
    assert conflicts == []
    assert present[ni.RUNNER_EGRESS] is False


def test_tenant_peers_needs_both_policies(cluster):
    cluster["policies"] = {ni.RUNNER_INGRESS}  # half-applied
    b = _by_key(isolation_status.collect(_record(deny_tenant_peers=True)))["deny_tenant_peers"]
    assert b["applied"] is False


def test_cannot_look_is_not_the_same_as_nothing_in_force(cluster, monkeypatch):
    monkeypatch.setattr(
        isolation_status,
        "_present_policies",
        lambda ns, expected_intent: None,
    )
    status = isolation_status.collect(_record(runner_deny_internal=True))
    # None, not False: a missing RBAC rule must not render as a confident claim
    # that the boundary is absent.
    assert _by_key(status)["runner_deny_internal"]["applied"] is None
    assert status["legacy_present"] is None
    assert status["conflicting_policies"] is None


def test_additional_allow_policy_marks_tenant_boundaries_not_applied(cluster):
    cluster["policies"] = {
        ni.RUNNER_EGRESS,
        ni.TERMINAL_EGRESS,
        ni.RUNNER_INGRESS,
        ni.TERMINAL_INGRESS,
        ni.PROXY_POLICY,
        ni.POSTGRES_POLICY,
    }
    cluster["conflicts"] = ["tenant-egress-open"]
    cluster["proxy"] = {
        "present": True,
        "ready": 2,
        "desired": 2,
        "applied": True,
    }

    status = isolation_status.collect(_record(
        runner_deny_internal=True,
        deny_tenant_peers=True,
        egress_mode="allowlist",
    ))
    rows = _by_key(status)
    assert rows["runner_deny_internal"]["applied"] is False
    assert rows["deny_tenant_peers"]["applied"] is False
    assert rows["egress"]["applied"] is False
    # The conflict selects tenant/proxy pods, not PostgreSQL. Keep the separate
    # destination-side measurement honest.
    assert rows["postgres"]["applied"] is True
    assert status["conflicting_policies"] == ["tenant-egress-open"]


def test_live_inventory_detects_unknown_union_allow_but_not_empty_baseline(
    monkeypatch,
):
    def policy(name, spec, *, labels=None):
        return SimpleNamespace(
            metadata=SimpleNamespace(
                name=name,
                labels=labels or {},
                annotations={},
            ),
            spec=spec,
        )

    api = SimpleNamespace(
        list_namespaced_network_policy=lambda _: SimpleNamespace(items=[
            policy(
                "tenant-egress-open",
                {
                    "podSelector": {
                        "matchExpressions": [{
                            "key": "app",
                            "operator": "In",
                            "values": ["agent-runner"],
                        }],
                    },
                    "policyTypes": ["Egress"],
                    "egress": [{}],
                },
            ),
            policy(
                ni.RUNNER_BASELINE,
                {
                    "podSelector": {"matchLabels": {"app": "agent-runner"}},
                    "policyTypes": ["Ingress", "Egress"],
                    "ingress": [],
                    "egress": [],
                },
            ),
            policy(
                "unrelated-allow",
                {
                    "podSelector": {"matchLabels": {"app": "postgres"}},
                    "policyTypes": ["Ingress"],
                    "ingress": [{}],
                },
            ),
        ])
    )
    monkeypatch.setattr(isolation_status, "_networking", lambda: api)

    present, conflicts = isolation_status._present_policies(
        "priva-cloud", "current-intent"
    )
    assert set(present) == {
        "tenant-egress-open",
        ni.RUNNER_BASELINE,
        "unrelated-allow",
    }
    assert conflicts == ["tenant-egress-open"]


def test_conflicts_survive_the_api_response_model():
    from priva_common.models.admin import NetworkIsolationStatus

    status = NetworkIsolationStatus.model_validate({
        "conflicting_policies": ["tenant-egress-open"],
    })
    assert status.model_dump()["conflicting_policies"] == ["tenant-egress-open"]
    assert NetworkIsolationStatus().conflicting_policies == []


def test_enforcement_is_unknown_until_the_probe_has_run(cluster):
    status = isolation_status.collect(_record())
    assert status["enforcement"]["state"] == "unknown"
    cluster["facts"] = {"state": "true", "checked_at": "2026-07-29T00:00:00Z", "cni": "kindnet"}
    status = isolation_status.collect(_record())
    # Reported with its timestamp so a stale verdict is visibly stale — the value
    # is a cached measurement, never an inference from the CNI's name.
    assert status["enforcement"] == {"state": "true", "checked_at": "2026-07-29T00:00:00Z",
                                     "cni": "kindnet"}


def test_old_probe_contract_is_not_reported_as_enforced(monkeypatch):
    from datetime import datetime, timezone

    data = {
        ni.FACT_ENFORCED: "true",
        ni.FACT_CHECKED_AT: datetime.now(timezone.utc).isoformat(),
        ni.FACT_CNI: "kindnet",
    }
    api = SimpleNamespace(
        read_namespaced_config_map=lambda *_: SimpleNamespace(data=data),
        read_namespace=lambda *_: SimpleNamespace(
            metadata=SimpleNamespace(uid="cluster-1")
        ),
    )
    monkeypatch.setattr(
        "priva_control_panel.provisioner._core", lambda: api
    )
    assert isolation_status._enforcement("priva-cloud")["state"] == "unknown"
    data[ni.FACT_PROBE_VERSION] = ni.PROBE_VERSION
    data["networkPolicyIngressEnforced"] = "true"
    data["networkPolicyEgressEnforced"] = "true"
    data[ni.FACT_ADDRESS_FAMILY] = "ipv4"
    data[ni.FACT_CLUSTER_UID] = "cluster-1"
    assert isolation_status._enforcement("priva-cloud")["state"] == "true"

    data[ni.FACT_CHECKED_AT] = "2000-01-01T00:00:00Z"
    status = isolation_status._enforcement("priva-cloud")
    assert status["state"] == "unknown"
    assert status["stale"] is True


def test_leftover_legacy_policies_are_surfaced(cluster):
    cluster["policies"] = {ni.RUNNER_EGRESS, "redis-deny-terminal"}
    status = isolation_status.collect(_record(runner_deny_internal=True))
    # Policies UNION their allow rules, so a superseded permissive one silently
    # widens the new strict set.
    assert status["legacy_present"] == ["redis-deny-terminal"]


def test_proxy_is_required_in_every_mode(cluster):
    assert isolation_status.collect(_record())["proxy"]["required"] is True
    assert isolation_status.collect(_record(egress_mode="allowlist"))["proxy"]["required"] is True


def test_restricted_egress_needs_both_tenant_policies_and_proxy_policy(cluster):
    cluster["proxy"] = {
        "present": True,
        "ready": 2,
        "desired": 2,
        "applied": True,
    }
    cluster["policies"] = {ni.PROXY_POLICY}
    row = _by_key(isolation_status.collect(
        _record(egress_mode="allowlist")))["egress"]
    assert row["applied"] is False
    cluster["policies"] |= {ni.RUNNER_EGRESS, ni.TERMINAL_EGRESS}
    row = _by_key(isolation_status.collect(
        _record(egress_mode="deny_all")))["egress"]
    assert row["applied"] is True


def test_proxy_ready_requires_current_configmap_generation(monkeypatch):
    intent = "intent-a"
    conf = "http_access deny all\n"
    config_sha = hashlib.sha256(conf.encode()).hexdigest()
    cm = SimpleNamespace(
        data={"squid.conf": conf},
        metadata=SimpleNamespace(
            resource_version="7",
            annotations={
                ni.PROXY_CONFIG_SHA256_ANNOTATION: config_sha,
                ni.ISOLATION_INTENT_ANNOTATION: intent,
            },
        ),
    )
    template_annotations = {
        ni.PROXY_CONFIG_SHA256_ANNOTATION: config_sha,
        ni.PROXY_CONFIG_REVISION_ANNOTATION: "7",
        ni.ISOLATION_INTENT_ANNOTATION: intent,
    }
    deployment = SimpleNamespace(
        metadata=SimpleNamespace(generation=3),
        spec=SimpleNamespace(
            replicas=2,
            template=SimpleNamespace(
                metadata=SimpleNamespace(annotations=template_annotations)
            ),
        ),
        status=SimpleNamespace(
            observed_generation=3,
            ready_replicas=2,
            updated_replicas=2,
            available_replicas=2,
        ),
    )
    monkeypatch.setattr(
        "priva_control_panel.provisioner._core",
        lambda: SimpleNamespace(read_namespaced_config_map=lambda *_: cm),
    )
    monkeypatch.setattr(
        "priva_control_panel.provisioner._apps",
        lambda: SimpleNamespace(read_namespaced_deployment=lambda *_: deployment),
    )

    assert isolation_status._proxy_health(
        "priva-cloud", intent
    ) == {
        "present": True,
        "ready": 2,
        "desired": 2,
        "applied": True,
    }

    # A subPath pod remains physically Ready after the ConfigMap changes, but it
    # is serving the old bytes. The status must treat that as not Ready/applied.
    cm.metadata.resource_version = "8"
    stale = isolation_status._proxy_health("priva-cloud", intent)
    assert stale["applied"] is False
    assert stale["ready"] == 0


def test_postgres_row_is_always_desired_and_measured(cluster):
    cluster["policies"] = {ni.POSTGRES_POLICY}
    row = _by_key(isolation_status.collect(_record()))["postgres"]
    assert row["desired"] is True and row["applied"] is True


# --- input validation ---------------------------------------------------------

def _validate(entries):
    """The REAL router helper — not a re-implementation. A copied validator
    passes forever while the shipped one drifts."""
    from priva_control_panel.routers.admin import normalise_allowlist
    return [(e.host, e.port) for e in normalise_allowlist(
        [SimpleNamespace(**e) for e in entries])]


def test_allowlist_rejects_input_that_would_break_squid():
    from fastapi import HTTPException
    # `acl ... dstdomain` with no argument makes squid refuse to start, which
    # takes every agent's egress down with it.
    with pytest.raises(HTTPException):
        _validate([{"host": "   ", "port": 443}])
    # A URL rather than a domain: squid would match the literal string and the
    # admin would believe the host was allowed.
    with pytest.raises(HTTPException):
        _validate([{"host": "https://api.example.com", "port": 443}])
    with pytest.raises(HTTPException):
        _validate([{"host": "1.1.1.1", "port": 443}])
    with pytest.raises(HTTPException):
        _validate([{"host": "good.example\nbad.example", "port": 443}])
    # Port zero used to mean "any port" in the Squid renderer. A row must grant
    # one explicit TCP port instead of silently widening the destination.
    with pytest.raises(HTTPException):
        _validate([{"host": "api.example.com", "port": 0}])
    with pytest.raises(HTTPException):
        _validate([{"host": "api.example.com", "port": 99999}])


@pytest.mark.parametrize("model_name", ["api", "record"])
def test_allowlist_models_require_a_concrete_tcp_port(model_name):
    from priva_common.dataplane import EgressAllowEntryRecord
    from priva_common.models.admin import EgressAllowEntryModel

    model = EgressAllowEntryModel if model_name == "api" else EgressAllowEntryRecord
    for port in (0, 65536):
        with pytest.raises(ValidationError):
            model(host="api.example.com", port=port)
    assert model(host="api.example.com", port=1).port == 1
    assert model(host="api.example.com", port=65535).port == 65535


def test_allowlist_normalises_and_dedupes():
    assert _validate([{"host": "  API.Example.COM ", "port": 443},
                      {"host": "api.example.com.", "port": 443}]) == [("api.example.com", 443)]
