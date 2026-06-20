"""control-panel ext_proc EPP decision logic (no cluster): given request headers,
returns a steer (x-gateway-destination-endpoint) when authed+awake, or an
immediate response (401 unauth / 503 waking). auth + provisioner are faked."""

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


def _run(coro):
    return asyncio.run(coro)


def test_epp_steers_when_authed_and_awake(monkeypatch):
    async def fake_auth(token, xuser):
        return _User()
    monkeypatch.setattr(X, "authenticate_raw_token", fake_auth)
    monkeypatch.setattr(X.provisioner, "wake_and_wait", lambda aid: "10.1.2.3:8091")

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


def test_epp_503_when_waking(monkeypatch):
    async def fake_auth(token, xuser):
        return _User()
    monkeypatch.setattr(X, "authenticate_raw_token", fake_auth)
    monkeypatch.setattr(X.provisioner, "wake_and_wait", lambda aid: None)
    resp = _run(X.handle_request_headers(_hh({"authorization": "Bearer t"})))
    assert resp.WhichOneof("response") == "immediate_response"
    assert resp.immediate_response.status.code == 503


def test_epp_reads_token_from_query(monkeypatch):
    seen = {}

    async def fake_auth(token, xuser):
        seen["token"] = token
        return _User()
    monkeypatch.setattr(X, "authenticate_raw_token", fake_auth)
    monkeypatch.setattr(X.provisioner, "wake_and_wait", lambda aid: "10.1.2.3:8091")
    _run(X.handle_request_headers(_hh({":path": "/api/agent/ws/run?token=abc123"})))
    assert seen["token"] == "abc123"
