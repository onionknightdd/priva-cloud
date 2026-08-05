"""Scheduled callback push: account scoping, owner resolution, and card rendering."""

from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_CONN_SRC = os.path.join(_REPO, "services", "channel-connector", "src")
if _CONN_SRC not in sys.path:
    sys.path.insert(0, _CONN_SRC)

from priva_channel_connector import api as api_mod  # noqa: E402
from priva_channel_connector.engine import (  # noqa: E402
    ReconcileEngine,
    SchedulerCallbackDeliveryFailed,
    SchedulerCallbackOwnerUnbound,
    SchedulerCallbackRateLimited,
    SchedulerCallbackRejected,
    SchedulerCallbackUnavailable,
    SchedulerCallbackWorkerUnavailable,
)
from priva_channel_connector.scheduler_callback import (  # noqa: E402
    SchedulerCallbackPayload,
    render_scheduler_callback_card,
)
from priva_common.service_token import ServicePrincipal  # noqa: E402


def _agent_payload(**overrides) -> dict:
    payload = {
        "run_id": "run-1",
        "job_id": "job-1",
        "job_name": "Daily briefing",
        "job_type": "agent_run",
        "status": "success",
        "duration_ms": 12_345,
        "result": {"message": "All done"},
    }
    payload.update(overrides)
    return payload


def _block(card: dict, label: str) -> str:
    prefix = f"**{label}**"
    return next(
        element["content"]
        for element in card["body"]["elements"]
        if element.get("content", "").startswith(prefix)
    )


def _headers(service_token: str = "runner-a", callback_token: str = "callback-a") -> dict:
    return {
        "X-Priva-Service-Token": service_token,
        "X-Priva-Scheduler-Callback-Token": callback_token,
    }


class _ApiEngine:
    armed_count = 1

    def __init__(self, error: Exception | None = None):
        self.error = error
        self.calls: list[tuple[str, SchedulerCallbackPayload, dict]] = []

    async def start(self):
        return None

    async def stop(self):
        return None

    async def push_scheduler_callback(
        self, account_id: str, payload: SchedulerCallbackPayload, card: dict,
    ) -> str:
        self.calls.append((account_id, payload, card))
        if self.error:
            raise self.error
        return "om_callback"


@pytest.fixture
def verify_tokens(monkeypatch):
    principals = {
        "runner-a": ServicePrincipal("agent-runner", "A"),
        "runner-b": ServicePrincipal("agent-runner", "B"),
        "scheduler": ServicePrincipal("scheduler"),
    }

    def verify(token: str):
        if token not in principals:
            raise ValueError("bad token")
        return principals[token]

    monkeypatch.setattr(api_mod, "verify_service", verify)

    def verify_callback(token: str):
        if token == "callback-a":
            return {"account_id": "A", "run_id": "run-1", "job_id": "job-1"}
        if token == "callback-b":
            return {"account_id": "B", "run_id": "run-1", "job_id": "job-1"}
        raise ValueError("bad callback token")

    monkeypatch.setattr(api_mod, "verify_callback_token", verify_callback)


