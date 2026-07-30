"""Shared fixtures for the operator unit suite.

The kopf handlers are plain functions (kopf's decorators return them unchanged), so
the tests call them directly with fabricated spec/status + a stub patch/logger and a
mocked kube client — no cluster, no kopf runtime.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest


class _Patch:
    """Stand-in for kopf's patch object — handlers write ``patch.status[...]`` and kopf
    applies it as a single PATCH on return. The tests inspect what was written."""

    def __init__(self) -> None:
        self.status: dict = {}


class _Logger:
    """No-op logger (handlers log freely; tests don't assert on it)."""

    def info(self, *a, **k) -> None: ...
    def warning(self, *a, **k) -> None: ...
    def debug(self, *a, **k) -> None: ...
    def error(self, *a, **k) -> None: ...


@pytest.fixture
def patch_obj() -> _Patch:
    return _Patch()


@pytest.fixture
def stub_logger() -> _Logger:
    return _Logger()


@pytest.fixture(autouse=True)
def verified_isolation_baseline(monkeypatch):
    """Unrelated handler tests run with a verified last-known-good boundary.

    Tests which exercise the fail-closed path override these attributes
    explicitly. Without this fixture every quota/idle/identity unit test would
    also need to fabricate data-spine, CNI-probe and Deployment readiness state.
    """
    from priva_operator import reconcile

    isolation = SimpleNamespace(
        runner_deny_internal=False,
        terminal_deny_internal=False,
        deny_tenant_peers=False,
        egress_mode="unrestricted",
        egress_allowlist=[],
    )
    monkeypatch.setattr(reconcile, "_network_isolation_cache", isolation)
    monkeypatch.setattr(reconcile, "_network_isolation_last_attempt", time.monotonic())
    monkeypatch.setattr(reconcile, "_network_policy_last_render", time.monotonic())
    monkeypatch.setattr(
        reconcile,
        "_network_isolation_applied_intent",
        reconcile.isolation_intent_digest(isolation, reconcile.get_settings()),
    )
    monkeypatch.setattr(reconcile, "_network_isolation_dirty", False)
    monkeypatch.setattr(
        reconcile.kube, "network_policy_enforced", lambda *a, **k: True)
    monkeypatch.setattr(reconcile.kube, "egress_proxy_ready", lambda *a, **k: True)
    monkeypatch.setattr(
        reconcile.kube, "wait_egress_proxy_ready", lambda *a, **k: True)
    monkeypatch.setattr(reconcile.kube, "egress_generation", lambda *a, **k: "e1:test")
    monkeypatch.setattr(
        reconcile.kube, "applied_egress_generation", lambda *a, **k: "e1:test")
    monkeypatch.setattr(
        reconcile.kube, "applied_terminal_egress_generation",
        lambda *a, **k: "e1:test",
    )
    # Handler unit tests use fabricated status snapshots. Live-phase race tests
    # override these explicitly; the common baseline must never contact a real
    # Kubernetes API server.
    monkeypatch.setattr(reconcile.kube, "current_cr_phase", lambda *a, **k: None)
    monkeypatch.setattr(
        reconcile.kube, "current_cr_terminal_phase", lambda *a, **k: None)
    monkeypatch.setattr(
        reconcile.kube, "agenttenant_teardown_started", lambda *a, **k: False)
    monkeypatch.setattr(
        reconcile.kube, "applied_runner_drain_token", lambda *a, **k: None)
    monkeypatch.setattr(
        reconcile.kube, "applied_terminal_drain_token", lambda *a, **k: None)
    monkeypatch.setattr(
        reconcile, "_force_close_account_admission", lambda *a, **k: None)
    monkeypatch.setattr(
        reconcile, "_force_close_runner_admission", lambda *a, **k: None)
    monkeypatch.setattr(
        reconcile, "_force_close_terminal_admission", lambda *a, **k: None)
