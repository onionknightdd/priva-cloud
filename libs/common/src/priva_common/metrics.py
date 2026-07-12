"""Prometheus metric definitions and registry rendering.

Single definition site for every metric so the middleware and routers import
the same objects. HTTP metrics are recorded by ``AccessLogMiddleware`` (it
already resolves the route template, method, status and duration — no duplicate
work). Domain counters are incremented from the agent run lifecycle.

Multiprocess: when ``PROMETHEUS_MULTIPROC_DIR`` is set (uvicorn ``--workers``
> 1, owned/wiped by ``server.sh``), each worker writes to that dir and
``/metrics`` aggregates across workers via ``MultiProcessCollector``. When
unset (single-process dev), the default in-process registry is used.

See ``docs/adr/0002-prometheus-metrics-architecture.md`` for rationale.
"""

from __future__ import annotations

import os

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# --- HTTP auto-metrics (recorded in AccessLogMiddleware) ---

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests.",
    ["handler", "method", "status"],
)

HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds.",
    ["handler", "method"],
)

# --- Agent domain counters (incremented from the run lifecycle) ---

AGENT_RUNS_STARTED = Counter(
    "agent_runs_started_total",
    "Total agent runs started.",
)

AGENT_RUNS_FINISHED = Counter(
    "agent_runs_finished_total",
    "Total agent runs finished, by terminal outcome.",
    ["outcome"],  # success | error | cancelled
)

SCHEDULED_RUNS = Counter(
    "priva_scheduled_runs_total",
    "Scheduler-dispatched runs executed on this pod, by job type and outcome.",
    ["job_type", "status"],  # status: success | error | cancelled
)

# --- services/scheduler (the firing engine; design §11) ---

SCHEDULER_FIRES = Counter(
    "priva_scheduler_fires_total",
    "Claimed fires by pipeline outcome.",
    ["outcome"],  # dispatched | skipped_busy | skipped_inactive | skipped_cap | error
)

SCHEDULER_CLAIM_LOST = Counter(
    "priva_scheduler_claim_lost_total",
    "Fires this replica lost to another replica's claim (normal in N>1).",
)

SCHEDULER_DISPATCH_SECONDS = Histogram(
    "priva_scheduler_dispatch_seconds",
    "Wake+dial dispatch latency (claim win to runner admission verdict).",
)

SCHEDULER_ARMED_JOBS = Gauge(
    "priva_scheduler_armed_jobs",
    "Jobs currently armed on this replica's APScheduler clock.",
)

SCHEDULER_SWEEP_REAPED = Counter(
    "priva_scheduler_sweep_reaped_total",
    "Stale 'running' records aged out to error(dispatch_lost) by the sweep.",
)

# --- Hook executor (admin policy hooks + user hooks; the blast-radius alarm) ---
# hook_id = policy id for admin hooks, "user" for user-configured hooks
# (unbounded user commands must not explode label cardinality).

HOOK_FIRES = Counter(
    "priva_hook_fires_total",
    "Hook executions, by hook and outcome.",
    ["hook_id", "status"],  # ok | blocked | error | timeout
)

HOOK_DURATION = Histogram(
    "priva_hook_duration_seconds",
    "Hook execution latency in seconds.",
    ["hook_id"],
)


def build_registry() -> CollectorRegistry:
    """Return the registry to scrape.

    Multiprocess mode rebuilds a fresh registry per scrape backed by a
    ``MultiProcessCollector`` reading every worker's files; single-process
    mode reuses the default global registry.
    """
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        from prometheus_client import multiprocess

        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return registry
    return REGISTRY


def render() -> tuple[bytes, str]:
    """Return the exposition payload and its Content-Type."""
    return generate_latest(build_registry()), CONTENT_TYPE_LATEST