def test_callback_endpoint_authenticates_scopes_and_delivers_card(verify_tokens):
    engine = _ApiEngine()
    with TestClient(api_mod.create_app(engine)) as client:
        assert client.post(
            "/internal/scheduler-callback/A", json=_agent_payload()
        ).status_code == 401
        assert client.post(
            "/internal/scheduler-callback/A", json=_agent_payload(),
            headers=_headers("bad"),
        ).status_code == 401
        assert client.post(
            "/internal/scheduler-callback/A", json=_agent_payload(),
            headers=_headers("scheduler"),
        ).status_code == 403
        assert client.post(
            "/internal/scheduler-callback/A", json=_agent_payload(),
            headers=_headers("runner-b"),
        ).status_code == 403

        assert client.post(
            "/internal/scheduler-callback/A", json=_agent_payload(),
            headers={"X-Priva-Service-Token": "runner-a"},
        ).status_code == 401
        assert client.post(
            "/internal/scheduler-callback/A", json=_agent_payload(),
            headers=_headers(callback_token="bad"),
        ).status_code == 401
        assert client.post(
            "/internal/scheduler-callback/A", json=_agent_payload(),
            headers=_headers(callback_token="callback-b"),
        ).status_code == 403

        response = client.post(
            "/internal/scheduler-callback/A", json=_agent_payload(),
            headers=_headers(),
        )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "status": "delivered", "account_id": "A",
        "run_id": "run-1", "message_id": "om_callback",
    }
    assert len(engine.calls) == 1
    account_id, payload, card = engine.calls[0]
    assert account_id == "A"
    assert payload.run_id == "run-1"
    assert card["header"]["title"]["content"] == "✅ 定时任务执行成功"
    assert card["header"]["subtitle"]["content"] == "Daily briefing · Agent · 12.35s"
    assert "All done" in _block(card, "Agent 结果")


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (SchedulerCallbackUnavailable("disabled"), 409),
        (SchedulerCallbackOwnerUnbound("unbound"), 409),
        (SchedulerCallbackRejected("rejected"), 409),
        (SchedulerCallbackRateLimited("slow down"), 429),
        (SchedulerCallbackWorkerUnavailable("not ready"), 503),
        (SchedulerCallbackDeliveryFailed("send failed"), 502),
    ],
)
def test_callback_endpoint_maps_delivery_failures(verify_tokens, error, status):
    engine = _ApiEngine(error)
    with TestClient(api_mod.create_app(engine)) as client:
        response = client.post(
            "/internal/scheduler-callback/A", json=_agent_payload(),
            headers=_headers(),
        )
    assert response.status_code == status
    assert response.json()["detail"] == str(error)


def test_callback_dto_rejects_result_for_a_different_job_type(verify_tokens):
    engine = _ApiEngine()
    mismatched = _agent_payload(result={
        "method": "GET", "url": "https://example.test", "status_code": 200,
        "reason": "OK", "body": "ok", "error": None,
    })
    with TestClient(api_mod.create_app(engine)) as client:
        response = client.post(
            "/internal/scheduler-callback/A", json=mismatched,
            headers=_headers(),
        )
    assert response.status_code == 422
    assert engine.calls == []


def test_callback_endpoint_rejects_oversized_fields_and_body(verify_tokens):
    engine = _ApiEngine()
    with TestClient(api_mod.create_app(engine)) as client:
        field_response = client.post(
            "/internal/scheduler-callback/A",
            json=_agent_payload(result={"message": "x" * 4002}),
            headers=_headers(),
        )
        body_response = client.post(
            "/internal/scheduler-callback/A",
            content=b"x" * (1024 * 1024 + 1),
            headers={
                "Content-Type": "application/json",
                **_headers(),
            },
        )

    assert field_response.status_code == 422
    assert body_response.status_code == 413
    assert engine.calls == []


def test_agent_and_http_keep_head_and_mark_truncation():
    long = "HEAD-" + "x" * 3991 + "-TAIL"
    agent = SchedulerCallbackPayload.model_validate(_agent_payload(result={"message": long}))
    agent_card = render_scheduler_callback_card(agent)
    agent_content = _block(agent_card, "Agent 结果")
    assert "HEAD-" in agent_content and "-TAIL" not in agent_content
    assert "内容已截断" in agent_content and len(agent_content) <= 4000

    http = SchedulerCallbackPayload.model_validate(_agent_payload(
        job_type="http_call", status="error",
        result={
            "method": "GET", "url": "https://example.test", "status_code": None,
            "reason": "", "body": long, "error": long,
        },
    ))
    http_card = render_scheduler_callback_card(http)
    body = _block(http_card, "响应体")
    error = _block(http_card, "异常信息")
    assert "HEAD-" in body and "-TAIL" not in body
    assert "HEAD-" in error and "-TAIL" not in error
    assert "内容已截断" in body and "内容已截断" in error
    assert len(body) <= 4000 and len(error) <= 4000
    labels = [e["content"].splitlines()[0] for e in http_card["body"]["elements"]]
    assert labels.index("**异常信息**") < labels.index("**响应体**")


