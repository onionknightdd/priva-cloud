"""The cp-proxy SSE lane must relay a stream whole, not buffer or truncate it.

/api/sandbox rides agentgateway's GIE InferencePool, whose EPP ext_proc is
hardcoded to FullDuplexStreamed and cuts every response body at ~8KB — an event
stream included, so a real run's `result` event never reaches the client. The
cp-proxy lane exists to carry those bytes past the ext_proc (ADR 0003); these
tests pin the two properties that make it usable for SSE: the whole body
survives, and events arrive as they are produced rather than at the end.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest
import uvicorn

from priva_control_panel.app import create_app

# Comfortably past the ~8KB ext_proc cut-off this lane exists to avoid.
FILLER_EVENTS = 40
FILLER_BYTES = 500
# With ?slow=1 the fake runner stalls this long mid-stream, standing in for a
# model that is still thinking. Long enough that a buffering proxy cannot hide it.
STALL_SECONDS = 1.5


class _FakeRunner(BaseHTTPRequestHandler):
    """Stands in for the account's pod: emits an SSE run far larger than 8KB."""

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", 0) or 0))
        if self.headers.get("X-Priva-Runner-Token") != "minted-token":
            self.send_response(401)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self._emit("stream_init", {"stream_id": "s1"})
        if "slow=1" in self.path:
            time.sleep(STALL_SECONDS)
        for i in range(FILLER_EVENTS):
            self._emit("assistant", {"seq": i, "text": "x" * FILLER_BYTES})
        self._emit("result", {"type": "result", "session_id": "sid-42", "result": "done"})

    def _emit(self, event, data):
        self.wfile.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())
        self.wfile.flush()

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_runner():
    srv = HTTPServer(("127.0.0.1", 0), _FakeRunner)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def client(monkeypatch, fake_runner):
    """A real uvicorn server, with the EPP's steering preamble stubbed.

    Not TestClient: httpx's ASGI transport collects the whole response body
    before handing it back, so every stream looks buffered through it and a
    relay-vs-buffer assertion could never fail. Streaming only means anything
    over a real socket.
    """
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

    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=0, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    # trust_env=False: a host/system proxy must not sit between us and 127.0.0.1.
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False,
                      timeout=httpx.Timeout(10.0, read=None)) as c:
        yield c
    server.should_exit = True
    thread.join(timeout=10)


def _post_stream(client, query=""):
    return client.stream(
        "POST",
        f"/api/cp-proxy/agent/run/stream{query}",
        json={"message": "hi"},
        headers={"Authorization": "Bearer t", "Accept": "text/event-stream"},
    )


def test_sse_body_survives_past_the_extproc_cutoff(client):
    with _post_stream(client) as res:
        assert res.status_code == 200
        assert "text/event-stream" in res.headers["content-type"]
        body = "".join(res.iter_text())

    # The failure this guards against is a body cut at ~8KB, which drops `result`.
    assert len(body) > 8192 * 2
    assert "event: result" in body
    assert json.loads(body.rsplit("data: ", 1)[1].strip())["session_id"] == "sid-42"
    assert body.count("event: assistant") == FILLER_EVENTS


def test_stream_is_relayed_not_buffered(client):
    """The first event must arrive while the upstream is still stalled.

    The upstream emits stream_init, then holds the connection open for
    STALL_SECONDS before sending anything else. A proxy that buffers the body
    (the pre-existing `r.content` path) could only answer after that stall, so
    time-to-first-event is what separates relaying from buffering here.
    """
    started = time.monotonic()
    with _post_stream(client, "?slow=1") as res:
        first = next(res.iter_lines())
        elapsed = time.monotonic() - started

    assert first == "event: stream_init"
    assert elapsed < STALL_SECONDS / 2, f"first event took {elapsed:.2f}s — body was buffered"


def test_sse_response_is_not_cacheable(client):
    with _post_stream(client) as res:
        assert res.headers["cache-control"] == "no-cache"
        assert res.headers["x-accel-buffering"] == "no"


def test_non_sse_responses_are_relayed_too(client):
    """Ordinary responses are relayed as well, not buffered.

    This used to be the negative control, asserting the non-SSE path could not
    answer before the upstream finished. That buffering was itself the problem:
    the lane exists to carry LARGE bodies (100MB downloads, big transcripts), so
    `r.content` held the whole thing in the shared control-plane process. Both
    paths now stream; the SSE assertion above stands on its own, since a
    buffering proxy cannot physically emit an event before the upstream's stall
    elapses.
    """
    started = time.monotonic()
    with client.stream(
        "POST", "/api/cp-proxy/agent/run/stream?slow=1",
        json={"message": "hi"}, headers={"Authorization": "Bearer t"},
    ) as res:
        assert res.status_code == 200
        lines = res.iter_lines()
        first = next(lines)
        elapsed = time.monotonic() - started
        body = "\n".join([first, *lines])

    assert first == "event: stream_init"
    assert elapsed < STALL_SECONDS / 2, (
        f"first byte took {elapsed:.2f}s — the response was buffered")
    assert "event: result" in body
