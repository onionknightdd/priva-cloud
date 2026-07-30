from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx

from priva_agent_runner.routers import credentials, scheduler_jobs
from priva_agent_runner.services import http_client
from priva_agent_runner.services.claude_sdk import session_recap


class _ResponseClient:
    def __init__(self, response: httpx.Response):
        self.response = response
        self.requests: list[tuple[str, str]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url: str, **_kwargs):
        self.requests.append(("GET", url))
        return self.response

    async def post(self, url: str, **_kwargs):
        self.requests.append(("POST", url))
        return self.response


def test_external_client_uses_https_proxy_and_ignores_no_proxy(monkeypatch):
    captured = {}

    def build_client(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setenv("HTTPS_PROXY", "http://priva-egress-proxy:3128")
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setattr(http_client.httpx, "AsyncClient", build_client)

    http_client.external_async_client("https://api.example.test/v1", timeout=12)

    assert captured == {
        "proxy": "http://priva-egress-proxy:3128",
        "trust_env": False,
        "timeout": 12,
    }


def test_credentials_model_probe_uses_external_client(monkeypatch):
    client = _ResponseClient(httpx.Response(200, json={"data": [{"id": "model-a"}]}))
    captured = {}

    def build_client(url: str, **kwargs):
        captured.update(url=url, **kwargs)
        return client

    monkeypatch.setattr(
        credentials,
        "read_settings_env",
        lambda: {
            "ANTHROPIC_BASE_URL": "https://models.example.test",
            "ANTHROPIC_AUTH_TOKEN": "secret",
        },
    )
    monkeypatch.setattr(credentials, "external_async_client", build_client)

    models = asyncio.run(credentials.load_model_list(timeout=7))

    assert [model.id for model in models] == ["model-a"]
    assert captured == {"url": "https://models.example.test", "timeout": 7}


def test_session_recap_uses_external_client(monkeypatch):
    client = _ResponseClient(httpx.Response(
        200,
        json={"content": [{"type": "text", "text": "finished proxy hardening"}]},
    ))
    captured = {}

    def build_client(url: str, **kwargs):
        captured.update(url=url, **kwargs)
        return client

    monkeypatch.setattr(
        session_recap,
        "read_settings_env",
        lambda: {
            "ANTHROPIC_BASE_URL": "https://models.example.test",
            "ANTHROPIC_AUTH_TOKEN": "secret",
            "ANTHROPIC_MODEL": "model-a",
        },
    )
    monkeypatch.setattr(session_recap, "external_async_client", build_client)

    recap = asyncio.run(session_recap._ask_model("user: harden egress"))

    assert recap == "finished proxy hardening"
    assert captured == {
        "url": "https://models.example.test",
        "timeout": session_recap._TIMEOUT_SEC,
    }


def test_scheduler_trigger_remains_direct_even_with_proxy_env(monkeypatch):
    captured = {}
    client = _ResponseClient(httpx.Response(202))

    def build_client(**kwargs):
        captured.update(kwargs)
        return client

    monkeypatch.setenv("HTTP_PROXY", "http://priva-egress-proxy:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://priva-egress-proxy:3128")
    monkeypatch.setattr(scheduler_jobs.httpx, "AsyncClient", build_client)
    monkeypatch.setattr(
        scheduler_jobs,
        "get_settings",
        lambda: SimpleNamespace(
            scheduler=SimpleNamespace(internal_url="http://priva-scheduler:8082")
        ),
    )
    monkeypatch.setattr(scheduler_jobs, "auth_header", lambda: {"X-Test": "token"})

    response = asyncio.run(scheduler_jobs._post_trigger("job-1"))

    assert response.status_code == 202
    assert captured == {"trust_env": False, "timeout": 10.0}
    assert client.requests == [
        ("POST", "http://priva-scheduler:8082/internal/trigger/job-1")
    ]
