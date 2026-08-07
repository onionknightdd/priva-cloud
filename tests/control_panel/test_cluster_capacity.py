from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from priva_control_panel import provisioner as P
from priva_control_panel.routers import admin


def _container(cpu: str = "", memory: str = "", *, restart_policy: str | None = None):
    return SimpleNamespace(
        resources=SimpleNamespace(requests={"cpu": cpu, "memory": memory}),
        restart_policy=restart_policy,
    )


def _pod(
    *,
    node_name: str | None,
    containers=None,
    init_containers=None,
    labels=None,
    phase: str = "Running",
    overhead=None,
):
    return SimpleNamespace(
        metadata=SimpleNamespace(labels=labels or {}),
        spec=SimpleNamespace(
            node_name=node_name,
            containers=containers or [],
            init_containers=init_containers or [],
            overhead=overhead or {},
        ),
        status=SimpleNamespace(phase=phase),
    )


def _node(
    name: str,
    *,
    cpu: str = "4",
    memory: str = "8Gi",
    ready: bool = True,
    unschedulable: bool = False,
    taint_effects=(),
):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name),
        spec=SimpleNamespace(
            unschedulable=unschedulable,
            taints=[SimpleNamespace(effect=effect) for effect in taint_effects],
        ),
        status=SimpleNamespace(
            conditions=[SimpleNamespace(type="Ready", status="True" if ready else "False")],
            allocatable={"cpu": cpu, "memory": memory},
        ),
    )


def test_pod_effective_requests_follow_scheduler_init_and_overhead_rules():
    pod = _pod(
        node_name="n1",
        containers=[_container("400m", "256Mi"), _container("100m", "128Mi")],
        init_containers=[
            _container("100m", "100Mi", restart_policy="Always"),
            _container("800m", "700Mi"),
        ],
        overhead={"cpu": "50m", "memory": "16Mi"},
    )

    cpu_m, memory_mb = P._pod_effective_requests(pod)

    # init peak = sidecar + ordinary init = 900m / 800Mi, then Pod overhead.
    assert cpu_m == pytest.approx(950)
    assert memory_mb == pytest.approx(816)


def test_runner_eligible_node_rejects_cordon_and_hard_taints():
    assert P._node_runner_eligibility(_node("ready")) == (True, "eligible")
    assert P._node_runner_eligibility(
        _node("preferred", taint_effects=("PreferNoSchedule",))
    ) == (True, "eligible")
    assert P._node_runner_eligibility(_node("not-ready", ready=False)) == (
        False,
        "not_ready",
    )
    assert P._node_runner_eligibility(_node("cordoned", unschedulable=True)) == (
        False,
        "cordoned",
    )
    assert P._node_runner_eligibility(
        _node("tainted", taint_effects=("NoSchedule",))
    ) == (False, "untolerated_taint")


def test_scrape_cluster_capacity_subtracts_only_fixed_load_on_eligible_nodes(monkeypatch):
    active_runtime = {
        "app": "agent-runner",
        "priva.io/account-id": "active",
    }
    inactive_runtime = {
        "app": "terminal",
        "priva.io/account-id": "disabled",
    }
    nodes = [
        _node("n1"),
        _node("n2", cpu="8", memory="16Gi", taint_effects=("NoSchedule",)),
    ]
    pods = [
        _pod(node_name="n1", containers=[_container("500m", "512Mi")]),
        _pod(node_name="n1", containers=[_container("2", "2Gi")], labels=active_runtime),
        _pod(node_name="n1", containers=[_container("250m", "256Mi")], labels=inactive_runtime),
        _pod(node_name="n1", containers=[_container("1", "1Gi")], phase="Succeeded"),
        _pod(node_name="n2", containers=[_container("3", "3Gi")]),
        _pod(node_name=None, containers=[_container("100m", "64Mi")], phase="Pending"),
    ]
    core = SimpleNamespace(
        list_node=lambda: SimpleNamespace(items=nodes),
        list_pod_for_all_namespaces=lambda: SimpleNamespace(items=pods),
    )
    monkeypatch.setattr(P, "_core", lambda: core)

    snapshot = P.scrape_cluster_capacity({"active"})

    assert {key: value for key, value in snapshot.items() if key != "nodes"} == {
        "total_nodes": 2,
        "eligible_nodes": 1,
        "node_allocatable_cpu_m": pytest.approx(4000),
        "node_allocatable_memory_mb": pytest.approx(8192),
        "non_runner_requested_cpu_m": pytest.approx(750),
        "non_runner_requested_memory_mb": pytest.approx(768),
        "pending_non_runner_pods": 1,
    }
    assert snapshot["nodes"] == [
        {
            "name": "n1",
            "eligible": True,
            "eligibility_reason": "eligible",
            "cpu": {
                "allocatable": 4000,
                "non_runner_requested": 750,
                "runtime_requested": 2000,
                "current_remaining": 1250,
            },
            "memory": {
                "allocatable": 8192,
                "non_runner_requested": 768,
                "runtime_requested": 2048,
                "current_remaining": 5376,
            },
        },
        {
            "name": "n2",
            "eligible": False,
            "eligibility_reason": "untolerated_taint",
            "cpu": {
                "allocatable": 8000,
                "non_runner_requested": 3000,
                "runtime_requested": 0,
                "current_remaining": None,
            },
            "memory": {
                "allocatable": 16384,
                "non_runner_requested": 3072,
                "runtime_requested": 0,
                "current_remaining": None,
            },
        },
    ]