def test_http_network_error_accepts_null_reason_and_body():
    payload = SchedulerCallbackPayload.model_validate(_agent_payload(
        job_type="http_call", status="error",
        result={
            "method": "GET", "url": "https://unreachable.test",
            "status_code": None, "reason": None, "body": None,
            "error": "Connection refused",
        },
    ))
    card = render_scheduler_callback_card(payload)
    assert "Connection refused" in _block(card, "异常信息")
    assert "(空)" in _block(card, "响应体")
    labels = [e["content"].splitlines()[0] for e in card["body"]["elements"]]
    assert labels.index("**异常信息**") < labels.index("**响应体**")


def test_script_keeps_tail_and_renders_stderr_before_stdout():
    long = "HEAD-" + "x" * 3991 + "-TAIL"
    payload = SchedulerCallbackPayload.model_validate(_agent_payload(
        job_type="user_script", status="error",
        result={"exit_code": 3, "stdout": long, "stderr": long, "timed_out": False},
    ))
    card = render_scheduler_callback_card(payload)
    stderr = _block(card, "stderr")
    stdout = _block(card, "stdout")
    for content in (stderr, stdout):
        assert "HEAD-" not in content and "-TAIL" in content
        assert "内容已截断" in content and len(content) <= 4000
    labels = [e["content"].splitlines()[0] for e in card["body"]["elements"]]
    assert labels.index("**stderr**") < labels.index("**stdout**")
    assert card["header"]["title"]["content"] == "❌ 定时任务执行失败"


class _Configs:
    def __init__(self, cfg=None, error: Exception | None = None):
        self.cfg = cfg
        self.error = error

    def get(self, account_id: str):
        if self.error:
            raise self.error
        return self.cfg


class _Scheduler:
    def __init__(self, *, run=None, job=None, error: Exception | None = None):
        self.run = run
        self.job = job
        self.error = error

    def get_run(self, account_id: str, run_id: str):
        if self.error:
            raise self.error
        if self.run is not None:
            return self.run
        return SimpleNamespace(
            run_id=run_id,
            job_id="job-1",
            job_name="Daily briefing",
            status="success",
        )

    def get_job(self, job_id: str):
        if self.error:
            raise self.error
        if self.job is not None:
            return self.job
        return SimpleNamespace(
            id=job_id,
            job_config=SimpleNamespace(
                job_type="agent_run",
                callback=SimpleNamespace(type="feishu"),
            ),
        )


class _Worker:
    def __init__(self, message_id: str | None = "om_1"):
        self.message_id = message_id
        self.calls: list[tuple[str, dict]] = []

    async def send_card_to_user(self, open_id: str, card: dict):
        self.calls.append((open_id, card))
        return self.message_id


def _engine(cfg, worker=None, *, run=None, job=None) -> ReconcileEngine:
    client = SimpleNamespace(
        feishu_configs=_Configs(cfg),
        scheduler=_Scheduler(run=run, job=job),
    )
    engine = ReconcileEngine(client, None, None, poll_seconds=10)
    if worker is not None:
        engine._workers["A"] = worker
        engine._digests["A"] = "digest-1"
    return engine


def _validated_agent_payload(**overrides) -> SchedulerCallbackPayload:
    return SchedulerCallbackPayload.model_validate(_agent_payload(**overrides))


def test_engine_resolves_owner_open_id_and_uses_active_worker():
    cfg = SimpleNamespace(
        effective_enabled=True,
        owner_open_id="ou_owner",
        desired_digest="digest-1",
    )
    worker = _Worker()
    engine = _engine(cfg, worker)
    card = {"schema": "2.0"}
    message_id = asyncio.run(engine.push_scheduler_callback(
        "A", _validated_agent_payload(), card,
    ))
    assert message_id == "om_1"
    assert worker.calls == [("ou_owner", card)]


