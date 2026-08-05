"""WakeDialDispatcher against a scripted runner (httpx MockTransport) and a
fake waker — the 202/409/429/conn-fail admission matrix from design §10."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from priva_common.models.scheduler import (
    AgentRunConfig,
    FeishuCallbackConfig,
    ScheduledRunRequest,
)

from priva_scheduler import dispatch as dispatch_mod
from priva_scheduler.dispatch import DispatchError, WakeDialDispatcher


def frame() -> ScheduledRunRequest:
    return ScheduledRunRequest(
        run_id="r-1", job_id="j-1", job_name="daily",
        callback_token="signed-callback-capability",
        job_config=AgentRunConfig(
            prompt="brief me", callback=FeishuCallbackConfig(type="feishu"),
        ),
    )


async def _awake(account_id: str) -> bool:
    return True


def scripted_transport(statuses: list[int], seen: list[httpx.Request]):
    """Each request pops the next scripted status (last one repeats)."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        code = statuses.pop(0) if len(statuses) > 1 else statuses[0]
        return httpx.Response(code, json={"status": "x", "run_id": "r-1"})

    return httpx.MockTransport(handler)


def test_202_accepted_carries_token_and_frame(fast_settings):
    seen: list[httpx.Request] = []
    d = WakeDialDispatcher(waker=_awake, transport=scripted_transport([202], seen))

    assert asyncio.run(d.dispatch("acct-1", "carol", frame())) == "accepted"

    (req,) = seen
    assert req.url.path == "/api/sandbox/agent/scheduled-run"
    assert "ar-acct-1" in req.url.host
    assert req.headers.get("X-Priva-Runner-Token")  # minted per attempt
    import json
    body = json.loads(req.content)
    assert body["run_id"] == "r-1" and body["job_config"]["job_type"] == "agent_run"
    assert body["job_config"]["callback"] == {"type": "feishu"}
    assert body["callback_token"] == "signed-callback-capability"
    assert body["permission_mode"] == "bypassPermissions"


def test_409_is_immediate_job_overlap(fast_settings):
    seen: list = []
    d = WakeDialDispatcher(waker=_awake, transport=scripted_transport([409], seen))
    assert asyncio.run(d.dispatch("acct-1", "carol", frame())) == "job_overlap"
    assert len(seen) == 1  # never retried


def test_429_readmits_then_accepts(fast_settings, monkeypatch):
    monkeypatch.setattr(dispatch_mod, "_ADMISSION_RETRY_DELAYS", (0.01, 0.01))
    seen: list = []
    d = WakeDialDispatcher(waker=_awake, transport=scripted_transport([429, 429, 202], seen))
    assert asyncio.run(d.dispatch("acct-1", "carol", frame())) == "accepted"
    assert len(seen) == 3  # initial + two re-admissions inside the D16 window


def test_429_window_exhausted_is_concurrency_cap(fast_settings, monkeypatch):
    monkeypatch.setattr(dispatch_mod, "_ADMISSION_RETRY_DELAYS", (0.01,))
    fast_settings.admission_retry_window_seconds = 0  # window already spent
    seen: list = []
    d = WakeDialDispatcher(waker=_awake, transport=scripted_transport([429], seen))
    assert asyncio.run(d.dispatch("acct-1", "carol", frame())) == "concurrency_cap"
    assert len(seen) == 1


def test_connection_failures_exhaust_to_wake_failed(fast_settings):
    calls = {"n": 0}

    def refuse(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("refused", request=request)

    d = WakeDialDispatcher(waker=_awake, transport=httpx.MockTransport(refuse))
    with pytest.raises(DispatchError) as err:
        asyncio.run(d.dispatch("acct-1", "carol", frame()))
    assert err.value.reason == "wake_failed"
    assert calls["n"] == fast_settings.wake_retry_attempts


def test_wake_never_ready_is_wake_failed_without_dialing(fast_settings):
    seen: list = []

    async def never_up(account_id: str) -> bool:
        return False

    d = WakeDialDispatcher(waker=never_up, transport=scripted_transport([202], seen))
    with pytest.raises(DispatchError) as err:
        asyncio.run(d.dispatch("acct-1", "carol", frame()))
    assert err.value.reason == "wake_failed"
    assert seen == []  # a pod that never came up is never dialed


def test_5xx_retries_then_accepts(fast_settings):
    seen: list = []
    d = WakeDialDispatcher(waker=_awake, transport=scripted_transport([503, 202], seen))
    assert asyncio.run(d.dispatch("acct-1", "carol", frame())) == "accepted"
    assert len(seen) == 2
