"""control-panel ext_proc EPP decision logic (no cluster): given request headers,
returns a steer (x-gateway-destination-endpoint) when authed+awake, or an immediate
response (401 unauth / 403 disabled / 503 waking). auth + provisioner are faked.

provisioner.wake_and_wait is now async, so the fakes are coroutines. The warm-path
liveness probe + per-account coalescing are exercised against the provisioner directly."""

from __future__ import annotations

import asyncio

from envoy.config.core.v3.base_pb2 import HeaderMap, HeaderValue
from envoy.service.ext_proc.v3 import external_processor_pb2 as ep

import priva_control_panel.extproc as X


def _hh(d: dict[str, str]):
    return ep.HttpHeaders(headers=HeaderMap(
        headers=[HeaderValue(key=k, raw_value=v.encode()) for k, v in d.items()]))


class _User:
    account_id = "acct-1"
    username = "alice"
    status = "active"


def _run(coro):
    return asyncio.run(coro)


def _awake(endpoint):
    """Build an async stand-in for provisioner.wake_and_wait returning ``endpoint``."""
    async def _f(account_id):
        return endpoint
    return _f


def test_epp_steers_when_authed_and_awake(monkeypatch):
    async def fake_auth(token, xuser):
        return _User()
    monkeypatch.setattr(X, "authenticate_raw_token", fake_auth)
    monkeypatch.setattr(X.provisioner, "wake_and_wait", _awake("10.1.2.3:8091"))

    resp = _run(X.handle_request_headers(_hh({"authorization": "Bearer t", ":path": "/api/agent/run/stream"})))
    assert resp.WhichOneof("response") == "request_headers"
    hdrs = {h.header.key: h.header.raw_value.decode()
            for h in resp.request_headers.response.header_mutation.set_headers}
    assert hdrs["x-gateway-destination-endpoint"] == "10.1.2.3:8091"
    assert hdrs["x-priva-runner-token"]  # signed token minted for the pod


def test_epp_401_when_unauthed(monkeypatch):
    async def fake_auth(token, xuser):
        return None
    monkeypatch.setattr(X, "authenticate_raw_token", fake_auth)
    resp = _run(X.handle_request_headers(_hh({})))
    assert resp.WhichOneof("response") == "immediate_response"
    assert resp.immediate_response.status.code == 401


def test_epp_403_when_disabled(monkeypatch):
    """A non-active account is pre-gated (403) BEFORE any wake — the pod stays asleep (#6)."""
    class _Disabled:
        account_id = "acct-1"
        username = "alice"
        status = "disabled"

    async def fake_auth(token, xuser):
        return _Disabled()
    monkeypatch.setattr(X, "authenticate_raw_token", fake_auth)

    woke = {"n": 0}

    async def fake_wake(account_id):
        woke["n"] += 1
        return "10.1.2.3:8091"
    monkeypatch.setattr(X.provisioner, "wake_and_wait", fake_wake)

    resp = _run(X.handle_request_headers(_hh({"authorization": "Bearer t"})))
    assert resp.WhichOneof("response") == "immediate_response"
    assert resp.immediate_response.status.code == 403
    assert woke["n"] == 0  # never waked a disabled account


def test_epp_503_when_waking(monkeypatch):
    async def fake_auth(token, xuser):
        return _User()
    monkeypatch.setattr(X, "authenticate_raw_token", fake_auth)
    monkeypatch.setattr(X.provisioner, "wake_and_wait", _awake(None))
    resp = _run(X.handle_request_headers(_hh({"authorization": "Bearer t"})))
    assert resp.WhichOneof("response") == "immediate_response"
    assert resp.immediate_response.status.code == 503


def test_epp_reads_token_from_query(monkeypatch):
    seen = {}

    async def fake_auth(token, xuser):
        seen["token"] = token
        return _User()
    monkeypatch.setattr(X, "authenticate_raw_token", fake_auth)
    monkeypatch.setattr(X.provisioner, "wake_and_wait", _awake("10.1.2.3:8091"))
    _run(X.handle_request_headers(_hh({":path": "/api/agent/ws/run?token=abc123"})))
    assert seen["token"] == "abc123"


def test_epp_reads_token_from_subprotocol(monkeypatch):
    """WS upgrade: the JWT rides the Sec-WebSocket-Protocol handshake header
    (`priva.ws.v1, priva.token.<jwt>`), not the URL or an Authorization header."""
    seen = {}

    async def fake_auth(token, xuser):
        seen["token"] = token
        return _User()
    monkeypatch.setattr(X, "authenticate_raw_token", fake_auth)
    monkeypatch.setattr(X.provisioner, "wake_and_wait", _awake("10.1.2.3:8091"))
    _run(X.handle_request_headers(_hh({
        ":path": "/api/agent/ws/run",
        "sec-websocket-protocol": "priva.ws.v1, priva.token.jwt.payload.sig",
    })))
    assert seen["token"] == "jwt.payload.sig"


def test_token_from_subprotocol_helper():
    assert X._token_from_subprotocol("priva.ws.v1, priva.token.aaa.bbb.ccc") == "aaa.bbb.ccc"
    assert X._token_from_subprotocol("priva.ws.v1") is None
    assert X._token_from_subprotocol("") is None


# --- provisioner.wake_and_wait: coalescing + warm-path liveness (no cluster) -----------

def test_wake_coalescing(monkeypatch):
    """N concurrent cold requests for one account collapse to a single spec.wake patch
    (#3/#4). Coalescing is per-process; the operator's idempotent on_wake is the real
    cross-replica guard."""
    P = X.provisioner
    P._wake_tasks.clear()
    state = {"patched": 0}

    def fake_patch(account_id):
        state["patched"] += 1

    def fake_status(account_id):
        return {"phase": "Running", "podIP": "10.0.0.9"} if state["patched"] else {}

    async def alive(ip, port):
        return True

    monkeypatch.setattr(P, "_patch_wake", fake_patch)
    monkeypatch.setattr(P, "_status", fake_status)
    monkeypatch.setattr(P, "_alive", alive)

    async def main():
        return await asyncio.gather(*[P.wake_and_wait("acct-1") for _ in range(50)])

    results = _run(main())
    assert all(r == "10.0.0.9:8091" for r in results)
    assert state["patched"] == 1


def test_warm_path_liveness(monkeypatch):
    """Running+podIP but the liveness probe fails -> fall through to a re-wake (#1/#2)."""
    P = X.provisioner
    P._wake_tasks.clear()
    state = {"patched": 0}

    monkeypatch.setattr(P, "_status", lambda account_id: {"phase": "Running", "podIP": "10.0.0.9"})

    async def dead_probe(ip, port):
        return False
    monkeypatch.setattr(P, "_alive", dead_probe)

    def fake_patch(account_id):
        state["patched"] += 1
    monkeypatch.setattr(P, "_patch_wake", fake_patch)

    result = _run(P.wake_and_wait("acct-1"))
    assert state["patched"] == 1  # probe failed -> re-wake patched spec.wake
    assert result == "10.0.0.9:8091"  # operator-healed status returned after re-wake
