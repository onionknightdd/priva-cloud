"""In-pod activity tracker the operator reads via /health to decide idle->sleep.

active_runs = in-flight requests (a streaming run / parked permission WebSocket
keeps the connection open, so it counts and the pod won't be slept mid-turn).
last_activity_ts = epoch of the last request boundary. /health probes are NOT
counted (else the operator's own polling would keep the pod awake forever).

The revision/drain gate closes the health-check TOCTOU: the operator observes
``active=0, revision=N`` and can atomically stop new admission only if nothing
entered and left in between. Once closed, the gate remains closed for the
lifetime of this process. A timed lease is unsafe because Pod termination can
outlive it, allowing a client which already resolved the old endpoint to enter
again while Kubernetes is still deleting the Pod.
"""

from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_active = 0
_last = time.time()
_revision = 0
_drain_gate = False


def try_enter() -> bool:
    """Atomically enter unless the operator currently owns the drain gate."""
    global _active, _last, _revision
    with _lock:
        if _drain_gate:
            return False
        _active += 1
        _revision += 1
        _last = time.time()
        return True


def enter() -> None:
    """Enter a nested/background activity slot.

    Production callers run below ActivityMiddleware, so a drain cannot begin
    between outer admission and this nested reservation. Raising is a defensive
    backstop for any future caller which skips that middleware.
    """
    if not try_enter():
        raise RuntimeError("agent runner is draining")


def leave() -> None:
    global _active, _last, _revision
    with _lock:
        _active = max(0, _active - 1)
        _revision += 1
        _last = time.time()


def snapshot() -> tuple[int, float]:
    with _lock:
        return _active, _last


def state() -> tuple[int, float, int, bool]:
    """Return one atomic health/drain snapshot."""
    with _lock:
        return _active, _last, _revision, _drain_gate


def begin_drain(expected_revision: int) -> bool:
    """Permanently close admission iff the observed idle revision is current."""
    global _drain_gate
    with _lock:
        if _active != 0 or int(expected_revision) != _revision:
            return False
        # Revision represents activity only, not drain control. A repeated
        # request with the same idle revision is therefore idempotent, including
        # when the first HTTP response was lost.
        _drain_gate = True
        return True


def force_drain() -> tuple[int, int]:
    """Permanently close admission without waiting for the runtime to become idle.

    This is reserved for authenticated lifecycle teardown (offboarding/purge).
    Existing requests retain their activity slots and can finish or be terminated
    by Kubernetes, but no new request can enter after this function owns the
    lock. The returned pair is ``(active_runs, activity_revision)``.
    """
    global _drain_gate
    with _lock:
        _drain_gate = True
        return _active, _revision
