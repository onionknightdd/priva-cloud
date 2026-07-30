"""Crash-safe admin runner restart state machine."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from priva_control_panel import provisioner as P


class _Apps:
    def __init__(self, replicas: int, events: list[str]):
        self.replicas = replicas
        self.events = events

    def read_namespaced_deployment(self, name, namespace):
        self.events.append("read")
        return SimpleNamespace(spec=SimpleNamespace(replicas=self.replicas))

    def patch_namespaced_deployment_scale(self, name, namespace, body):
        assert body == {"spec": {"replicas": 0}}
        self.events.append("scale-zero")
        self.replicas = 0


class _Core:
    def __init__(self, pod_lists, events: list[str]):
        self.pod_lists = list(pod_lists)
        self.events = events
        self.index = 0

    def list_namespaced_pod(self, namespace, label_selector):
        self.events.append("list-pods")
        index = min(self.index, len(self.pod_lists) - 1)
        self.index += 1
        return SimpleNamespace(items=self.pod_lists[index])


class _Custom:
    def __init__(self, events: list[str], *, fail_clear_once: bool = False):
        self.events = events
        self.status: dict = {}
        self.wake_requested_at: list[str] = []
        self.fail_clear_once = fail_clear_once

    def get_namespaced_custom_object(self, group, version, namespace, plural, name):
        self.events.append("status-read")
        return {"status": deepcopy(self.status)}

    def patch_namespaced_custom_object_status(
        self, group, version, namespace, plural, name, body,
    ):
        fields = body["status"]
        pending = fields.get("restartPending")
        if "restartPending" in fields and pending is None:
            if self.fail_clear_once:
                self.fail_clear_once = False
                self.events.append("clear-failed")
                raise RuntimeError("injected clear failure")
            self.events.append("clear")
            self.status.pop("restartPending", None)
        elif pending is not None:
            self.events.append(f"mark-{pending['stage']}")
            self.status["restartPending"] = deepcopy(pending)
        for key in ("phase", "podIP", "readyReplicas"):
            if key in fields:
                self.status[key] = fields[key]

    def patch_namespaced_custom_object(
        self, group, version, namespace, plural, name, body,
    ):
        requested_at = body["spec"]["wake"]["requestedAt"]
        self.events.append("wake")
        self.wake_requested_at.append(requested_at)


def _settings():
    return SimpleNamespace(kubernetes=SimpleNamespace(
        namespace_tenants="tenants",
        runner_service_port=8082,
        wake_hold_seconds=0.01,
    ))


def _install(monkeypatch, apps, core, custom):
    monkeypatch.setattr(P, "get_settings", _settings)
    monkeypatch.setattr(P, "_apps", lambda: apps)
    monkeypatch.setattr(P, "_core", lambda: core)
    monkeypatch.setattr(P, "_custom", lambda: custom)


def test_force_restart_crosses_zero_and_waits_for_terminating_pods(monkeypatch):
    events: list[str] = []
    # deletionTimestamp is deliberately set: Terminating still blocks wake.
    terminating = SimpleNamespace(
        metadata=SimpleNamespace(deletion_timestamp="2026-07-29T00:00:00Z"),
    )
    apps = _Apps(1, events)
    core = _Core([[terminating], []], events)
    custom = _Custom(events)
    _install(monkeypatch, apps, core, custom)

    restarted = P.force_restart_pod("acct")

    assert restarted == 1
    assert events == [
        "status-read",
        "read",
        "mark-draining",
        "scale-zero",
        "list-pods",
        "list-pods",
        "mark-waking",
        "wake",
        "clear",
    ]
    assert custom.status["phase"] == "Zero"
    assert "restartPending" not in custom.status
    assert len(custom.wake_requested_at) == 1


def test_force_restart_is_noop_for_dormant_runner(monkeypatch):
    events: list[str] = []
    apps = _Apps(0, events)
    custom = _Custom(events)

    monkeypatch.setattr(P, "get_settings", _settings)
    monkeypatch.setattr(P, "_apps", lambda: apps)
    monkeypatch.setattr(P, "_custom", lambda: custom)
    monkeypatch.setattr(
        P, "_core", lambda: (_ for _ in ()).throw(
            AssertionError("core must not be called"),
        ),
    )

    assert P.force_restart_pod("acct") == 0
    assert events == ["status-read", "read"]


def test_retry_continues_from_replicas_zero_after_wait_timeout(monkeypatch):
    events: list[str] = []
    apps = _Apps(1, events)
    custom = _Custom(events)
    core_box = [_Core([[SimpleNamespace()]], events)]
    _install(monkeypatch, apps, core_box[0], custom)
    monkeypatch.setattr(P, "_core", lambda: core_box[0])
    monkeypatch.setattr(P, "_RESTART_POD_TIMEOUT_SECONDS", 0)

    with pytest.raises(RuntimeError, match="timed out waiting"):
        P.force_restart_pod("acct")

    assert apps.replicas == 0
    assert custom.status["restartPending"]["stage"] == "draining"
    assert custom.status["phase"] == "Draining"
    assert custom.wake_requested_at == []

    # The old pod has now gone. Despite replicas already being zero, retry
    # resumes the persisted transaction and emits wake.
    core_box[0] = _Core([[]], events)
    monkeypatch.setattr(P, "_RESTART_POD_TIMEOUT_SECONDS", 1)
    assert P.force_restart_pod("acct") == 1
    assert custom.wake_requested_at
    assert "restartPending" not in custom.status


def test_retry_after_wake_and_clear_failure_never_scales_new_pod(monkeypatch):
    events: list[str] = []
    apps = _Apps(1, events)
    core = _Core([[]], events)
    custom = _Custom(events, fail_clear_once=True)
    _install(monkeypatch, apps, core, custom)

    with pytest.raises(RuntimeError, match="injected clear failure"):
        P.force_restart_pod("acct")

    assert custom.status["restartPending"]["stage"] == "waking"
    first_token = custom.wake_requested_at[0]
    assert events.count("scale-zero") == 1

    # Model the operator already creating the replacement. The persisted
    # `waking` stage must bypass both scale and pod wait on retry.
    apps.replicas = 1
    monkeypatch.setattr(
        P, "_core", lambda: (_ for _ in ()).throw(
            AssertionError("waking retry must not list or kill the new pod"),
        ),
    )

    assert P.force_restart_pod("acct") == 1
    assert events.count("scale-zero") == 1
    assert custom.wake_requested_at == [first_token, first_token]
    assert "restartPending" not in custom.status


def test_restart_pending_is_a_runner_route_gate(monkeypatch):
    pending = {
        "requestedAt": "2026-07-29T00:00:00+00:00",
        "stage": "draining",
        "oldPods": 1,
    }
    monkeypatch.setattr(P, "get_settings", _settings)
    monkeypatch.setattr(P, "_status", lambda _account_id: {
        "phase": "Running",
        "podIP": "10.0.0.8",
        "restartPending": pending,
    })
    monkeypatch.setattr(
        P, "_patch_wake", lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(AssertionError("must not wake during restart"))
        ),
    )

    assert asyncio.run(P.wake_and_wait("acct")) is None
    assert asyncio.run(P._drive_wake("acct")) is None
