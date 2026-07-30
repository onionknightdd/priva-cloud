from __future__ import annotations

import json
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
        runner_image="priva/agent-runner:test",
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


def test_allocation_hash_changes_when_total_changes_at_same_percent():
    settings = _settings(25)
    defaults = _defaults(25)
    before = kube.allocation_hash(
        {"resources": {"cpu": 1.0, "memoryMb": 1024}}, settings, defaults, "alice")
    after = kube.allocation_hash(
        {"resources": {"cpu": 2.0, "memoryMb": 2048}}, settings, defaults, "alice")
    assert before != after


def test_allocation_hash_changes_when_token_verification_key_rotates():
    settings = _settings(25)
    defaults = _defaults(25)
    before = kube.allocation_hash(
        {}, settings, defaults, "alice", verification_key="public-key-a")
    after = kube.allocation_hash(
        {}, settings, defaults, "alice", verification_key="public-key-b")

    assert before != after


def test_allocation_hash_covers_the_full_verifier_overlap_ring():
    settings = _settings(25)
    defaults = _defaults(25)
    before = kube.allocation_hash(
        {},
        settings,
        defaults,
        "alice",
        verification_key_ring=("current",),
    )
    overlap = kube.allocation_hash(
        {},
        settings,
        defaults,
        "alice",
        verification_key_ring=("current", "future"),
    )

    assert before != overlap


def test_allocation_hash_tracks_which_overlap_key_is_the_current_signer():
    settings = _settings(25)
    defaults = _defaults(25)
    old_current = kube.allocation_hash(
        {},
        settings,
        defaults,
        "alice",
        verification_key_ring=("old-current", "new-future"),
    )
    new_current = kube.allocation_hash(
        {},
        settings,
        defaults,
        "alice",
        verification_key_ring=("new-future", "old-current"),
    )

    assert old_current != new_current


def test_runner_and_terminal_share_full_allocation_generation():
    runner, terminal = _containers()
    key = "priva.io/allocation-hash"
    assert runner["metadata"]["annotations"][key]
    assert runner["metadata"]["annotations"][key] == terminal["metadata"]["annotations"][key]
    assert "priva.io/terminal-template-hash" not in runner["metadata"]["annotations"]
    assert terminal["metadata"]["annotations"]["priva.io/terminal-template-hash"]


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
    assert container["readinessProbe"]["tcpSocket"] == {"port": 8092}
    assert container["livenessProbe"]["tcpSocket"] == {"port": 8092}
    assert "httpGet" not in container["readinessProbe"]
    assert "httpGet" not in container["livenessProbe"]
    assert "envFrom" not in container
    assert container["command"] == [
        "/usr/bin/prlimit", "--nofile=4096:4096", "--nproc=256:256",
        "--core=0:0", "--", "/usr/local/bin/priva-terminald",
    ]
    tmp = next(v for v in terminal_spec["volumes"] if v["name"] == "tmp")
    assert tmp["emptyDir"] == {"medium": "Memory", "sizeLimit": "256Mi"}
    data_mount = next(m for m in container["volumeMounts"] if m["name"] == "data")
    assert data_mount == {"name": "data", "mountPath": "/workspace", "subPath": "acct"}


def test_terminal_manifest_binds_signed_capabilities_to_account_and_pod():
    runner, terminal = _containers()
    runner_env = {
        item["name"]: item
        for item in runner["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    container = terminal["spec"]["template"]["spec"]["containers"][0]
    env = {item["name"]: item for item in container["env"]}

    assert env["PRIVA_TERMINAL_ACCOUNT_ID"]["value"] == "acct"
    assert env["PRIVA_TERMINAL_POD"]["valueFrom"]["fieldRef"] == {
        "apiVersion": "v1",
        "fieldPath": "status.podIP",
    }
    public_key = env["PRIVA_SERVICE_IDENTITY__PUBLIC_KEY"]["value"]
    assert public_key.startswith("-----BEGIN PUBLIC KEY-----")
    assert "PRIVATE KEY" not in public_key
    assert json.loads(
        env["PRIVA_SERVICE_IDENTITY__ADDITIONAL_PUBLIC_KEYS"]["value"]
    ) == []
    assert json.loads(
        runner_env["PRIVA_SERVICE_IDENTITY__ADDITIONAL_PUBLIC_KEYS"]["value"]
    ) == []
    assert len(env["PRIVA_INTERNAL_DRAIN_TOKEN"]["value"]) >= 32
    assert len(runner_env["PRIVA_INTERNAL_DRAIN_TOKEN"]["value"]) >= 32
    assert (
        env["PRIVA_INTERNAL_DRAIN_TOKEN"]["value"]
        != runner_env["PRIVA_INTERNAL_DRAIN_TOKEN"]["value"]
    )


def test_terminal_template_hash_changes_when_verification_key_rotates():
    settings = _settings()
    defaults = _defaults()
    before = kube.terminal_template_hash(
        {}, settings, defaults, "alice", verification_key="public-key-a")
    after = kube.terminal_template_hash(
        {}, settings, defaults, "alice", verification_key="public-key-b")

    assert before != after


def test_invalid_non_step_percent_fails_closed():
    assert kube.resolve_terminal_percent(_settings(17), None) == 0


def test_tenant_isolation_policies_are_not_shipped_as_static_manifests():
    # They are rendered by the operator from the admin settings now
    # (tests/operator/test_network_policies.py). A static copy would flap: up.sh
    # or helm writes it, the operator prunes it 15s later.
    root = Path(__file__).resolve().parents[2]
    assert not (root / "deploy/k8s/terminal-networkpolicy.yaml").exists()
    assert not (root / "deploy/helm/priva-cloud/templates/terminal-networkpolicy.yaml").exists()


def test_postgres_stays_a_hand_applied_control_plane_boundary():
    # data-spine→postgres is a control-plane boundary, not a tenant one, so it is
    # deliberately NOT in the operator's managed set and must not be prunable by it.
    from priva_operator import netpol
    assert "postgres-only-data-spine" not in netpol.LEGACY_POLICIES

    root = Path(__file__).resolve().parents[2]
    postgres_docs = list(yaml.safe_load_all(
        (root / "deploy/k8s/postgres.yaml").read_text()))
    postgres_policy = next(
        doc["spec"] for doc in postgres_docs
        if doc and doc.get("kind") == "NetworkPolicy"
        and doc["metadata"]["name"] == "postgres-only-data-spine")
    assert postgres_policy["policyTypes"] == ["Ingress"]
    assert postgres_policy["ingress"][0]["from"] == [
        {"podSelector": {"matchLabels": {"app": "data-spine"}}}]
