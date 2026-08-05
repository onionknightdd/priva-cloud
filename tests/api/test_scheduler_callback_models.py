from __future__ import annotations

import pytest
from pydantic import ValidationError

from priva_common.models.scheduler import (
    AgentRunConfig,
    FeishuCallbackConfig,
    HttpCallConfig,
    UserScriptConfig,
)
from priva_common.scheduler_callback_token import mint, verify


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (AgentRunConfig, {"job_type": "agent_run", "prompt": "brief me"}),
        (HttpCallConfig, {"job_type": "http_call", "url": "https://example.com"}),
        (
            UserScriptConfig,
            {"job_type": "user_script", "source": "inline", "script": "print('ok')"},
        ),
    ],
)
def test_feishu_callback_round_trips_for_every_user_job_type(model, payload):
    config = model.model_validate({**payload, "callback": {"type": "feishu"}})

    assert config.callback == FeishuCallbackConfig(type="feishu")
    assert config.model_dump(mode="json")["callback"] == {"type": "feishu"}
    assert model.model_validate(config.model_dump(mode="json")).callback.type == "feishu"


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (AgentRunConfig, {"prompt": "brief me"}),
        (HttpCallConfig, {"url": "https://example.com"}),
        (UserScriptConfig, {"source": "inline", "script": "echo ok"}),
    ],
)
def test_callback_defaults_to_none_and_rejects_unknown_types(model, payload):
    assert model.model_validate(payload).callback is None

    for callback in ({}, {"type": "webhook"}, {"type": "feishu", "target": "x"}):
        with pytest.raises(ValidationError):
            model.model_validate({**payload, "callback": callback})


def test_scheduler_callback_capability_is_scoped_to_one_run():
    token = mint("acct-1", "run-1", "job-1", ttl_seconds=60)

    claims = verify(token)
    assert claims["account_id"] == "acct-1"
    assert claims["run_id"] == "run-1"
    assert claims["job_id"] == "job-1"

    header, payload, signature = token.split(".")
    tampered_payload = ("a" if payload[0] != "a" else "b") + payload[1:]
    with pytest.raises(ValueError):
        verify(".".join((header, tampered_payload, signature)))
