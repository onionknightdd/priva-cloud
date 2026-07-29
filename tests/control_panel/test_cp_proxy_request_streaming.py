"""The cp-proxy must relay the REQUEST body as a stream, not buffer it.

The proxy used to read the whole body before dialling the pod, so an upload was
resident in the shared control-plane process — in full, per concurrent request —
before a single byte reached the tenant's runner. The size ceiling is enforced
upstream of the relay by MaxBodySizeMiddleware on the ASGI receive channel, so
buffering here bought no protection.

Buffering is observable without measuring memory: a proxy that buffers cannot
send request headers upstream until it has read the whole body, so the upstream
handler is not entered until the client has finished uploading. A streaming
proxy connects immediately. That gap is what these tests measure — the same
shape as the SSE relay assertion in test_cp_proxy_sse.py.

The fake runner is uvicorn, matching the real pod's stack: a chunked request
body needs a server that supports one (``http.server`` does not, and a fake
built on it fails a correct streaming proxy).
"""

import socket
import threading
import time

import httpx
import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from priva_control_panel.app import MAX_PROXY_BODY_BYTES, create_app

# The client dribbles its body over this long. A buffering proxy cannot contact
# the upstream until it elapses.
UPLOAD_SECONDS = 1.5
CHUNK = b"x" * 8192
CHUNKS = 6

_entered_at: list[float] = []


def _serve(app):
    """Start a uvicorn server in a thread; the caller must call the returned
    stop(). create_app() binds the ext_proc port (fixed 9000) in its lifespan, so
    a server that is not fully joined blocks the next test from starting one."""
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.01)

    def stop():
        server.should_exit = True
        thread.join(timeout=10)

    return stop, server.servers[0].sockets[0].getsockname()[1]


@pytest.fixture
def fake_runner():
    async def handler(request):
        _entered_at.append(time.monotonic())
        body = await request.body()
        return JSONResponse({
            "received": len(body),
            "chunked": request.headers.get("transfer-encoding") == "chunked",
            "token_ok": request.headers.get("x-priva-runner-token") == "minted-token",
        })

    app = Starlette(routes=[Route("/{path:path}", handler, methods=["POST"])])
    stop, port = _serve(app)
    _entered_at.clear()
    yield f"127.0.0.1:{port}"
    stop()


@pytest.fixture
def proxy(monkeypatch, fake_runner):
    """A real uvicorn server — an in-process ASGI transport would collect the
    whole body first and make every request look buffered."""
    import priva_common.runner_token as runner_token
    from priva_control_panel import provisioner
    from priva_control_panel.services import auth

    class _User:
        account_id = "acct-1"
        username = "alice"
        status = "active"

    async def _authenticate(token, user_name=None):
        return _User() if token else None

    async def _wake(account_id):
        return fake_runner

    monkeypatch.setattr(auth, "authenticate_raw_token", _authenticate)
    monkeypatch.setattr(provisioner, "wake_and_wait", _wake)
    monkeypatch.setattr(runner_token, "mint", lambda *a, **k: "minted-token")

    stop, port = _serve(create_app())
    yield port
    stop()


@pytest.fixture
def client(proxy):
    with httpx.Client(base_url=f"http://127.0.0.1:{proxy}", trust_env=False,
                      timeout=httpx.Timeout(30.0, read=None)) as c:
        yield c


def _slow_body():
    """Dribble the body out so a buffered relay is measurably late."""
    for i in range(CHUNKS):
        if i:
            time.sleep(UPLOAD_SECONDS / CHUNKS)
        yield CHUNK


def test_the_pod_is_reached_before_the_upload_finishes(client):
    started = time.monotonic()
    res = client.post(
        "/api/cp-proxy/agent/files/upload",
        content=_slow_body(),
        headers={"Authorization": "Bearer t", "Content-Type": "application/octet-stream"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["received"] == len(CHUNK) * CHUNKS
    assert res.json()["token_ok"]        # steering still intact

    assert _entered_at, "the upstream was never reached"
    entered = _entered_at[0] - started
    assert entered < UPLOAD_SECONDS / 2, (
        f"upstream entered after {entered:.2f}s — the body was buffered before dialling"
    )


def test_a_known_length_is_forwarded_rather_than_chunked(client):
    """Preserving Content-Length keeps the pod on an ordinary sized body (and
    keeps its own cheap pre-check working); only an unknown length falls back to
    Transfer-Encoding: chunked."""
    payload = b"y" * 4096
    res = client.post(
        "/api/cp-proxy/agent/files/upload",
        content=payload,
        headers={"Authorization": "Bearer t", "Content-Type": "application/octet-stream"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["received"] == len(payload)
    assert res.json()["chunked"] is False


def test_an_unknown_length_relays_as_chunked(client):
    res = client.post(
        "/api/cp-proxy/agent/files/upload",
        content=(c for c in [b"a" * 1024, b"b" * 1024]),
        headers={"Authorization": "Bearer t", "Content-Type": "application/octet-stream"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["received"] == 2048
    assert res.json()["chunked"] is True


def test_an_oversized_declared_body_is_refused_before_any_of_it_is_read(proxy):
    """Streaming must not have opened the ceiling. Sent over a raw socket: the
    point is that the 413 comes back from the headers alone, without the client
    ever uploading a body, so no httpx length bookkeeping is involved."""
    request = (
        "POST /api/cp-proxy/agent/files/upload HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{proxy}\r\n"
        "Authorization: Bearer t\r\n"
        "Content-Type: application/octet-stream\r\n"
        f"Content-Length: {MAX_PROXY_BODY_BYTES + 1}\r\n"
        "\r\n"
    ).encode()

    with socket.create_connection(("127.0.0.1", proxy), timeout=10) as sock:
        sock.sendall(request)          # headers only — not one byte of body
        sock.settimeout(10)
        status = sock.recv(64).decode(errors="replace")

    assert "413" in status, status
    assert not _entered_at, "the pod was dialled for a body that should never have been read"
