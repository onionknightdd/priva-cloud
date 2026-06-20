"""Dev-edge reverse-proxy: control-panel -> agent-runner.

The SPA talks to a single origin (the control-panel). This router forwards the
runtime + agent-coupled routes to the agent-runner over localhost, minting a
short-TTL signed ``X-Priva-Runner-Token`` from the browser's already-validated
platform session (the browser never sees the runner token). Three transport
classes: plain HTTP/JSON, SSE (streamed, unbuffered), and WebSocket (bidirable
relay with close-code propagation). Forwarding is driven by an explicit prefix
list, not route fall-through.
"""

from __future__ import annotations

import asyncio
import os

import httpx
import websockets
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse

from priva_common.logging import get_app_logger
from priva_common.runner_token import mint

from .services.auth import authenticate_raw_token

logger = get_app_logger(__name__)

router = APIRouter()


def _agent_runner_url() -> str:
    return os.environ.get("AGENT_RUNNER_URL", "http://127.0.0.1:8091").rstrip("/")


def _ws_base() -> str:
    return _agent_runner_url().replace("http://", "ws://").replace("https://", "wss://")

# HTTP prefixes forwarded to the agent-runner (config + execution faces all live
# in the runner this phase; CP serves auth/admin/user_data/resource directly).
HTTP_PREFIXES = [
    "/api/agent",
    "/api/files",
    "/api/user/files",
    "/api/hooks",
    "/api/resource/mcp",
    "/api/resource/skills",
    "/api/resource/skill-hub",
    "/api/subagents",
    "/api/pty/feature",
    "/api/admin/pty",
]

# Hop-by-hop headers we must not forward.
_DROP_HEADERS = {"host", "content-length", "connection", "keep-alive", "transfer-encoding", "accept-encoding"}


async def _runner_token_from_request(request: Request) -> str:
    """Resolve the browser's platform session to a signed runner token."""
    auth = request.headers.get("authorization") or ""
    token = auth[7:] if auth.lower().startswith("bearer ") else None
    user = await authenticate_raw_token(token, request.headers.get("x-user-name"))
    if user is None:
        raise HTTPException(401, "Authentication required")
    return mint(user.account_id, user.username)


def _fwd_headers(request: Request, runner_token: str) -> dict[str, str]:
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_HEADERS}
    headers["X-Priva-Runner-Token"] = runner_token
    return headers


async def _proxy_http(request: Request, path: str) -> Response:
    runner_token = await _runner_token_from_request(request)
    url = f"{_agent_runner_url()}{request.url.path}"
    if request.url.query:
        url += f"?{request.url.query}"
    body = await request.body()
    headers = _fwd_headers(request, runner_token)

    # SSE / streamed responses: stream raw bytes through, no buffering.
    wants_stream = "text/event-stream" in (request.headers.get("accept") or "") or request.url.path.endswith("/stream")

    # follow_redirects so AR's trailing-slash 307s (which point at AR's own
    # origin) are resolved here instead of leaking to the browser, which would
    # otherwise be bounced straight at the runner and bypass the proxy.
    client = httpx.AsyncClient(timeout=httpx.Timeout(None), follow_redirects=True)
    req = client.build_request(request.method, url, headers=headers, content=body)

    if wants_stream:
        resp = await client.send(req, stream=True)

        async def _aiter():
            try:
                async for chunk in resp.aiter_raw():
                    yield chunk
            finally:
                await resp.aclose()
                await client.aclose()

        passthrough = {k: v for k, v in resp.headers.items() if k.lower() not in _DROP_HEADERS | {"content-encoding"}}
        return StreamingResponse(
            _aiter(),
            status_code=resp.status_code,
            headers=passthrough,
            media_type=resp.headers.get("content-type", "text/event-stream"),
        )

    resp = await client.send(req)
    content = resp.content
    await client.aclose()
    passthrough = {k: v for k, v in resp.headers.items() if k.lower() not in _DROP_HEADERS | {"content-encoding"}}
    return Response(content=content, status_code=resp.status_code, headers=passthrough, media_type=resp.headers.get("content-type"))


def _register_http_prefix(prefix: str) -> None:
    methods = ["GET", "POST", "PUT", "DELETE", "PATCH"]

    async def _bare(request: Request):
        return await _proxy_http(request, "")

    async def _sub(request: Request, path: str):
        return await _proxy_http(request, path)

    router.add_api_route(prefix, _bare, methods=methods, include_in_schema=False, name=f"proxy{prefix}")
    router.add_api_route(prefix + "/{path:path}", _sub, methods=methods, include_in_schema=False, name=f"proxy{prefix}-sub")


for _p in HTTP_PREFIXES:
    _register_http_prefix(_p)


# --- WebSocket relay ---------------------------------------------------------

async def _relay_ws(client_ws: WebSocket, upstream_path: str) -> None:
    """Relay a browser WebSocket to the agent-runner.

    The agent/pty protocols send the platform token in the first client message
    (init frame), so we accept, peek that frame to mint the runner token, open
    the upstream socket with the signed header, replay the init frame, then pump
    both directions until either side closes (propagating the close code).
    """
    await client_ws.accept()
    try:
        first = await client_ws.receive_text()
    except (WebSocketDisconnect, Exception):
        return

    # Extract the platform token from the init frame to mint the runner token.
    import json
    try:
        init = json.loads(first)
    except Exception:
        init = {}
    token = init.get("token")
    try:
        user = await authenticate_raw_token(token, init.get("x_user_name"))
        if user is None:
            raise HTTPException(401, "auth")
        runner_token = mint(user.account_id, user.username)
    except Exception:
        await client_ws.close(code=4001)
        return

    upstream_url = f"{_ws_base()}{upstream_path}"
    try:
        upstream = await websockets.connect(
            upstream_url,
            additional_headers={"X-Priva-Runner-Token": runner_token},
            ping_interval=None,
            max_size=None,
        )
    except Exception as exc:
        logger.warning("upstream ws connect failed: {}", exc)
        await client_ws.close(code=4500)
        return

    # Replay the init frame upstream.
    await upstream.send(first)

    async def _client_to_upstream():
        try:
            while True:
                msg = await client_ws.receive_text()
                await upstream.send(msg)
        except (WebSocketDisconnect, Exception):
            return

    async def _upstream_to_client():
        try:
            async for msg in upstream:
                if isinstance(msg, bytes):
                    await client_ws.send_bytes(msg)
                else:
                    await client_ws.send_text(msg)
        except Exception:
            return

    t1 = asyncio.create_task(_client_to_upstream())
    t2 = asyncio.create_task(_upstream_to_client())
    done, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()

    # Propagate the upstream close code to the browser where possible.
    close_code = 1000
    try:
        close_code = upstream.close_code or 1000
    except Exception:
        pass
    try:
        await upstream.close()
    except Exception:
        pass
    try:
        await client_ws.close(code=close_code)
    except Exception:
        pass


@router.websocket("/api/agent/ws/run")
async def proxy_agent_ws(websocket: WebSocket):
    await _relay_ws(websocket, "/api/agent/ws/run")


@router.websocket("/api/pty/ws")
async def proxy_pty_ws(websocket: WebSocket):
    await _relay_ws(websocket, "/api/pty/ws")
