from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
RAW_RBAC = ROOT / "deploy" / "rbac" / "control-panel-rbac.yaml"
CHART = ROOT / "deploy" / "helm" / "priva-cloud"


def _capacity_role(documents):
    return next(
        document for document in documents
        if document
        and document.get("kind") == "ClusterRole"
        and document.get("metadata", {}).get("name") == "priva-control-panel-nodes"
    )


def _assert_capacity_permissions(role):
    rules = role["rules"]
    node_rules = [rule for rule in rules if "nodes" in rule.get("resources", [])]
    pod_rules = [rule for rule in rules if "pods" in rule.get("resources", [])]
    assert any({"get", "list"} <= set(rule["verbs"]) for rule in node_rules)
    assert any("list" in rule["verbs"] for rule in pod_rules)
    assert all(rule.get("apiGroups") == [""] for rule in node_rules + pod_rules)


def test_raw_control_panel_rbac_can_read_cluster_capacity_inventory():
    documents = list(yaml.safe_load_all(RAW_RBAC.read_text()))
    _assert_capacity_permissions(_capacity_role(documents))


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
def test_helm_control_panel_rbac_can_read_cluster_capacity_inventory():
    rendered = subprocess.run(
        ["helm", "template", "priva", str(CHART)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    _assert_capacity_permissions(_capacity_role(yaml.safe_load_all(rendered)))
