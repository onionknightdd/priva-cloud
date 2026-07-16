"""Parse the agent-runner ``/run/stream`` SSE and fold it into an ordered ``StreamState``.

The fold is a pure function over ``(event, data_json)`` frames so it is unit-testable
without httpx or a live pod. Event contract (from ``priva_common.serialization`` +
``claude_sdk.service.agent_run_stream``):
  - ``event: assistant`` → ``data.content[]`` blocks; text blocks are ``{type:"text", text}``
  - ``event: tool_use``  → an *assistant*-shaped payload whose ``content[]`` carries
    ``{type:"tool_use", id, name, input}`` (and possibly text blocks too)
  - ``event: tool_result`` → a *user*-shaped payload; ``content[]`` carries
    ``{type:"tool_result", tool_use_id, content, is_error}`` — paired to its tool_use by id
  - ``event: result``    → ``session_id`` (authoritative), ``is_error``, ``duration_ms``, ``num_turns``
  - ``event: stream_error`` / ``retry_exhausted`` → fatal run error (``data.message``)
  - other events (system, stream_init, keepalive, hook_event, task_*, …) → ignored

``StreamState.timeline`` preserves message order — a flat list whose items are either a
text run (``str``) or a ``ToolStep`` — so the card renders text and tool steps interleaved
exactly as they streamed, instead of lumping all steps at the end.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class RunOutcome:
    # The SDK-assigned/rotated session id, captured from the `result` event ONLY.
    # None when the run produced no result (e.g. dial/wake failure) — the caller
    # then keeps whatever binding it had.
    session_id: str | None = None
    text: str = ""             # assistant text to relay back to Feishu
    is_error: bool = False
    error_text: str | None = None


@dataclass
class ToolStep:
    """One tool invocation shown as a step row; a ``tool_use`` frame creates it, the
    matching ``tool_result`` (same id) flips its status."""
    tool_use_id: str
    name: str
    status: str = "running"    # running | done | error
    summary: str = ""          # one-line input summary (Bash→command, Read/Edit→file_path, …)
    tool_input: dict | None = None  # raw input, carried forward so the card layer can derive its own summary/deltas


@dataclass
class StreamState:
    """Running snapshot folded from the SSE frames (see ``step``), mutated in place as
    frames arrive. ``timeline`` is the ordered surface (``str`` text runs interleaved with
    ``ToolStep``); the rest is bookkeeping."""
    timeline: list = field(default_factory=list)   # ordered: str (text run) | ToolStep
    _by_id: dict = field(default_factory=dict)      # tool_use_id -> ToolStep (pairing)
    session_id: str | None = None
    is_error: bool = False
    error_text: str | None = None
    duration_ms: int | None = None
    num_turns: int | None = None

    @property
    def text(self) -> str:
        return "\n".join(t for t in self.timeline if isinstance(t, str) and t)

    @property
    def steps(self) -> list:
        return [t for t in self.timeline if isinstance(t, ToolStep)]

    def outcome(self) -> RunOutcome:
        return RunOutcome(self.session_id, self.text, self.is_error, self.error_text)

    @classmethod
    def from_outcome(cls, o: RunOutcome) -> "StreamState":
        """Rebuild a minimal state from a RunOutcome — used when a run failed before any
        frame arrived (wake/dial failure) so the caller still has something to render."""
        s = cls(session_id=o.session_id, is_error=o.is_error, error_text=o.error_text)
        if o.text:
            s.timeline.append(o.text)
        return s


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


_SUMMARY_MAX = 80
# Tool → the single input field worth showing on the step row (one-line summary).
_INPUT_KEY = {
    "Bash": "command",
    "Read": "file_path", "Edit": "file_path", "Write": "file_path",
    "NotebookEdit": "notebook_path",
    "Grep": "pattern", "Glob": "pattern",
    "WebFetch": "url", "WebSearch": "query",
    "Task": "description",
}


def _one_line(s) -> str:
    return " ".join(str(s).split())


def _summarize_input(name: str, tool_input) -> str:
    """One-line summary of a tool call's input (Bash→command, file tools→path, …).
    Falls back to the first non-empty string value. Truncated to keep the card small."""
    if not isinstance(tool_input, dict):
        return _one_line(tool_input)[:_SUMMARY_MAX]
    key = _INPUT_KEY.get(name)
    val = tool_input.get(key) if key else None
    if not val:
        val = next((v for v in tool_input.values() if isinstance(v, str) and v), "")
    return _one_line(val)[:_SUMMARY_MAX]


def step(state: StreamState, event: str, data_str: str) -> bool:
    """Fold ONE ``(event, data_json_str)`` frame into ``state`` (pure + sync). Returns
    True when the *visible* snapshot changed (a text run or a step was appended / a step
    status flipped / an error surfaced). Pure bookkeeping (result timing) returns False."""
    try:
        data = json.loads(data_str) if data_str else {}
    except (ValueError, TypeError):
        return False
    if not isinstance(data, dict):
        return False

    if event in ("assistant", "tool_use"):
        changed = False
        buf: list[str] = []
        for b in data.get("content", []):
            if not isinstance(b, dict):
                continue
            btype = b.get("type")
            if btype == "text":
                t = b.get("text", "")
                if t:
                    buf.append(t)
            elif btype == "tool_use":
                if buf:                       # flush pending text before the tool (keep order)
                    state.timeline.append("".join(buf))
                    buf = []
                tid = b.get("id") or ""
                name = b.get("name") or "tool"
                inp = b.get("input")
                st = ToolStep(tid, name, "running", _summarize_input(name, inp),
                              inp if isinstance(inp, dict) else None)
                state.timeline.append(st)
                if tid:
                    state._by_id[tid] = st
                changed = True
        if buf:
            state.timeline.append("".join(buf))
            changed = True
        return changed

    if event == "tool_result":
        content = data.get("content")
        if not isinstance(content, list):
            return False
        changed = False
        for b in content:
            if not isinstance(b, dict) or b.get("type") != "tool_result":
                continue
            st = state._by_id.get(b.get("tool_use_id"))
            if st is not None:
                st.status = "error" if b.get("is_error") else "done"
                changed = True
        return changed

    if event == "result":
        state.session_id = data.get("session_id") or state.session_id
        state.is_error = state.is_error or bool(data.get("is_error"))
        if data.get("duration_ms") is not None:
            state.duration_ms = data.get("duration_ms")
        if data.get("num_turns") is not None:
            state.num_turns = data.get("num_turns")
        return False

    if event in ("stream_error", "retry_exhausted"):
        state.is_error = True
        state.error_text = data.get("message") or state.error_text
        return True

    # system / stream_init / keepalive / hook_event / task_* → received, ignored.
    return False


def reduce_sse(frames) -> RunOutcome:
    """Reduce ``(event, data_json_str)`` frames to a ``RunOutcome`` (batch entry point,
    used by the plain-text fallback and the unit tests). Re-expressed on ``step``."""
    state = StreamState()
    for event, data_str in frames:
        step(state, event, data_str)
    return state.outcome()
