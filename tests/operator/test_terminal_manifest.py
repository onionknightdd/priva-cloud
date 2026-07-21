from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import priva_operator.kube as kube
import yaml


def _settings(percent=25):
    return SimpleNamespace(kubernetes=SimpleNamespace(
        runner_service_port=8091,
        terminal_service_port=8092,
        runner_uid=10001,
        runner_gid=10001,
        runner_image_pull_secret="",
        runner_cpu_cores=2.0,
        runner_memory_mb=2048,
        runner_storage_gb=10,
        terminal_resource_percent=percent,
        terminal_max_sessions=2,
        terminal_idle_timeout_seconds=1800,
        terminal_max_lifetime_seconds=14400,
        terminal_output_rate_limit_bytes_per_sec=262144,
        terminal_output_burst_bytes=1048576,
        terminal_output_buffer_bytes=1048576,
        terminal_tmp_size_limit="256Mi",
    ))


def _defaults(percent=25):
    return SimpleNamespace(
        cpu_cores=2.0,
        memory_mb=2048,
        storage_gb=10,
        runner_image="priva/agent-runner:test",
        terminal_resource_percent=percent,
        terminal_max_sessions=2,
        terminal_idle_timeout_seconds=1800,
        terminal_max_lifetime_seconds=14400,
    )


def _containers(percent=25):
    settings = _settings(percent)
    defaults = _defaults(percent)
    mount = kube.MountInfo(kind="shared_pvc_subpath", claim="data", sub_path="acct")
    owner = {"apiVersion": "priva.io/v1alpha1", "kind": "AgentTenant",
             "name": "acct", "uid": "uid"}
    runner = kube._deployment_body(
        "ns", "acct", "alice", "image:test", "IfNotPresent", settings,
        owner, {}, mount, defaults)
    terminal = kube._terminal_deployment_body(
        "ns", "acct", "alice", "image:test", "IfNotPresent", settings,
        owner, {}, mount, defaults)
    return runner, terminal


def test_fixed_allocation_sums_to_tenant_commitment():
    runner, terminal = _containers()
    rr = runner["spec"]["template"]["spec"]["containers"][0]["resources"]
    tr = terminal["spec"]["template"]["spec"]["containers"][0]["resources"]
    assert rr["requests"] == rr["limits"] == {"cpu": "1500m", "memory": "1536Mi"}
    assert tr["requests"] == tr["limits"] == {"cpu": "500m", "memory": "512Mi"}


def test_terminal_manifest_has_independent_security_and_scratch_boundary():
    runner, terminal = _containers()
    runner_spec = runner["spec"]["template"]["spec"]
    terminal_spec = terminal["spec"]["template"]["spec"]
    runner_container = runner_spec["containers"][0]
    container = terminal_spec["containers"][0]

    assert container["image"] == runner_container["image"]
    assert terminal_spec["securityContext"]["runAsUser"] == 10001
    assert terminal_spec["securityContext"]["runAsGroup"] == 10001
    assert terminal_spec["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    assert terminal_spec["automountServiceAccountToken"] is False
    assert terminal_spec["hostPID"] is False
    assert terminal_spec["hostIPC"] is False
    assert terminal_spec["hostNetwork"] is False
    assert terminal_spec["shareProcessNamespace"] is False
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "privileged": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    assert "envFrom" not in container
    assert container["command"] == [
        "/usr/bin/prlimit", "--nofile=4096:4096", "--nproc=256:256",
        "--core=0:0", "--", "/usr/local/bin/priva-terminald",
    ]
    tmp = next(v for v in terminal_spec["volumes"] if v["name"] == "tmp")
    assert tmp["emptyDir"] == {"medium": "Memory", "sizeLimit": "256Mi"}
    data_mount = next(m for m in container["volumeMounts"] if m["name"] == "data")
    assert data_mount == {"name": "data", "mountPath": "/workspace", "subPath": "acct"}


def test_invalid_non_step_percent_fails_closed():
    assert kube.resolve_terminal_percent(_settings(17), None) == 0


def test_terminal_network_policies_only_protect_internal_destinations():
    root = Path(__file__).resolve().parents[2]
    docs = list(yaml.safe_load_all(
        (root / "deploy/k8s/terminal-networkpolicy.yaml").read_text()))
    policies = {doc["metadata"]["name"]: doc["spec"] for doc in docs}

    assert set(policies) == {
        "data-spine-deny-terminal",
        "redis-deny-terminal",
        "runner-deny-tenant-peers",
        "terminal-deny-tenant-peers",
    }
    # These are destination-ingress guards, not a default-deny egress policy:
    # Terminal keeps DNS, internet, and unrelated-site access.
    assert all(spec["policyTypes"] == ["Ingress"] for spec in policies.values())
    assert all("egress" not in spec for spec in policies.values())

    data_spine_sources = policies["data-spine-deny-terminal"]["ingress"][0]["from"]
    redis_sources = policies["redis-deny-terminal"]["ingress"][0]["from"]
    for sources in (data_spine_sources, redis_sources):
        expression = sources[0]["podSelector"]["matchExpressions"][0]
        assert expression == {"key": "app", "operator": "NotIn", "values": ["terminal"]}

    for name in ("runner-deny-tenant-peers", "terminal-deny-tenant-peers"):
        expression = policies[name]["ingress"][0]["from"][0]["podSelector"][
            "matchExpressions"][0]
        assert expression == {
            "key": "app", "operator": "NotIn", "values": ["agent-runner", "terminal"]}

    postgres_docs = list(yaml.safe_load_all(
        (root / "deploy/k8s/postgres.yaml").read_text()))
    postgres_policy = next(
        doc["spec"] for doc in postgres_docs
        if doc and doc.get("kind") == "NetworkPolicy"
        and doc["metadata"]["name"] == "postgres-only-data-spine")
    assert postgres_policy["policyTypes"] == ["Ingress"]
    assert postgres_policy["ingress"][0]["from"] == [
        {"podSelector": {"matchLabels": {"app": "data-spine"}}}]