def test_cluster_capacity_route_counts_only_active_account_quotas(monkeypatch):
    users = [
        SimpleNamespace(account_id="default", status="active"),
        SimpleNamespace(account_id="custom", status="active"),
        SimpleNamespace(account_id="disabled", status="disabled"),
    ]
    defaults = SimpleNamespace(cpu_cores=1.0, memory_mb=1024)
    specs = [
        SimpleNamespace(account_id="custom", cpu_cores=2.5, memory_mb=4096),
        SimpleNamespace(account_id="disabled", cpu_cores=8.0, memory_mb=16384),
    ]
    data_client = SimpleNamespace(
        resource_specs=SimpleNamespace(list=lambda: specs),
        runner_defaults=SimpleNamespace(get=lambda: defaults),
    )
    monkeypatch.setattr(admin, "get_user_store", lambda: SimpleNamespace(list_users=lambda: users))

    import priva_common.dataplane as dataplane

    monkeypatch.setattr(dataplane, "get_client", lambda: data_client)

    def snapshot(active_ids):
        assert active_ids == {"default", "custom"}
        return {
            "total_nodes": 2,
            "eligible_nodes": 2,
            "node_allocatable_cpu_m": 10000,
            "node_allocatable_memory_mb": 20000,
            "non_runner_requested_cpu_m": 1000,
            "non_runner_requested_memory_mb": 2000,
            "pending_non_runner_pods": 0,
            "nodes": [{
                "name": "worker-1",
                "eligible": True,
                "eligibility_reason": "eligible",
                "cpu": {
                    "allocatable": 10000,
                    "non_runner_requested": 1000,
                    "runtime_requested": 2000,
                    "current_remaining": 7000,
                },
                "memory": {
                    "allocatable": 20000,
                    "non_runner_requested": 2000,
                    "runtime_requested": 4096,
                    "current_remaining": 13904,
                },
            }],
        }

    monkeypatch.setattr(P, "scrape_cluster_capacity", snapshot)

    result = asyncio.run(admin.get_cluster_capacity())

    assert result.active_accounts == 2
    assert result.cpu.assignable == 9000
    assert result.cpu.allocated == 3500
    assert result.cpu.remaining == 5500
    assert result.cpu.allocation_percent == pytest.approx(38.9)
    assert result.cpu.overcommit_percent == 0
    assert result.memory.assignable == 18000
    assert result.memory.allocated == 5120
    assert result.nodes[0].name == "worker-1"
    assert result.nodes[0].cpu.runtime_requested == 2000
    assert result.nodes[0].memory.current_remaining == 13904


def test_capacity_metric_reports_signed_remaining_and_excess_only():
    metric = admin._capacity_metric(16000, 0, 20000)

    assert metric.remaining == -4000
    assert metric.allocation_percent == 125
    assert metric.overcommit_percent == 25
