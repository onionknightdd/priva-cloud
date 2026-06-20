"""``priva-cloud serve`` — supervise all discovered services as subprocesses.

Spawns each registered service as a ``priva-cloud <name>`` subprocess (so each
runs in its own process with its own entry-point), prefix-aggregates their
output, and forwards SIGINT/SIGTERM. The direct successor of the old
``priva/bin/server.sh`` three-daemon launch. ``--only a,b`` restricts the set.

Boot order: data-spine first (others compose against it), then the rest.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import threading

from .discovery import registered

# Preferred boot order; unknown services are appended in discovery order.
_ORDER = ["data-spine", "agent-runner", "control-panel"]


def _ordered(names: list[str]) -> list[str]:
    known = [n for n in _ORDER if n in names]
    rest = [n for n in names if n not in _ORDER]
    return known + rest


def _pump(name: str, stream) -> None:
    for raw in iter(stream.readline, b""):
        line = raw.decode(errors="replace").rstrip("\n")
        print(f"[{name}] {line}", flush=True)


def run(argv: list[str]) -> int:
    only: set[str] | None = None
    rest: list[str] = []
    it = iter(argv)
    for arg in it:
        if arg == "--only":
            only = {s.strip() for s in next(it, "").split(",") if s.strip()}
        elif arg.startswith("--only="):
            only = {s.strip() for s in arg.split("=", 1)[1].split(",") if s.strip()}
        else:
            rest.append(arg)

    available = registered()
    names = [n for n in available if only is None or n in only]
    if not names:
        print("priva-cloud serve: no matching services installed", file=sys.stderr)
        return 1
    names = _ordered(names)

    # data-spine is not a long-running daemon in the in_process transport (the
    # Phase-1/alpha default): AR and CP each compose() it against the shared
    # SQLite. So we run its schema init as a one-shot pre-step and drop it from
    # the supervised set rather than spawning a server that doesn't exist.
    if "data-spine" in names:
        names.remove("data-spine")
        print("[serve] data-spine init (in_process — no daemon)", flush=True)
        rc = subprocess.run([sys.executable, "-m", "priva_cli", "data-spine", "init"])
        if rc.returncode != 0:
            print("[serve] data-spine init failed", file=sys.stderr)
            return rc.returncode
    if not names:
        print("priva-cloud serve: nothing to supervise (only data-spine requested)", file=sys.stderr)
        return 0

    procs: list[tuple[str, subprocess.Popen]] = []
    threads: list[threading.Thread] = []

    def _shutdown(*_):
        for _name, p in procs:
            if p.poll() is None:
                p.terminate()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print(f"priva-cloud serve: starting {', '.join(names)}", flush=True)
    for name in names:
        p = subprocess.Popen(
            [sys.executable, "-m", "priva_cli", name, *rest],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        procs.append((name, p))
        t = threading.Thread(target=_pump, args=(name, p.stdout), daemon=True)
        t.start()
        threads.append(t)

    # Wait for any to exit; then tear the rest down.
    exit_code = 0
    try:
        while procs:
            for name, p in list(procs):
                code = p.poll()
                if code is not None:
                    print(f"[serve] {name} exited with {code}", flush=True)
                    exit_code = code or exit_code
                    procs.remove((name, p))
                    _shutdown()
            if not procs:
                break
            import time
            time.sleep(0.3)
    except KeyboardInterrupt:
        _shutdown()
    for _name, p in procs:
        try:
            p.wait(timeout=10)
        except Exception:
            p.kill()
    return exit_code
