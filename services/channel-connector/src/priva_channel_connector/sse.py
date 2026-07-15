"""Parse the agent-runner ``/run/stream`` SSE and reduce it to a ``RunOutcome``.

The reduction is a pure function over ``(event, data_json)`` frames so it is unit-
testable without httpx or a live pod. Event contract (from
``priva_common.serialization`` + ``claude_sdk.service.agent_run_stream``):
  - ``event: assistant`` → ``data.content[]`` blocks; text blocks are ``{type:"text", text}``
  - ``event: result``    → ``data.session_id`` (authoritative), ``data.is_error``
  - ``event: stream_error`` → ``data.message`` (fatal run error)
  - other events (result's ``result`` text, tool_use, tool_result, system, stream_init,
    keepalive, …) are received but IGNORED for the MVP relay.

MVP relay policy (user-defined 2026-07-15): the connector relays ONLY the assistant
messages' text to Feishu. The ``result`` event is still consulted for ``session_id``
(binding bookkeeping, not relayed) and ``is_error``, but its ``result`` field is NOT
sent — everything else is dropped for now (streaming cards / tool echoes come later).
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class RunOutcome:
    # The SDK-assigned/rotated session id, captured from the `result` event ONLY.
    # None when the run produced no result (e.g. dial/wake failure) — the caller
    # then keeps whatever binding it had.
    session_id: str | None = None
    text: str = ""             # assistant text to relay back to Feishu
    is_error: bool = False
    error_text: str | None = None


async def iter_sse(resp):
    """Yield ``(event, data_str)`` frames from an httpx streaming response."""
    event: str | None = None
    data: list[str] = []
    async for raw in resp.aiter_lines():
        line = raw.rstrip("\r\n")
        if line == "":
            if data:
                yield (event or "message", "\n".join(data))
            event, data = None, []
            continue
        if line.startswith(":"):
            continue  # comment / keepalive
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data.append(line[len("data:"):].lstrip(" "))
    if data:
        yield (event or "message", "\n".join(data))


def reduce_sse(frames) -> RunOutcome:
    """Reduce ``(event, data_json_str)`` frames to a ``RunOutcome``. Pure + sync.

    MVP: relay assistant text only. ``result`` is consulted for session_id + is_error
    (not relayed); every other event type is ignored for the reply."""
    texts: list[str] = []
    session_id: str | None = None
    is_error = False
    error_text: str | None = None
    for event, data_str in frames:
        try:
            data = json.loads(data_str) if data_str else {}
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        if event == "assistant":
            parts = [
                b.get("text", "")
                for b in data.get("content", [])
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            joined = "".join(p for p in parts if p)
            if joined:
                texts.append(joined)
        elif event == "result":
            # session_id here is authoritative (the SDK's real id, post any rotation).
            # Consumed for binding + error state only — the result text is NOT relayed.
            session_id = data.get("session_id") or session_id
            is_error = is_error or bool(data.get("is_error"))
        elif event == "stream_error":
            is_error = True
            error_text = data.get("message") or error_text
        # tool_use / tool_result / system / stream_init / keepalive → received, ignored.
    return RunOutcome(
        session_id=session_id,
        text="\n".join(texts),
        is_error=is_error,
        error_text=error_text,
    )
