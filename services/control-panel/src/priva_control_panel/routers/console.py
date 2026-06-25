"""Admin control-plane console — a web terminal INTO a control-plane pod
(control-panel / operator / data-spine), served by the control-panel itself.

This is NOT the per-account agent-runner terminal (that rides ``/api/pty/ws`` and
is steered to the account's pod by the EPP). Control-plane pods don't run the pty
router and aren't on the InferencePool, so the control-panel bridges a **Kubernetes
exec** PTY (``connect_get_namespaced_pod_exec``) to the browser WebSocket here.

Security: admin-only, and high-privilege — the control-panel ServiceAccount holds
cluster RBAC and reaches the secret-backed data-spine. Every open is audited.
The path ``/api/admin/console/ws`` is served by the control-panel directly (the
gateway's catch-all), so we authenticate the platform JWT off the handshake
ourselves (it rides the ``Sec-WebSocket-Protocol`` header, same as the SPA).
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from priva_common.audit_log import AuditEntry, get_audit_logger
from priva_common.config import get_settings
from priva_common.logging import get_app_logger

logger = get_app_logger(__name__)

router = APIRouter(tags=["console"])

WS_SUBPROTOCOL = "priva.ws.v1"
WS_TOKEN_PREFIX = "priva.token."
WS_TARGET_PREFIX = "priva.target."

# Allowlisted control-plane targets -> pod ``app`` label. The agent-runner accounts
# are deliberately NOT here (they use /api/pty/ws via the EPP).
CONTROL_PLANE_TARGETS = {
    "control-panel": "control-panel",
    "operator": "operator",
    "data-spine": "data-spine",
}


def _subprotocols(websocket: WebSocket) -> list[str]:
    raw = websocket.headers.get("sec-websocket-protocol", "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def _find(parts: list[str], prefix: str) -> str | None:
    for p in parts:
        if p.startswith(prefix):
            return p[len(prefix):] or None
    return None


def _b64url_decode(raw: str | None) -> str | None:
    if not raw:
        return None
    import base64
    try:
        pad = "=" * (-len(raw) % 4)
        return base64.urlsafe_b64decode(raw + pad).decode("utf-8") or None
    except Exception:
        return None


async def _send_close(ws: WebSocket, reason: str, code: int) -> None:
    try:
        await ws.send_json({"type": "closed", "reason": reason})
    except Exception:
        pass
    try:
        await ws.close(code=code)
    except Exception:
        pass


def _resolve_pod(app_label: str) -> str | None:
    """Name of a Running pod with ``app=<app_label>`` in the system namespace."""
    from .. import provisioner
    s = get_settings()
    ns = s.kubernetes.namespace_system
    provisioner._load()
    from kubernetes import client
    try:
        resp = client.CoreV1Api().list_namespaced_pod(ns, label_selector=f"app={app_label}")
    except Exception as exc:  # pragma: no cover
        logger.warning("console: list pods app={} failed: {}", app_label, exc)
        return None
    for p in resp.items:
        if p.status and p.status.phase == "Running" and p.metadata and p.metadata.name:
            return p.metadata.name
    return None


def _exec_worker(pod: str, ns: str, cols: int, rows: int,
                 loop: asyncio.AbstractEventLoop,
                 out_q: "asyncio.Queue", in_q: "queue.Queue", stop: threading.Event) -> None:
    """Runs in a thread: owns the k8s exec WSClient end-to-end (read + write in one
    thread so the underlying socket is never touched concurrently). Shuttles output
    to the asyncio side via call_soon_threadsafe, and drains input from a thread-safe
    queue."""
    from kubernetes import client
    from kubernetes.stream import stream
    from kubernetes.stream.ws_client import RESIZE_CHANNEL

    def emit(kind: str, data) -> None:
        loop.call_soon_threadsafe(out_q.put_nowait, (kind, data))

    try:
        resp = stream(
            client.CoreV1Api().connect_get_namespaced_pod_exec,
            pod, ns,
            # Prefer an interactive login bash, fall back to sh. Do NOT redirect the
            # shell's stderr — bash writes its prompt (PS1) to stderr, and with tty=True
            # the pty merges stderr into stdout, so swallowing it leaves a blank terminal.
            command=["/bin/sh", "-c", "if command -v bash >/dev/null 2>&1; then exec bash -il; else exec sh -i; fi"],
            stderr=True, stdin=True, stdout=True, tty=True,
            _preload_content=False,
        )
    except Exception as exc:
        emit("error", f"exec failed: {exc}")
        emit("closed", "process_exit")
        return

    try:
        resp.write_channel(RESIZE_CHANNEL, json.dumps({"Width": cols, "Height": rows}))
    except Exception:
        pass

    try:
        while resp.is_open() and not stop.is_set():
            resp.update(timeout=0.1)
            if resp.peek_stdout():
                emit("output", resp.read_stdout())
            if resp.peek_stderr():
                emit("output", resp.read_stderr())
            try:
                while True:
                    kind, payload = in_q.get_nowait()
                    if kind == "input":
                        resp.write_stdin(payload)
                    elif kind == "resize":
                        resp.write_channel(RESIZE_CHANNEL, payload)
            except queue.Empty:
                pass
    except Exception as exc:
        emit("error", str(exc))
    finally:
        try:
            resp.close()
        except Exception:
            pass
        emit("closed", "process_exit")


@router.websocket("/api/admin/console/ws")
async def control_plane_console(websocket: WebSocket):
    parts = _subprotocols(websocket)
    sub = WS_SUBPROTOCOL if WS_SUBPROTOCOL in parts else None
    await websocket.accept(subprotocol=sub)

    # --- auth: admin JWT off the handshake (this path bypasses the EPP) ---
    from ..services.auth import authenticate_raw_token
    token = _find(parts, WS_TOKEN_PREFIX)
    try:
        user = await authenticate_raw_token(token, None)
    except Exception:
        user = None
    if user is None:
        await _send_close(websocket, "auth", 4001)
        return
    if getattr(user, "role", "") != "admin":
        await _send_close(websocket, "admin_required", 4001)
        return

    target = _b64url_decode(_find(parts, WS_TARGET_PREFIX))
    app_label = CONTROL_PLANE_TARGETS.get(target or "")
    if not app_label:
        await _send_close(websocket, "bad_target", 4002)
        return

    # First frame carries the initial terminal size.
    try:
        init = await websocket.receive_json()
    except Exception:
        await _send_close(websocket, "no_init", 4002)
        return
    cols = max(20, min(500, int(init.get("cols") or 80)))
    rows = max(5, min(200, int(init.get("rows") or 24)))

    pod = await asyncio.to_thread(_resolve_pod, app_label)
    if not pod:
        await _send_close(websocket, "pod_unavailable", 1011)
        return

    ns = get_settings().kubernetes.namespace_system
    try:  # high-privilege action — always audited
        get_audit_logger().append(AuditEntry(
            actor=user.username,
            action="admin.console_open",
            target=f"control-plane:{target}",
            details={"pod": pod, "namespace": ns},
        ))
    except Exception:  # pragma: no cover
        pass

    await websocket.send_json({
        "type": "ready",
        "session_id": uuid.uuid4().hex[:16],
        "cwd": f"{target} · {pod}",
        "cols": cols, "rows": rows,
    })

    loop = asyncio.get_running_loop()
    out_q: asyncio.Queue = asyncio.Queue()
    in_q: queue.Queue = queue.Queue()
    stop = threading.Event()
    worker = threading.Thread(
        target=_exec_worker, args=(pod, ns, cols, rows, loop, out_q, in_q, stop), daemon=True)
    worker.start()

    async def pump_output() -> None:
        while True:
            kind, data = await out_q.get()
            if kind == "output":
                await websocket.send_json({"type": "output", "data": data})
            elif kind == "error":
                await websocket.send_json({"type": "error", "message": data})
            elif kind == "closed":
                await websocket.send_json({"type": "closed", "reason": data})
                return

    out_task = asyncio.create_task(pump_output())
    try:
        while True:
            msg = await websocket.receive_json()
            t = msg.get("type")
            if t == "input":
                in_q.put(("input", msg.get("data", "")))
            elif t == "resize":
                c = max(20, min(500, int(msg.get("cols") or cols)))
                r = max(5, min(200, int(msg.get("rows") or rows)))
                in_q.put(("resize", json.dumps({"Width": c, "Height": r})))
            elif t == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # pragma: no cover
        logger.warning("console ws error pod={}: {}", pod, exc)
    finally:
        stop.set()
        out_task.cancel()
        try:
            await out_task
        except Exception:
            pass
