"""Connector-specific env knobs. Everything else (dataplane transport, kubernetes
namespace/port, jwt secret) comes from the shared ``priva_common.config`` settings,
exactly like the scheduler."""

from __future__ import annotations

import os


def poll_seconds() -> float:
    """Reconcile poll cadence. Tighter than the scheduler's 30s because this is a
    kill-switch: an admin disable must take effect quickly even if the low-latency
    push is missed."""
    return float(os.environ.get("CONNECTOR_POLL_SECONDS", "10"))


def api_host() -> str:
    return os.environ.get("CONNECTOR_HOST", "0.0.0.0")


def api_port() -> int:
    return int(os.environ.get("CONNECTOR_PORT", "8083"))
