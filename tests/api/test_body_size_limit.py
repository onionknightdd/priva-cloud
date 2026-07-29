"""Request bodies must be bounded BEFORE anything parses or spools them.

Per-route ``await file.read(MAX + 1)`` bounds memory only. With ``UploadFile``,
Starlette's multipart parser runs during dependency resolution — before the
endpoint — and streams every file part into a ``SpooledTemporaryFile`` that
rolls to disk past 1 MB. ``max_part_size`` guards only NON-file parts
(``formparsers.on_part_data``: the check is inside ``if part.file is None``), so
a file part is unbounded at the parser. A 20 MB upload to a 3 MB route was fully
written to the container's /tmp before the route could reject it — and the
runner's /tmp emptyDir had no sizeLimit, making that node ephemeral storage.
"""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.testclient import TestClient

from priva_common.body_limit import MaxBodySizeMiddleware

LIMIT = 2 * 1024 * 1024


@pytest.fixture
def app_and_state():
    state: dict = {}
    app = FastAPI()

    @app.post("/upload")
    async def upload(file: UploadFile = File(...)):
        state["reached"] = True
        state["spooled_bytes"] = (
            os.fstat(file.file.fileno()).st_size if getattr(file.file, "_rolled", False) else 0
        )
        return {"ok": True}

    @app.post("/raw")
    async def raw(request: Request):
        state["reached"] = True
        state["body"] = len(await request.body())
        return {"ok": True}

    app.add_middleware(MaxBodySizeMiddleware, max_bytes=LIMIT)
    return app, state


def _chunks(total: int, size: int = 64 * 1024):
    sent = 0
    while sent < total:
        n = min(size, total - sent)
        sent += n
        yield b"\0" * n


def test_oversized_multipart_never_reaches_the_parser(app_and_state):
    app, state = app_and_state
    with TestClient(app) as client:
        resp = client.post(
            "/upload", files={"file": ("big.zip", b"\0" * (20 * 1024 * 1024), "application/zip")})
    assert resp.status_code == 413
    assert not state.get("reached"), "the endpoint ran, so the body was already spooled"


def test_a_chunked_body_without_content_length_is_still_bounded(app_and_state):
    """Content-Length is client-supplied and absent on chunked transfers, so the
    running byte count — not the header — is what enforces the limit."""
    app, state = app_and_state
    with TestClient(app) as client:
        resp = client.post("/raw", content=_chunks(20 * 1024 * 1024))
    assert resp.status_code == 413
    # The handler is entered (it is what consumes the stream), but the read is
    # cut off — it never sees a complete body.
    assert "body" not in state


def test_a_lying_content_length_does_not_get_through(app_and_state):
    """Understating the declared length must not buy a bigger body."""
    app, state = app_and_state
    with TestClient(app) as client:
        resp = client.post(
            "/raw", content=_chunks(20 * 1024 * 1024), headers={"content-length": "10"})
    assert resp.status_code == 413
    assert "body" not in state   # the running count caught what the header hid


def test_bodies_under_the_limit_pass_through_untouched(app_and_state):
    app, state = app_and_state
    with TestClient(app) as client:
        ok = client.post("/upload", files={"file": ("s.zip", b"\0" * 1024, "application/zip")})
        assert ok.status_code == 200 and state["reached"]

        state.clear()
        raw = client.post("/raw", content=b"x" * 4096)
        assert raw.status_code == 200 and state["body"] == 4096


def test_get_requests_are_not_disturbed(app_and_state):
    app, _ = app_and_state

    @app.get("/ping")
    async def ping():
        return {"pong": True}

    with TestClient(app) as client:
        assert client.get("/ping").status_code == 200


def test_the_runner_app_actually_installs_the_limit():
    """The cap must be WIRED, not merely defined.

    Asserting the constant passed while `add_middleware` was deleted outright —
    the pod that receives tenant uploads could lose its entire body ceiling with
    a green suite. Inspect the built app's middleware stack instead.
    """
    from priva_agent_runner.app import create_app

    installed = [m.cls for m in create_app().user_middleware]
    assert MaxBodySizeMiddleware in installed, (
        "agent-runner does not install MaxBodySizeMiddleware")


def test_the_control_panel_app_actually_installs_the_limit():
    from priva_control_panel.app import create_app

    installed = [m.cls for m in create_app().user_middleware]
    assert MaxBodySizeMiddleware in installed
