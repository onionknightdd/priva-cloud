"""In-pod activity tracker the operator reads via /health to decide idle->sleep.

active_runs = in-flight requests (a streaming run / parked permission WebSocket
keeps the connection open, so it counts and the pod won't be slept mid-turn).
last_activity_ts = epoch of the last request boundary. /health probes are NOT
counted (else the operator's own polling would keep the pod awake forever).
"""

from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_active = 0
_last = time.time()


def enter() -> None:
    global _active, _last
    with _lock:
        _active += 1
        _last = time.time()


def leave() -> None:
    global _active, _last
    with _lock:
        _active = max(0, _active - 1)
        _last = time.time()


def snapshot() -> tuple[int, float]:
    with _lock:
        return _active, _last
