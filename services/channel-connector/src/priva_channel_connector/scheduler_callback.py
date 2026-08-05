"""Scheduled-job callback DTOs and Feishu card rendering.

The agent-runner sends only an account-scoped outcome payload.  Addressing stays
inside channel-connector: neither a Feishu ``open_id`` nor app credentials are
accepted on this wire.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


_ITEM_LIMIT = 4000
_TRUNCATED = "内容已截断"
_CAPTURE_LIMIT = _ITEM_LIMIT + 1


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentCallbackResult(_StrictModel):
    message: str = Field(max_length=_CAPTURE_LIMIT)


class HttpCallbackResult(_StrictModel):
    method: str = Field(min_length=1, max_length=16)
    url: str = Field(min_length=1, max_length=8192)
    status_code: int | None = None
    reason: str | None = Field(default=None, max_length=512)
    body: str | None = Field(default=None, max_length=_CAPTURE_LIMIT)
    error: str | None = Field(default=None, max_length=_CAPTURE_LIMIT)


class ScriptCallbackResult(_StrictModel):
    exit_code: int | None = None
    stdout: str = Field(default="", max_length=_CAPTURE_LIMIT)
    stderr: str = Field(default="", max_length=_CAPTURE_LIMIT)
    timed_out: bool = False


CallbackResult = AgentCallbackResult | HttpCallbackResult | ScriptCallbackResult


class SchedulerCallbackPayload(_StrictModel):
    run_id: str = Field(min_length=1, max_length=128)
    job_id: str = Field(min_length=1, max_length=128)
    # Job names historically have no model-level minimum. Keep the callback
    # contract compatible with those rows while bounding untrusted input.
    job_name: str = Field(max_length=512)
    job_type: Literal["agent_run", "http_call", "user_script"]
    status: Literal["success", "error", "cancelled"]
    duration_ms: int | None = Field(default=None, ge=0)
    session_id: str | None = Field(default=None, max_length=128)
    result: CallbackResult

    @model_validator(mode="after")
    def _result_matches_job_type(self):
        expected = {
            "agent_run": AgentCallbackResult,
            "http_call": HttpCallbackResult,
            "user_script": ScriptCallbackResult,
        }[self.job_type]
        if not isinstance(self.result, expected):
            raise ValueError(f"result does not match job_type {self.job_type}")
        return self


def _pt(content: str) -> dict:
    return {"tag": "plain_text", "content": content}


def _md(content: str) -> dict:
    return {"tag": "markdown", "content": content}


def _bounded_block(
    label: str | None,
    value: str | None,
    *,
    keep: Literal["head", "tail"],
    code: bool = False,
) -> dict:
    """Build one markdown element whose *actual content* never exceeds 4000 chars.

    Agent/HTTP values retain their beginning; stdout/stderr retain their end, where
    the most useful exception/trace lines normally live.  The truncation marker is
    outside the code fence so it stays visible in the rendered card.
    """
    text = str(value or "")
    if not text:
        text = "(空)"

    prefix = f"**{label}**\n\n" if label else ""
    fence_open = "```text\n" if code else ""
    fence_close = "\n```" if code else ""
    complete = f"{prefix}{fence_open}{text}{fence_close}"
    if len(complete) <= _ITEM_LIMIT:
        return _md(complete)

    marker = f"<font color='grey'>{_TRUNCATED}</font>"
    if keep == "head":
        suffix = f"{fence_close}\n\n{marker}"
        budget = max(0, _ITEM_LIMIT - len(prefix) - len(fence_open) - len(suffix))
        content = f"{prefix}{fence_open}{text[:budget]}{suffix}"
    else:
        middle = f"{marker}\n\n{fence_open}"
        budget = max(0, _ITEM_LIMIT - len(prefix) - len(middle) - len(fence_close))
        content = f"{prefix}{middle}{text[-budget:] if budget else ''}{fence_close}"
    return _md(content)


def _duration(ms: int | None) -> str:
    if ms is None:
        return "—"
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms / 1000:.2f}s"


def _one_line(value: str, limit: int = 80) -> str:
    line = " ".join(str(value).splitlines()).strip()
    return line if len(line) <= limit else line[: limit - 1] + "…"


def render_scheduler_callback_card(payload: SchedulerCallbackPayload) -> dict:
    """Render a terminal scheduled-job outcome as a Feishu card-json-v2 dict."""
    status_meta = {
        "success": ("✅", "执行成功", "green"),
        "error": ("❌", "执行失败", "red"),
        "cancelled": ("⏹️", "已取消", "grey"),
    }
    type_labels = {
        "agent_run": "Agent",
        "http_call": "HTTP Call",
        "user_script": "Script",
    }
    icon, status_label, template = status_meta[payload.status]
    type_label = type_labels[payload.job_type]

    meta = (
        f"**任务**：{payload.job_name}\n"
        f"**类型**：{type_label}\n"
        f"**状态**：{status_label}\n"
        f"**耗时**：{_duration(payload.duration_ms)}\n"
        f"**Job ID**：`{payload.job_id}`\n"
        f"**Run ID**：`{payload.run_id}`\n"
        f"**Session ID**：{f'`{payload.session_id}`' if payload.session_id else '—'}"
    )
    # Keep metadata as the first body element, directly below the card header.
    # There is intentionally no extra “运行信息” section label.
    elements = [_bounded_block(None, meta, keep="head")]

    if isinstance(payload.result, AgentCallbackResult):
        label = "异常信息" if payload.status == "error" else "运行结果"
        elements.append(_bounded_block(label, payload.result.message, keep="head"))
        elements.append({"tag": "hr"})

    elif isinstance(payload.result, HttpCallbackResult):
        result = payload.result
        response = "—" if result.status_code is None else str(result.status_code)
        if result.reason:
            response += f" {result.reason}"
        request_meta = (
            f"**请求**：`{result.method}` {result.url}\n"
            f"**响应**：`{response}`"
        )
        elements.append(_bounded_block("HTTP 结果", request_meta, keep="head"))
        if result.error:
            elements.append(_bounded_block("异常信息", result.error, keep="head", code=True))
        elements.append(_bounded_block("响应体", result.body, keep="head", code=True))

    else:
        result = payload.result
        exit_code = "—" if result.exit_code is None else str(result.exit_code)
        script_meta = (
            f"**Exit code**：`{exit_code}`\n"
            f"**Timed out**：`{'yes' if result.timed_out else 'no'}`"
        )
        elements.append(_bounded_block("脚本结果", script_meta, keep="head"))
        elements.append(_bounded_block("stderr", result.stderr, keep="tail", code=True))
        elements.append(_bounded_block("stdout", result.stdout, keep="tail", code=True))

    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": _pt(f"{icon} 定时任务{status_label}"),
            "subtitle": _pt(
                f"{_one_line(payload.job_name, 60)} · {type_label} · {_duration(payload.duration_ms)}"
            ),
            "template": template,
        },
        "body": {"elements": elements},
    }


__all__ = [
    "AgentCallbackResult",
    "HttpCallbackResult",
    "SchedulerCallbackPayload",
    "ScriptCallbackResult",
    "render_scheduler_callback_card",
]
