"""Deployment invariants for the fail-closed tenant network boundary."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy" / "helm" / "priva-cloud"


def test_probe_revokes_old_success_and_binds_new_fact_to_cluster():
    script = (ROOT / "deploy" / "checks" / "networkpolicy-cni.sh").read_text()
    invalidate = 'record_fact unknown unknown unknown "$CNI" unknown'
    create_probe_namespace = 'kubectl create ns "$NS"'
    assert invalidate in script
    assert script.index(invalidate) < script.index(create_probe_namespace)
    assert '--from-literal=networkPolicyProbeVersion="3"' in script
    assert '--from-literal=networkPolicyAddressFamily="$address_family"' in script
    assert '--from-literal=networkPolicyClusterUid="$CLUSTER_UID"' in script


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
def test_chart_installs_independent_empty_networkpolicy_baselines():
    rendered = subprocess.run(
        ["helm", "template", "priva", str(CHART)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    objects = [item for item in yaml.safe_load_all(rendered) if item]
    policies = {
        item["metadata"]["name"]: item
        for item in objects
        if item.get("kind") == "NetworkPolicy"
    }
    for name, app in (
        ("priva-tenant-runner-baseline", "agent-runner"),
        ("priva-tenant-terminal-baseline", "terminal"),
        ("priva-tenant-egress-proxy-baseline", "egress-proxy"),
    ):
        policy = policies[name]
        assert policy["spec"]["podSelector"]["matchLabels"]["app"] == app
        assert set(policy["spec"]["policyTypes"]) == {"Ingress", "Egress"}
        assert policy["spec"]["ingress"] == []
        assert policy["spec"]["egress"] == []


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
def test_operator_has_pod_delete_for_fail_closed_proxy_cutover():
    rendered = subprocess.run(
        ["helm", "template", "priva", str(CHART)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    objects = [item for item in yaml.safe_load_all(rendered) if item]
    role = next(
        item
        for item in objects
        if item.get("kind") == "Role"
        and item["metadata"]["name"] == "priva-operator"
    )
    pod_rule = next(
        rule for rule in role["rules"] if rule.get("resources") == ["pods"]
    )
    assert set(pod_rule["verbs"]) == {"get", "list", "watch", "delete"}
