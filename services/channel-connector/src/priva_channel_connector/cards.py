"""Pure Feishu interactive-card (schema 2.0) renderer over a ``StreamState`` snapshot.

No ``lark_oapi`` import — this only builds the card *dict*; the transport serializes and
sends/patches it. Kept separate from ``sse.py`` (pure reducer) and ``lark_ws.py`` (I/O) to
preserve the seam split, and unit-testable on its own.

Design decisions (2026-07-16, user-confirmed):
  - NO header; while running a single "Thinking…" footer note signals activity and is
    dropped once final (a completed card is just the answer + steps);
  - text streams by whole assistant message (AR has no token deltas); an error is shown by
    a ⚠️ prefix in the body (there is no coloured header to carry it);
  - tool step rows = glyph + tool name + one-line input summary (D2);
  - steps panel EXPANDED while running and on error, collapsed on clean finish (D3).
"""

from __future__ import annotations

from .sse import StreamState

_BODY_MAX = 3000        # tail-cap the streamed text so the card stays well under Feishu's 30KB
_ERR_MAX = 500          # head-cap the error prefix (kept intact on the error card)
_STEPS_MAX = 30         # keep only the most recent N step rows
_GLYPH = {"running": "⟳", "done": "✔", "error": "✗"}
_THINKING = "Thinking…"


def _one_line_head(s) -> str:
    """Head-truncate the error text so it stays visible on the error card (never clipped
    away by the body tail-cap). Kept short — error messages, not streamed output."""
    if not s:
        return ""
    s = str(s)
    return s[:_ERR_MAX] + ("…" if len(s) > _ERR_MAX else "")


def _clip_body(text: str) -> str:
    """Tail-cap the streamed text (we want the latest output). Cut on a line boundary and
    re-balance code fences so a slice through a ``` block doesn't corrupt the rest of the
    card into a code block / literal text."""
    if len(text) <= _BODY_MAX:
        return text
    clipped = text[-_BODY_MAX:]
    nl = clipped.find("\n")
    if nl != -1:                       # start at a clean line, not mid-line
        clipped = clipped[nl + 1:]
    if clipped.count("```") % 2:       # odd fence count → we cut inside a code block; reopen it
        clipped = "```\n" + clipped
    return "…\n" + clipped


def _steps_md(steps) -> str:
    shown = steps[-_STEPS_MAX:]
    hidden = len(steps) - len(shown)
    lines: list[str] = []
    if hidden > 0:
        lines.append(f"… 更早 {hidden} 步")
    for st in shown:
        row = f"{_GLYPH.get(st.status, '•')} **{st.name}**"
        if st.summary:
            # keep the summary inside an inline code span; neutralise backticks so it
            # can't break out and corrupt the markdown.
            row += f"  `{st.summary.replace('`', 'ˋ')}`"
        lines.append(row)
    return "\n".join(lines)


def render_card(state: StreamState, *, final: bool) -> dict:
    """Build the Feishu interactive-card dict for a StreamState snapshot. No header; a
    "Thinking…" footer note rides the running card and is dropped once ``final``."""
    error = bool(state.is_error)
    elements: list[dict] = []

    # --- body text ---
    if final and error:
        # Keep the error prefix intact at the HEAD and clip only the (possibly long)
        # streamed text tail — otherwise a long output would truncate the error away.
        et = _one_line_head(state.error_text)
        txt = state.text
        if et and txt:
            body = f"⚠️ {et}\n\n{_clip_body(txt)}"
        elif et:
            body = f"⚠️ {et}"
        else:
            body = _clip_body(txt) or "⚠️ 运行出错"
        elements.append({"tag": "markdown", "element_id": "md_text", "content": body})
    elif final:
        elements.append({"tag": "markdown", "element_id": "md_text",
                         "content": _clip_body(state.text) or "(无输出)"})
    elif state.text:                       # running WITH text
        elements.append({"tag": "markdown", "element_id": "md_text",
                         "content": _clip_body(state.text)})
    # else: running, no text yet → no body element; the "Thinking…" footer carries it.

    # --- tool steps ---
    if state.steps:
        n = len(state.steps)
        header_title = f"步骤 ({n}) · 全部完成" if (final and not error) else f"步骤 ({n})"
        elements.append({
            "tag": "collapsible_panel",
            "element_id": "steps_panel",
            "expanded": (not final) or error,          # D3: collapse only on clean finish
            "header": {"title": {"tag": "markdown", "content": header_title}},
            "elements": [{"tag": "markdown", "content": _steps_md(state.steps)}],
        })

    # --- footer: only while running, only "Thinking…" ---
    if not final:
        elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": _THINKING}]})

    return {
        "schema": "2.0",
        "config": {"update_multi": True, "streaming_mode": False},
        "body": {"elements": elements},
    }
