from __future__ import annotations

import asyncio
import threading

import httpx
import pytest
from fastapi import HTTPException

from priva_agent_runner import activity
from priva_agent_runner.app import ActivityMiddleware, app as runner_app


@pytest.fixture(autouse=True)
def reset_activity(monkeypatch):
    monkeypatch.setattr(activity, "_active", 0)
    monkeypatch.setattr(activity, "_last", 1.0)
    monkeypatch.setattr(activity, "_revision", 0)
    monkeypatch.setattr(activity, "_drain_gate", False)


def test_activity_between_health_and_drain_invalidates_revision():
    _, _, observed, _ = activity.state()
    assert activity.try_enter()
    activity.leave()
    assert activity.begin_drain(observed) is False


def test_drain_is_idempotent_and_permanent_for_process_lifetime():
    _, _, revision, _ = activity.state()

    assert activity.begin_drain(revision)
    assert activity.begin_drain(revision)  # lost response/retry is idempotent
    assert activity.try_enter() is False


def test_force_drain_preserves_existing_activity_and_closes_new_admission():
    assert activity.try_enter()
    active, revision = activity.force_drain()
    assert (active, revision) == (1, 1)
    assert activity.try_enter() is False

    activity.leave()
    assert activity.state() == (0, activity.snapshot()[1], 2, True)
    assert activity.try_enter() is False
    assert activity.state()[3] is True


def test_atomic_admission_and_drain_have_exactly_one_winner():
    _, _, revision, _ = activity.state()
    barrier = threading.Barrier(3)
    result: dict[str, bool] = {}

    def admit():
        barrier.wait()
        result["admit"] = activity.try_enter()

    def drain():
        barrier.wait()
        result["drain"] = activity.begin_drain(revision)

    threads = [threading.Thread(target=admit), threading.Thread(target=drain)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert result["admit"] is not result["drain"]
    if result["admit"]:
        activity.leave()


def test_middleware_returns_retryable_http_and_websocket_rejections():
    _, _, revision, _ = activity.state()
    assert activity.begin_drain(revision)
    called = []

    async def downstream(scope, receive, send):
        called.append(scope["type"])

    middleware = ActivityMiddleware(downstream)

    async def exercise():
        http_messages = []

        async def collect_http(message):
            http_messages.append(message)

        await middleware(
            {"type": "http", "path": "/api/sandbox/health"},
            lambda: None,
            collect_http,
        )
        websocket_messages = []

        async def collect_websocket(message):
            websocket_messages.append(message)

        await middleware(
            {"type": "websocket", "path": "/api/sandbox/agent"},
            lambda: None,
            collect_websocket,
        )
        return http_messages, websocket_messages

    http_messages, websocket_messages = asyncio.run(exercise())
    assert called == []
    assert http_messages[0]["status"] == 503
    assert (b"retry-after", b"1") in http_messages[0]["headers"]
    assert websocket_messages == [{"type": "websocket.close", "code": 1013}]


def test_internal_drain_requires_per_pod_capability(monkeypatch):
    endpoint = next(
        route.endpoint
        for route in runner_app.routes
        if getattr(route, "path", None) == "/internal/drain"
    )
    monkeypatch.setenv("PRIVA_INTERNAL_DRAIN_TOKEN", "pod-capability")

    with pytest.raises(HTTPException) as missing:
        asyncio.run(
            endpoint(
                revision=0,
                x_priva_drain_token=None,
            )
        )
    assert missing.value.status_code == 401

    with pytest.raises(HTTPException) as wrong:
        asyncio.run(
            endpoint(
                revision=0,
                x_priva_drain_token="other-pod-capability",
            )
        )
    assert wrong.value.status_code == 401

    async def request_with_service_token_only():
        transport = httpx.ASGITransport(app=runner_app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://runner.test"
        ) as client:
            return await client.post(
                "/internal/drain",
                params={"revision": 0},
                headers={"X-Priva-Service-Token": "operator-token-must-be-ignored"},
            )

    assert asyncio.run(request_with_service_token_only()).status_code == 401


def test_internal_force_drain_accepts_per_pod_capability(monkeypatch):
    endpoint = next(
        route.endpoint
        for route in runner_app.routes
        if getattr(route, "path", None) == "/internal/drain"
    )
    monkeypatch.setenv("PRIVA_INTERNAL_DRAIN_TOKEN", "pod-capability")
    assert activity.try_enter()

    result = asyncio.run(
        endpoint(
            force=True,
            revision=None,
            x_priva_drain_token="pod-capability",
        )
    )

    assert result == {
        "draining": True,
        "active_runs": 1,
        "activity_revision": 1,
    }
    assert activity.try_enter() is False


def test_internal_drain_rejects_wrong_per_pod_capability(monkeypatch):
    endpoint = next(
        route.endpoint
        for route in runner_app.routes
        if getattr(route, "path", None) == "/internal/drain"
    )
    monkeypatch.setenv("PRIVA_INTERNAL_DRAIN_TOKEN", "pod-capability")

    with pytest.raises(HTTPException) as denied:
        asyncio.run(
            endpoint(
                force=True,
                revision=None,
                x_priva_drain_token="other-pod-capability",
            )
        )

    assert denied.value.status_code == 401
    assert activity.state()[3] is False