def test_engine_requires_effective_config_bound_owner_and_worker():
    payload = _validated_agent_payload()
    with pytest.raises(SchedulerCallbackUnavailable):
        asyncio.run(_engine(None).push_scheduler_callback("A", payload, {}))
    with pytest.raises(SchedulerCallbackUnavailable):
        asyncio.run(_engine(SimpleNamespace(
            effective_enabled=False, owner_open_id="ou_owner", desired_digest="digest-1",
        )).push_scheduler_callback("A", payload, {}))
    with pytest.raises(SchedulerCallbackOwnerUnbound):
        asyncio.run(_engine(SimpleNamespace(
            effective_enabled=True, owner_open_id="", desired_digest="digest-1",
        )).push_scheduler_callback("A", payload, {}))
    with pytest.raises(SchedulerCallbackWorkerUnavailable):
        asyncio.run(_engine(SimpleNamespace(
            effective_enabled=True, owner_open_id="ou_owner", desired_digest="digest-1",
        )).push_scheduler_callback("A", payload, {}))


def test_engine_surfaces_transport_send_failure():
    cfg = SimpleNamespace(
        effective_enabled=True,
        owner_open_id="ou_owner",
        desired_digest="digest-1",
    )
    with pytest.raises(SchedulerCallbackDeliveryFailed):
        asyncio.run(_engine(cfg, _Worker(None)).push_scheduler_callback(
            "A", _validated_agent_payload(), {},
        ))


def test_engine_rejects_unverified_or_ineligible_run():
    cfg = SimpleNamespace(
        effective_enabled=True,
        owner_open_id="ou_owner",
        desired_digest="digest-1",
    )
    payload = _validated_agent_payload()

    running = SimpleNamespace(
        run_id="run-1", job_id="job-1", job_name="Daily briefing", status="running",
    )
    with pytest.raises(SchedulerCallbackRejected, match="not terminal"):
        asyncio.run(_engine(cfg, _Worker(), run=running).push_scheduler_callback(
            "A", payload, {},
        ))

    callback_disabled = SimpleNamespace(
        id="job-1",
        job_config=SimpleNamespace(job_type="agent_run", callback=None),
    )
    with pytest.raises(SchedulerCallbackRejected, match="not enabled"):
        asyncio.run(_engine(cfg, _Worker(), job=callback_disabled).push_scheduler_callback(
            "A", payload, {},
        ))


def test_engine_fences_stale_config_and_deduplicates_delivered_run():
    payload = _validated_agent_payload()
    stale = SimpleNamespace(
        effective_enabled=True,
        owner_open_id="ou_old",
        desired_digest="digest-2",
    )
    worker = _Worker()
    with pytest.raises(SchedulerCallbackWorkerUnavailable, match="changing"):
        asyncio.run(_engine(stale, worker).push_scheduler_callback("A", payload, {}))
    assert worker.calls == []

    current = SimpleNamespace(
        effective_enabled=True,
        owner_open_id="ou_owner",
        desired_digest="digest-1",
    )
    worker = _Worker()
    engine = _engine(current, worker)

    async def deliver_twice():
        first = await engine.push_scheduler_callback("A", payload, {"n": 1})
        second = await engine.push_scheduler_callback("A", payload, {"n": 2})
        return first, second

    assert asyncio.run(deliver_twice()) == ("om_1", "om_1")
    assert worker.calls == [("ou_owner", {"n": 1})]


def test_engine_rate_limits_distinct_callback_attempts(monkeypatch):
    from priva_channel_connector import engine as engine_mod

    monkeypatch.setattr(engine_mod, "_CALLBACK_RATE_LIMIT", 1)
    cfg = SimpleNamespace(
        effective_enabled=True,
        owner_open_id="ou_owner",
        desired_digest="digest-1",
    )
    engine = _engine(cfg, _Worker(None))

    async def exceed_rate():
        with pytest.raises(SchedulerCallbackDeliveryFailed):
            await engine.push_scheduler_callback(
                "A", _validated_agent_payload(), {},
            )
        with pytest.raises(SchedulerCallbackRateLimited):
            await engine.push_scheduler_callback(
                "A", _validated_agent_payload(run_id="run-2"), {},
            )

    asyncio.run(exceed_rate())
